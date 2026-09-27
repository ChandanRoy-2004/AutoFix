import hashlib
import hmac
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
import pytest

from app.api.github_routes import extract_failing_file, process_pr_healing, verify_signature
from app.core.config import settings
from app.models.schemas import HealResponse
from app.main import app

client = TestClient(app)


def test_health_check():
    """Verify headless health check endpoint returns 200 OK with status healthy and service autofix-bot."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "service": "autofix-bot"}


def test_verify_signature_dev_mode():
    with patch.object(settings, "GITHUB_WEBHOOK_SECRET", ""):
        assert verify_signature(b'{"test": 1}', None) is True
        assert verify_signature(b'{"test": 1}', "sha256=invalid") is True


def test_verify_signature_missing_header():
    with patch.object(settings, "GITHUB_WEBHOOK_SECRET", "secret123"):
        assert verify_signature(b'{"test": 1}', None) is False


def test_verify_signature_valid_and_invalid():
    secret = "mysecret"
    body = b'{"hello": "world"}'
    valid_sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    invalid_sig = "sha256=wrong"

    with patch.object(settings, "GITHUB_WEBHOOK_SECRET", secret):
        assert verify_signature(body, valid_sig) is True
        assert verify_signature(body, invalid_sig) is False


def test_webhook_invalid_signature():
    secret = "mysecret"
    body = json.dumps({"action": "opened"}).encode()
    with patch.object(settings, "GITHUB_WEBHOOK_SECRET", secret):
        response = client.post(
            "/api/github/webhook",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": "sha256=bad",
            },
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid signature"


def test_webhook_pull_request_opened():
    secret = "mysecret"
    payload = {
        "action": "opened",
        "number": 10,
        "repository": {"full_name": "owner/repo", "clone_url": "https://github.com/owner/repo.git"},
        "pull_request": {"number": 10, "head": {"ref": "feature-1"}},
        "installation": {"id": 12345},
    }
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    with patch.object(settings, "GITHUB_WEBHOOK_SECRET", secret):
        response = client.post(
            "/api/github/webhook",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": sig,
            },
        )
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert data["event"] == "pull_request"


def test_webhook_workflow_run_failure():
    secret = "mysecret"
    payload = {
        "action": "completed",
        "workflow_run": {
            "conclusion": "failure",
            "head_branch": "patch-1",
            "pull_requests": [{"number": 15}],
        },
        "repository": {"full_name": "owner/repo"},
        "installation": {"id": 54321},
    }
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    with patch.object(settings, "GITHUB_WEBHOOK_SECRET", secret):
        response = client.post(
            "/api/github/webhook",
            content=body,
            headers={
                "X-GitHub-Event": "workflow_run",
                "X-Hub-Signature-256": sig,
            },
        )
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert data["event"] == "workflow_run"


def test_webhook_invalid_json():
    secret = "mysecret"
    body = b"not-a-valid-json"
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    with patch.object(settings, "GITHUB_WEBHOOK_SECRET", secret):
        response = client.post(
            "/api/github/webhook",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": sig,
            },
        )
        assert response.status_code == 400


@pytest.mark.anyio
async def test_process_pr_healing_success(tmp_path: Path):
    """Test process_pr_healing worker executes full flow on success and cleans up."""
    mock_response = HealResponse(
        success=True,
        iterations_used=1,
        final_code="code",
        generated_tests="",
        language="python",
        patches=[],
        logs=[],
    )

    with patch("app.api.github_routes.GitHubService") as mock_gh_cls, \
         patch("app.api.github_routes.subprocess.run") as mock_subproc_run, \
         patch("app.api.github_routes.run_repo_healing_pipeline", new_callable=AsyncMock) as mock_run_healing:

        mock_gh = MagicMock()
        mock_gh.get_installation_access_token = AsyncMock(return_value="test_token_123")
        mock_gh.clone_repo = MagicMock(return_value=True)
        mock_gh.clone_repository = AsyncMock(return_value=True)
        mock_gh.commit_and_push_patch = MagicMock(return_value=True)
        mock_gh.format_pr_comment = MagicMock(return_value="## PR Comment Report")
        mock_gh.post_pr_comment = AsyncMock(return_value=True)
        mock_gh_cls.return_value = mock_gh

        mock_subproc = MagicMock()
        mock_subproc.returncode = 1
        mock_subproc.stdout = "FAILED tests/test_payload_validator.py - AssertionError"
        mock_subproc.stderr = "traceback error"
        mock_subproc_run.return_value = mock_subproc

        mock_run_healing.return_value = mock_response

        await process_pr_healing(
            repo_full_name="org/test-repo",
            branch="feature/order-processor",
            pr_number=88,
            installation_id=456,
        )

        mock_gh.get_installation_access_token.assert_awaited_once_with(456)
        mock_gh.clone_repository.assert_awaited_once_with(
            repo_full_name="org/test-repo",
            branch="feature/order-processor",
            destination=Path("workspace/pr_88"),
            installation_id=456,
        )
        mock_run_healing.assert_awaited_once_with(
            repo_dir=Path("workspace/pr_88").resolve(),
            language="python",
            failing_file="app/utils/payload_validator.py",
            failing_logs="traceback error\nFAILED tests/test_payload_validator.py - AssertionError",
        )
        mock_gh.commit_and_push_patch.assert_called_once_with(
            Path("workspace/pr_88"),
            "feature/order-processor",
            "fix(autofix): resolve edge case in payload_validator",
            "test_token_123",
        )
        mock_gh.post_pr_comment.assert_awaited_once_with(
            "org/test-repo",
            88,
            "## PR Comment Report",
            "test_token_123",
        )
        assert not Path("workspace/pr_88").exists()


@pytest.mark.anyio
async def test_process_pr_healing_already_healthy():
    """Test process_pr_healing exits early when pre-flight test run passes."""
    with patch("app.api.github_routes.GitHubService") as mock_gh_cls, \
         patch("app.api.github_routes.subprocess.run") as mock_subproc_run, \
         patch("app.api.github_routes.run_repo_healing_pipeline", new_callable=AsyncMock) as mock_run_healing:

        mock_gh = MagicMock()
        mock_gh.get_installation_access_token = AsyncMock(return_value="test_token_123")
        mock_gh.clone_repo = MagicMock(return_value=True)
        mock_gh.clone_repository = AsyncMock(return_value=True)
        mock_gh_cls.return_value = mock_gh

        mock_subproc = MagicMock()
        mock_subproc.returncode = 0
        mock_subproc.stdout = "All tests passed!"
        mock_subproc.stderr = ""
        mock_subproc_run.return_value = mock_subproc

        await process_pr_healing(
            repo_full_name="org/test-repo",
            branch="feature/order-processor",
            pr_number=87,
            installation_id=456,
        )

        mock_run_healing.assert_not_called()
        mock_gh.commit_and_push_patch.assert_not_called()
        mock_gh.post_pr_comment.assert_not_called()
        assert not Path("workspace/pr_87").exists()


def test_webhook_ignores_bot_sender():
    """Test webhook ignores events triggered by bot self-commit with 200 OK."""
    secret = "mysecret"
    payload = {
        "action": "synchronize",
        "sender": {"login": "autofix-bot[bot]"},
        "pull_request": {"number": 12, "head": {"ref": "fix-1"}},
        "repository": {"full_name": "owner/repo"},
    }
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    with patch.object(settings, "GITHUB_WEBHOOK_SECRET", secret):
        response = client.post(
            "/api/github/webhook",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": sig,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ignored"
        assert data["reason"] == "bot_event"


@pytest.mark.anyio
async def test_process_pr_healing_clone_failure():
    """Test process_pr_healing handles clone failure gracefully."""
    with patch("app.api.github_routes.GitHubService") as mock_gh_cls, \
         patch("app.api.github_routes.run_repo_healing_pipeline", new_callable=AsyncMock) as mock_run_healing:

        mock_gh = MagicMock()
        mock_gh.get_installation_access_token = AsyncMock(return_value="test_token_123")
        mock_gh.clone_repo = MagicMock(return_value=False)
        mock_gh.clone_repository = AsyncMock(return_value=False)
        mock_gh_cls.return_value = mock_gh

        await process_pr_healing(
            repo_full_name="org/test-repo",
            branch="feature/order-processor",
            pr_number=89,
            installation_id=456,
        )

        mock_run_healing.assert_not_called()
        mock_gh.commit_and_push_patch.assert_not_called()
        mock_gh.post_pr_comment.assert_not_called()
        assert not Path("workspace/pr_89").exists()


def test_extract_failing_file():
    """Verify extract_failing_file extracts source target from tracebacks or defaults to payload_validator.py."""
    # Traceback mentioning payload_validator.py
    tb_payload = """
Traceback (most recent call last):
  File "/repo/tests/test_payload_validator.py", line 29, in test_extract_repo_metadata
    meta = extract_repo_metadata(payload)
  File "/repo/app/utils/payload_validator.py", line 11, in extract_repo_metadata
    owner_login = repo_info["owner"]["login"]
TypeError: string indices must be integers, not 'str'
"""
    assert extract_failing_file(tb_payload) == "app/utils/payload_validator.py"

    # Traceback mentioning event_formatter.py
    tb_event = """
FAILED tests/test_event_formatter.py - AssertionError: formatting failed
"""
    assert extract_failing_file(tb_event) == "app/utils/event_formatter.py"

    # Arbitrary log fallback
    tb_generic = "Some random error traceback with no recognized source files"
    assert extract_failing_file(tb_generic) == "app/utils/payload_validator.py"
    assert extract_failing_file("") == "app/utils/payload_validator.py"


@pytest.mark.anyio
async def test_process_pr_healing_dynamic_discovery():
    """Verify process_pr_healing invokes pytest with dynamic testpaths and exclusion filter."""
    import sys

    mock_response = HealResponse(
        success=True,
        iterations_used=1,
        final_code="code",
        generated_tests="",
        language="python",
        patches=[],
        logs=[],
    )

    with patch("app.api.github_routes.GitHubService") as mock_gh_cls, \
         patch("app.api.github_routes.subprocess.run") as mock_subproc_run, \
         patch("app.api.github_routes.run_repo_healing_pipeline", new_callable=AsyncMock) as mock_run_healing:

        mock_gh = MagicMock()
        mock_gh.get_installation_access_token = AsyncMock(return_value="test_token_123")
        mock_gh.clone_repo = MagicMock(return_value=True)
        mock_gh.clone_repository = AsyncMock(return_value=True)
        mock_gh.commit_and_push_patch = MagicMock(return_value=True)
        mock_gh.format_pr_comment = MagicMock(return_value="## Report")
        mock_gh.post_pr_comment = AsyncMock(return_value=True)
        mock_gh_cls.return_value = mock_gh

        mock_subproc = MagicMock()
        mock_subproc.returncode = 1
        mock_subproc.stdout = "FAILED tests/test_payload_validator.py - TypeError"
        mock_subproc.stderr = "File \"app/utils/payload_validator.py\", line 11, in extract_repo_metadata"
        mock_subproc_run.return_value = mock_subproc

        mock_run_healing.return_value = mock_response

        await process_pr_healing(
            repo_full_name="org/test-repo",
            branch="feature/payload-validator-test",
            pr_number=90,
            installation_id=789,
        )

        mock_subproc_run.assert_called_once()
        called_cmd = mock_subproc_run.call_args[0][0]
        assert called_cmd == [
            sys.executable, "-m", "pytest",
            "tests",
            "-v",
        ]

        mock_run_healing.assert_awaited_once_with(
            repo_dir=Path("workspace/pr_90").resolve(),
            language="python",
            failing_file="app/utils/payload_validator.py",
            failing_logs="File \"app/utils/payload_validator.py\", line 11, in extract_repo_metadata\nFAILED tests/test_payload_validator.py - TypeError",
        )


@pytest.mark.anyio
async def test_process_pr_healing_targets_payload_validator_when_file_exists():
    """Verify pre-flight test command targets tests/test_payload_validator.py directly when present."""
    import sys

    mock_response = HealResponse(
        success=True,
        iterations_used=1,
        final_code="code",
        generated_tests="",
        language="python",
        patches=[],
        logs=[],
    )

    async def fake_clone(*args, **kwargs):
        dest = kwargs.get("destination") or kwargs.get("target_dir")
        if dest is None and len(args) >= 3:
            dest = args[2]
        target = Path(dest)
        (target / "tests").mkdir(parents=True, exist_ok=True)
        (target / "tests" / "test_payload_validator.py").write_text("def test_dummy(): pass", encoding="utf-8")
        return True

    with patch("app.api.github_routes.GitHubService") as mock_gh_cls, \
         patch("app.api.github_routes.subprocess.run") as mock_subproc_run, \
         patch("app.api.github_routes.run_repo_healing_pipeline", new_callable=AsyncMock) as mock_run_healing:

        mock_gh = MagicMock()
        mock_gh.get_installation_access_token = AsyncMock(return_value="test_token_123")
        mock_gh.clone_repo = MagicMock(side_effect=fake_clone)
        mock_gh.clone_repository = AsyncMock(side_effect=fake_clone)
        mock_gh.commit_and_push_patch = MagicMock(return_value=True)
        mock_gh.format_pr_comment = MagicMock(return_value="## Report")
        mock_gh.post_pr_comment = AsyncMock(return_value=True)
        mock_gh_cls.return_value = mock_gh

        mock_subproc = MagicMock()
        mock_subproc.returncode = 1
        mock_subproc.stdout = "FAILED tests/test_payload_validator.py - TypeError"
        mock_subproc.stderr = "Traceback error in payload_validator.py"
        mock_subproc_run.return_value = mock_subproc

        mock_run_healing.return_value = mock_response

        await process_pr_healing(
            repo_full_name="org/test-repo",
            branch="feature/payload-validator-test",
            pr_number=91,
            installation_id=789,
        )

        mock_subproc_run.assert_called_once()
        called_cmd = mock_subproc_run.call_args[0][0]
        assert called_cmd == [
            sys.executable, "-m", "pytest",
            "tests/test_payload_validator.py",
            "-v",
        ]

        mock_run_healing.assert_awaited_once_with(
            repo_dir=Path("workspace/pr_91").resolve(),
            language="python",
            failing_file="app/utils/payload_validator.py",
            failing_logs="Traceback error in payload_validator.py\nFAILED tests/test_payload_validator.py - TypeError",
        )




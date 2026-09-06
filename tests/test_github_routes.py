import hashlib
import hmac
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
import pytest

from app.api.github_routes import process_pr_healing, verify_signature
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
         patch("app.api.github_routes.get_sandbox") as mock_get_sandbox, \
         patch("app.api.github_routes.run_repo_healing_pipeline", new_callable=AsyncMock) as mock_run_healing:

        mock_gh = MagicMock()
        mock_gh.get_installation_access_token = AsyncMock(return_value="test_token_123")
        mock_gh.clone_repo = MagicMock(return_value=True)
        mock_gh.commit_and_push_patch = MagicMock(return_value=True)
        mock_gh.format_pr_comment = MagicMock(return_value="## PR Comment Report")
        mock_gh.post_pr_comment = AsyncMock(return_value=True)
        mock_gh_cls.return_value = mock_gh

        mock_sandbox = MagicMock()
        mock_sandbox.run_tests.return_value = (False, "IndexError: list index out of range")
        mock_get_sandbox.return_value = mock_sandbox

        mock_run_healing.return_value = mock_response

        await process_pr_healing(
            repo_full_name="org/test-repo",
            branch="feature/order-processor",
            pr_number=88,
            installation_id=456,
        )

        mock_gh.get_installation_access_token.assert_awaited_once_with(456)
        mock_gh.clone_repo.assert_called_once_with(
            repo_url="https://github.com/org/test-repo.git",
            branch="feature/order-processor",
            target_dir=Path("workspace/pr_88"),
            token="test_token_123",
        )
        mock_run_healing.assert_awaited_once_with(
            repo_dir=Path("workspace/pr_88").resolve(),
            language="python",
            failing_file="order_processor.py",
            failing_logs="IndexError: list index out of range",
        )
        mock_gh.commit_and_push_patch.assert_called_once_with(
            Path("workspace/pr_88"),
            "feature/order-processor",
            "fix(autofix): resolve edge case and discount bounds",
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
         patch("app.api.github_routes.get_sandbox") as mock_get_sandbox, \
         patch("app.api.github_routes.run_repo_healing_pipeline", new_callable=AsyncMock) as mock_run_healing:

        mock_gh = MagicMock()
        mock_gh.get_installation_access_token = AsyncMock(return_value="test_token_123")
        mock_gh.clone_repo = MagicMock(return_value=True)
        mock_gh_cls.return_value = mock_gh

        mock_sandbox = MagicMock()
        mock_sandbox.run_tests.return_value = (True, "All tests passed!")
        mock_get_sandbox.return_value = mock_sandbox

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


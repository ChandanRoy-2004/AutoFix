import hashlib
import hmac
import json
from pathlib import Path
import unittest.mock
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
import httpx
import pytest

from app.core.config import settings
from app.main import app
from app.services.github_service import GitHubService
from app.services.orchestrator import run_repo_healing_pipeline


def test_e2e_webhook_signature_and_event_dispatch():
    """Test webhook endpoint signature verification and pull_request event dispatch."""
    secret = settings.GITHUB_WEBHOOK_SECRET or "Pipelineautofix"
    payload = {
        "action": "opened",
        "number": 42,
        "repository": {
            "full_name": "owner/repo",
            "clone_url": "https://github.com/owner/repo.git",
        },
        "pull_request": {
            "number": 42,
            "head": {"ref": "fix/calc-bug"},
        },
        "installation": {"id": 999},
    }

    body = json.dumps(payload).encode("utf-8")
    signature = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    client = TestClient(app)

    with patch.object(settings, "GITHUB_WEBHOOK_SECRET", secret):
        response = client.post(
            "/api/github/webhook",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": signature,
            },
        )

        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert data["event"] == "pull_request"


@pytest.mark.anyio
async def test_e2e_full_pr_healing_and_comment_flow(tmp_path: Path):
    """End-to-end test of cloning PR repo, diagnosing bug, healing code, committing, and posting PR comment."""
    github_service = GitHubService()
    repo_dir = tmp_path / "test_repo"

    # Setup mock repo files that clone_repo will create
    def fake_clone(repo_url: str, branch: str, target_dir: Path, token: str = "") -> bool:
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        # Buggy calculator script
        (target / "calculator.py").write_text(
            "def add(a: int, b: int) -> int:\n    return a - b  # bug: subtraction\n",
            encoding="utf-8",
        )
        # Pytest test suite
        (target / "test_calculator.py").write_text(
            "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
            encoding="utf-8",
        )
        return True

    # 1. Mock GitHubService methods
    github_service.get_installation_access_token = AsyncMock(return_value="mock_token")
    github_service.clone_repo = MagicMock(side_effect=fake_clone)
    github_service.commit_and_push_patch = MagicMock(return_value=True)
    github_service.post_pr_comment = AsyncMock(return_value=True)

    # 2. Acquire installation token & clone repo
    token = await github_service.get_installation_access_token(installation_id=999)
    assert token == "mock_token"

    cloned = github_service.clone_repo(
        repo_url="https://github.com/owner/repo.git",
        branch="fix/calc-bug",
        target_dir=repo_dir,
        token=token,
    )
    assert cloned is True
    assert (repo_dir / "calculator.py").exists()
    assert (repo_dir / "test_calculator.py").exists()

    # 3. Run repo healing pipeline with LLM returning the corrected code
    healed_code = "```python\ndef add(a: int, b: int) -> int:\n    return a + b\n```"
    with patch("app.services.orchestrator.call_gemini", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = healed_code

        heal_response = await run_repo_healing_pipeline(
            repo_dir=repo_dir,
            language="python",
            failing_file="calculator.py",
            failing_logs="FAILED test_calculator.py::test_add - AssertionError: -1 != 5",
        )

        # Assert healing success
        assert heal_response.success is True
        assert heal_response.iterations_used == 1
        assert "return a + b" in heal_response.final_code
        assert len(heal_response.patches) == 1
        assert heal_response.patches[0].file_path == "calculator.py"
        assert heal_response.patches[0].original_content != heal_response.patches[0].patched_content

    # 4. Commit and push the patch
    push_success = github_service.commit_and_push_patch(
        repo_dir=repo_dir,
        branch="fix/calc-bug",
        commit_message="AutoFix: Autonomous patch for calculator.py",
        token=token,
    )
    assert push_success is True
    github_service.commit_and_push_patch.assert_called_once_with(
        repo_dir=repo_dir,
        branch="fix/calc-bug",
        commit_message="AutoFix: Autonomous patch for calculator.py",
        token="mock_token",
    )

    # 5. Format report and post PR comment
    comment_md = GitHubService.format_pr_comment(heal_response)
    comment_success = await github_service.post_pr_comment(
        repo_full_name="owner/repo",
        pr_number=42,
        comment_markdown=comment_md,
        token=token,
    )
    assert comment_success is True
    github_service.post_pr_comment.assert_called_once_with(
        repo_full_name="owner/repo",
        pr_number=42,
        comment_markdown=comment_md,
        token="mock_token",
    )

    # 6. Assertions on comment Markdown structure and contents
    assert "| Metric | Value |" in comment_md
    assert "| **Status** | ✅ Passed |" in comment_md
    assert "| **Iterations Used** | 1 |" in comment_md
    assert "calculator.py" in comment_md
    assert "return a + b" in comment_md
    assert "### 🧪 Test Logs" in comment_md
    assert "passed" in comment_md.lower()

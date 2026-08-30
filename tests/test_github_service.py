import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import pytest

from app.core.config import settings
from app.services.github_service import GitHubService


@pytest.fixture
def rsa_key_pair(tmp_path: Path):
    """Generate a temporary RSA key pair for testing JWT generation."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    pem_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_file = tmp_path / "test_private_key.pem"
    key_file.write_bytes(pem_bytes)

    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    return key_file, public_pem.decode("utf-8")


def test_generate_jwt_unconfigured():
    service = GitHubService()
    with patch.object(settings, "GITHUB_APP_ID", ""), patch.object(settings, "GITHUB_PRIVATE_KEY_PATH", ""):
        token = service.generate_jwt()
        assert token == ""


def test_generate_jwt_missing_file():
    service = GitHubService()
    with patch.object(settings, "GITHUB_APP_ID", "123456"), patch.object(settings, "GITHUB_PRIVATE_KEY_PATH", "/nonexistent/key.pem"):
        token = service.generate_jwt()
        assert token == ""


def test_generate_jwt_success(rsa_key_pair):
    key_file, public_pem = rsa_key_pair
    service = GitHubService()
    with patch.object(settings, "GITHUB_APP_ID", "987654"), patch.object(settings, "GITHUB_PRIVATE_KEY_PATH", str(key_file)):
        token = service.generate_jwt()
        assert token != ""

        # Decode and verify JWT
        decoded = jwt.decode(token, public_pem, algorithms=["RS256"])
        assert decoded["iss"] == "987654"
        assert "iat" in decoded
        assert "exp" in decoded
        assert decoded["exp"] - decoded["iat"] == 600


@pytest.mark.anyio
async def test_get_installation_access_token_success():
    service = GitHubService()
    with patch.object(service, "generate_jwt", return_value="mock.jwt.token"):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"token": "ghs_1234567890abcdef"}

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            token = await service.get_installation_access_token(12345)
            assert token == "ghs_1234567890abcdef"
            mock_post.assert_called_once()
            args, kwargs = mock_post.call_args
            assert "12345/access_tokens" in args[0]
            assert kwargs["headers"]["Authorization"] == "Bearer mock.jwt.token"


@pytest.mark.anyio
async def test_get_installation_access_token_failure():
    service = GitHubService()
    with patch.object(service, "generate_jwt", return_value="mock.jwt.token"):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            token = await service.get_installation_access_token(12345)
            assert token == ""


def test_clone_repo_success(tmp_path: Path):
    service = GitHubService()
    target_dir = tmp_path / "repo"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        success = service.clone_repo(
            repo_url="https://github.com/owner/test-repo.git",
            branch="main",
            target_dir=target_dir,
            token="ghs_mocktoken",
        )
        assert success is True
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "https://x-access-token:ghs_mocktoken@github.com/owner/test-repo.git" in cmd
        assert "-b" in cmd
        assert "main" in cmd


def test_commit_and_push_patch(tmp_path: Path):
    service = GitHubService()
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    with patch("subprocess.run") as mock_run:
        # Mock git status returning modified files, git get-url returning url, git push returning 0
        def side_effect(cmd, **kwargs):
            if "--porcelain" in cmd:
                return MagicMock(returncode=0, stdout=" M app.py\n")
            if "get-url" in cmd:
                return MagicMock(returncode=0, stdout="https://github.com/owner/test-repo.git\n")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect

        success = service.commit_and_push_patch(
            repo_dir=repo_dir,
            branch="fix-bug",
            commit_message="Fix bug in app.py",
            token="ghs_mocktoken",
        )
        assert success is True


@pytest.mark.anyio
async def test_post_pr_comment_success():
    service = GitHubService()
    mock_response = MagicMock()
    mock_response.status_code = 201

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
        success = await service.post_pr_comment(
            repo_full_name="owner/test-repo",
            pr_number=42,
            comment_markdown="AutoFix resolved all issues!",
            token="ghs_mocktoken",
        )
        assert success is True
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert "owner/test-repo/issues/42/comments" in args[0]
        assert kwargs["headers"]["Authorization"] == "token ghs_mocktoken"
        assert kwargs["json"]["body"] == "AutoFix resolved all issues!"


@pytest.mark.anyio
async def test_post_pr_comment_failure():
    service = GitHubService()
    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.text = "Forbidden"

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        success = await service.post_pr_comment(
            repo_full_name="owner/test-repo",
            pr_number=42,
            comment_markdown="Error",
            token="ghs_mocktoken",
        )
        assert success is False

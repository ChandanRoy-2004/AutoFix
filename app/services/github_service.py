import logging
import os
from pathlib import Path
import subprocess
import time

import httpx
import jwt

from app.core.config import BASE_DIR, settings

logger = logging.getLogger(__name__)


class GitHubService:
    """Service for interacting with GitHub App API, cloning repositories, committing patches, and commenting on PRs."""

    def __init__(self):
        self.last_clone_returncode: int | None = None
        self.last_clone_stderr: str = ""

    def generate_jwt(self) -> str:
        """Generate and sign a RS256 JWT valid for 10 minutes for GitHub App authentication."""
        if not settings.GITHUB_APP_ID or not settings.GITHUB_PRIVATE_KEY_PATH:
            logger.warning("GitHub App ID or private key path is not configured.")
            return ""

        key_path = Path(settings.GITHUB_PRIVATE_KEY_PATH)
        if not key_path.is_absolute():
            # Check relative to current working directory and BASE_DIR
            potential_paths = [key_path, (BASE_DIR / key_path).resolve()]
            found = next((p for p in potential_paths if p.exists() and p.is_file()), None)
            if not found:
                logger.warning("GitHub App private key file not found at %s", key_path)
                return ""
            key_path = found
        elif not key_path.exists() or not key_path.is_file():
            logger.warning("GitHub App private key file not found at %s", key_path)
            return ""

        try:
            private_key = key_path.read_text(encoding="utf-8")
            now = int(time.time())
            payload = {
                "iat": now,
                "exp": now + 600,
                "iss": str(settings.GITHUB_APP_ID),
            }
            encoded_jwt = jwt.encode(payload, private_key, algorithm="RS256")
            if isinstance(encoded_jwt, bytes):
                encoded_jwt = encoded_jwt.decode("utf-8")
            return encoded_jwt
        except Exception as e:
            logger.error("Failed to generate GitHub App JWT: %s", e)
            return ""

    async def get_installation_access_token(self, installation_id: int) -> str:
        """Fetch an installation access token using GitHub App JWT authentication."""
        jwt_token = self.generate_jwt()
        if not jwt_token:
            logger.error("Cannot fetch installation token: JWT is empty or unconfigured.")
            return ""

        url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers)
                if response.status_code == 201:
                    data = response.json()
                    return data.get("token", "")
                else:
                    logger.error(
                        "Failed to get installation access token (%d): %s",
                        response.status_code,
                        response.text,
                    )
                    return ""
        except Exception as e:
            logger.error("Error requesting installation access token: %s", e)
            return ""

    def clone_repo(self, repo_url: str, branch: str, target_dir: Path, token: str = "") -> bool:
        """Clone a remote GitHub repository branch using an optional access token."""
        target_path = Path(target_dir).resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)

        auth_url = repo_url
        if token:
            if repo_url.startswith("https://"):
                auth_url = f"https://x-access-token:{token}@{repo_url[8:]}"
            elif repo_url.startswith("http://"):
                auth_url = f"https://x-access-token:{token}@{repo_url[7:]}"
            else:
                auth_url = f"https://x-access-token:{token}@{repo_url}"

        cmd = ["git", "clone", "-b", branch, "--depth", "1", auth_url, str(target_path)]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
            )
            self.last_clone_returncode = result.returncode
            self.last_clone_stderr = result.stderr
            if result.returncode == 0:
                logger.info("Successfully cloned %s (branch: %s) to %s", repo_url, branch, target_path)
                return True
            else:
                logger.error("Failed to clone repository %s (exit code %s): %s", repo_url, result.returncode, result.stderr)
                return False
        except Exception as e:
            self.last_clone_returncode = -1
            self.last_clone_stderr = str(e)
            logger.error("Exception occurred while cloning repository: %s", e)
            return False

    def commit_and_push_patch(
        self,
        repo_dir: Path,
        branch: str,
        commit_message: str,
        token: str = "",
    ) -> bool:
        """Commit changes to the local repository and push them to the remote branch."""
        repo_path = Path(repo_dir).resolve()
        if not repo_path.exists() or not repo_path.is_dir():
            logger.error("Repository directory does not exist: %s", repo_path)
            return False

        try:
            # Configure git user identity
            subprocess.run(["git", "config", "user.name", "AutoFix Bot"], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "autofix-bot@users.noreply.github.com"], cwd=repo_path, check=True, capture_output=True)

            # Stage changes
            subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)

            # Check if there are changes to commit
            status_res = subprocess.run(["git", "status", "--porcelain"], cwd=repo_path, capture_output=True, text=True, check=True)
            if not status_res.stdout.strip():
                logger.info("No modifications detected to commit in %s", repo_path)
                return True

            # Commit
            subprocess.run(["git", "commit", "-m", commit_message], cwd=repo_path, check=True, capture_output=True)

            # Configure authenticated remote if token is provided
            if token:
                remote_res = subprocess.run(["git", "remote", "get-url", "origin"], cwd=repo_path, capture_output=True, text=True)
                if remote_res.returncode == 0:
                    remote_url = remote_res.stdout.strip()
                    if "@" in remote_url and "://" in remote_url:
                        proto, rest = remote_url.split("://", 1)
                        rest_clean = rest.split("@", 1)[1]
                        auth_remote = f"{proto}://x-access-token:{token}@{rest_clean}"
                    elif remote_url.startswith("https://"):
                        auth_remote = f"https://x-access-token:{token}@{remote_url[8:]}"
                    elif remote_url.startswith("http://"):
                        auth_remote = f"https://x-access-token:{token}@{remote_url[7:]}"
                    else:
                        auth_remote = f"https://x-access-token:{token}@{remote_url}"

                    subprocess.run(["git", "remote", "set-url", "origin", auth_remote], cwd=repo_path, check=True, capture_output=True)

            # Push to branch
            push_res = subprocess.run(["git", "push", "origin", branch], cwd=repo_path, capture_output=True, text=True, check=False)
            if push_res.returncode == 0:
                logger.info("Successfully pushed patch to branch %s", branch)
                return True
            else:
                logger.error("Failed to push patch to branch %s: %s", branch, push_res.stderr)
                return False
        except Exception as e:
            logger.error("Exception during commit_and_push_patch: %s", e)
            return False

    async def post_pr_comment(
        self,
        repo_full_name: str,
        pr_number: int,
        comment_markdown: str,
        token: str,
    ) -> bool:
        """Post a comment on a GitHub pull request / issue."""
        url = f"https://api.github.com/repos/{repo_full_name}/issues/{pr_number}/comments"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        payload = {"body": comment_markdown}

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers, json=payload)
                if response.status_code == 201:
                    logger.info("Successfully posted comment to %s#%d", repo_full_name, pr_number)
                    return True
                else:
                    logger.error("Failed to post PR comment (%d): %s", response.status_code, response.text)
                    return False
        except Exception as e:
            logger.error("Error posting PR comment: %s", e)
            return False

    @staticmethod
    def format_pr_comment(heal_response) -> str:
        """Format a GitHub Pull Request comment detailing the healing results in a Markdown table."""
        status_icon = "✅ Passed" if heal_response.success else "❌ Failed"
        patched_files_str = (
            ", ".join([p.file_path for p in heal_response.patches])
            if heal_response.patches
            else "None"
        )

        passed_test_logs = ""
        for log in reversed(heal_response.logs):
            if log.step_name in ["TESTS_PASSED", "REPO_TEST_RUN", "REPO_PIPELINE_COMPLETE"]:
                passed_test_logs = log.message
                break
        if not passed_test_logs and heal_response.logs:
            passed_test_logs = heal_response.logs[-1].message

        diff_section = ""
        if heal_response.patches:
            diff_section = "\n### 📝 Code Diffs & Patches\n"
            for patch in heal_response.patches:
                diff_section += f"**`{patch.file_path}`**\n```\n{patch.patched_content}\n```\n"

        comment = f"""## 🤖 AutoFix Autonomous Healer Report

| Metric | Value |
| :--- | :--- |
| **Status** | {status_icon} |
| **Iterations Used** | {heal_response.iterations_used} |
| **Language** | {heal_response.language.capitalize() if getattr(heal_response, 'language', None) else "Python"} |
| **Patched Files** | {patched_files_str} |

{diff_section}
### 🧪 Test Logs
```text
{passed_test_logs}
```
"""
        return comment.strip()


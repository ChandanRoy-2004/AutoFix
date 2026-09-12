import logging
import os
from pathlib import Path
import subprocess
import time
from typing import Any

import httpx
import jwt

from app.core.config import BASE_DIR, settings

logger = logging.getLogger(__name__)


class GitHubService:
    """Service for interacting with GitHub App API, cloning repositories, committing patches, and commenting on PRs."""

    def __init__(self):
        self.last_clone_returncode: int | None = None
        self.last_clone_stderr: str = ""

    def _generate_jwt(self) -> str:
        """Generate and sign a RS256 JWT valid for 10 minutes for GitHub App authentication."""
        key_text: str | None = settings.GITHUB_PRIVATE_KEY_CONTENT or os.environ.get("GITHUB_PRIVATE_KEY_CONTENT")

        if not key_text and settings.GITHUB_PRIVATE_KEY_PATH:
            key_path = Path(settings.GITHUB_PRIVATE_KEY_PATH)
            if not key_path.is_absolute():
                # Check relative to current working directory and BASE_DIR
                potential_paths = [key_path, (BASE_DIR / key_path).resolve()]
                found = next((p for p in potential_paths if p.exists() and p.is_file()), None)
                if found:
                    key_text = found.read_text(encoding="utf-8")
            elif key_path.exists() and key_path.is_file():
                key_text = key_path.read_text(encoding="utf-8")

        if not key_text:
            raise ValueError("No GitHub App private key found in environment or file path.")

        key_text = key_text.replace("\\n", "\n")

        if not settings.GITHUB_APP_ID:
            logger.warning("GitHub App ID is not configured.")
            return ""

        now = int(time.time())
        payload = {
            "iat": now,
            "exp": now + 600,
            "iss": str(settings.GITHUB_APP_ID),
        }
        encoded_jwt = jwt.encode(payload, key_text, algorithm="RS256")
        if isinstance(encoded_jwt, bytes):
            encoded_jwt = encoded_jwt.decode("utf-8")
        return encoded_jwt

    def generate_jwt(self) -> str:
        """Generate and sign a RS256 JWT valid for 10 minutes for GitHub App authentication."""
        try:
            return self._generate_jwt()
        except ValueError as e:
            logger.warning("GitHub App private key error: %s", e)
            return ""
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

    async def clone_repository(
        self,
        repo_full_name: str,
        branch: str,
        destination: Path,
        installation_id: int | None = None,
        token: str = "",
    ) -> bool:
        """Clone a remote repository fresh using an optional token or installation ID."""
        auth_token = token
        if not auth_token and installation_id:
            auth_token = await self.get_installation_access_token(installation_id)
        repo_url = f"https://github.com/{repo_full_name}.git" if not repo_full_name.startswith("http") else repo_full_name
        return self.clone_repo(
            repo_url=repo_url,
            branch=branch,
            target_dir=destination,
            token=auth_token,
        )

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
        comment_markdown: Any = None,
        token: str = "",
        *,
        comment_or_metadata: Any = None,
    ) -> bool:
        """Post a comment on a GitHub pull request / issue.
        
        Accepts either a rendered Markdown string or a structured metadata object / dict.
        """
        payload_input = comment_or_metadata if comment_or_metadata is not None else comment_markdown
        if isinstance(payload_input, (dict, object)) and not isinstance(payload_input, str):
            meta = (
                payload_input
                if isinstance(payload_input, dict)
                else (
                    payload_input.model_dump()
                    if hasattr(payload_input, "model_dump")
                    else getattr(payload_input, "__dict__", {})
                )
            )
            raw_failure_logs = meta.get("failure_logs") or ""
            if len(raw_failure_logs) > 2000:
                raw_failure_logs = truncate_failure_logs(raw_failure_logs, max_chars=2000)

            rendered_body = build_healing_audit_report(
                heal_response=payload_input if not isinstance(payload_input, dict) else None,
                status="Verified Passing ✅" if meta.get("success") else "Failed ❌",
                iteration=meta.get("iterations") or meta.get("iterations_used", 1),
                max_iterations=meta.get("max_iterations", 3),
                failing_file=meta.get("failing_file", "target.py"),
                model_name=meta.get("model_used") or meta.get("model_name", "gemini-3.6-flash"),
                ast_dependencies=meta.get("ast_dependencies", []),
                test_summary=meta.get("test_summary"),
                test_target=meta.get("test_target"),
                patched_code=meta.get("patched_code") or meta.get("final_code", ""),
                language=meta.get("language", "python"),
                truncated_failure_logs=raw_failure_logs,
            )
        else:
            rendered_body = str(payload_input if payload_input is not None else "")

        url = f"https://api.github.com/repos/{repo_full_name}/issues/{pr_number}/comments"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        payload = {"body": rendered_body}

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
    def build_healing_audit_report(heal_response=None, **kwargs) -> str:
        """Create a structured Markdown report builder for autonomous healing."""
        return build_healing_audit_report(heal_response, **kwargs)

    @staticmethod
    def format_pr_comment(heal_response) -> str:
        """Format a GitHub Pull Request comment detailing the healing results in a Markdown report."""
        if heal_response is not None and getattr(heal_response, "audit_report", None):
            return heal_response.audit_report
        return build_healing_audit_report(heal_response)


def truncate_failure_logs(logs: str, max_lines: int = 30, max_chars: int = 2000) -> str:
    """Truncate failure logs to ensure PR comments stay within size limits while preserving stack traces."""
    if not logs:
        return ""
    text = logs.strip()
    lines = text.splitlines()
    if len(lines) > max_lines:
        text = "\n".join(lines[-max_lines:])
    if len(text) > max_chars:
        text = text[-max_chars:]
    return text.strip()


def build_healing_audit_report(
    heal_response=None,
    *,
    status: str | None = None,
    iteration: int | None = None,
    max_iterations: int | None = None,
    failing_file: str | None = None,
    model_name: str | None = None,
    ast_dependencies: list[str] | dict | None = None,
    formatted_ast_tree: str | None = None,
    test_target: str | None = None,
    test_rows: str | None = None,
    test_summary: dict | None = None,
    patched_code: str | None = None,
    language: str | None = None,
    failure_logs: str | None = None,
    truncated_failure_logs: str | None = None,
) -> str:
    """Build a structured Markdown PR audit report for autonomous code self-healing."""
    # 1. Resolve status
    if status is None:
        if heal_response is not None:
            heal_success = getattr(heal_response, "success", None)
            if heal_success is None and isinstance(heal_response, dict):
                heal_success = heal_response.get("success")
            status = "Verified Passing ✅" if heal_success else "Failed ❌"
        else:
            status = "Verified Passing ✅"

    # 2. Resolve iterations
    if iteration is None:
        if heal_response is not None:
            iteration = (
                getattr(heal_response, "iterations", None)
                or getattr(heal_response, "iterations_used", None)
                or (heal_response.get("iterations") or heal_response.get("iterations_used") if isinstance(heal_response, dict) else None)
            )
        if iteration is None:
            iteration = 1

    if max_iterations is None:
        if heal_response is not None:
            max_iterations = (
                getattr(heal_response, "max_iterations", None)
                or (heal_response.get("max_iterations") if isinstance(heal_response, dict) else None)
            )
        if max_iterations is None:
            max_iterations = getattr(settings, "MAX_HEALING_ITERATIONS", 3)

    # 3. Resolve target module
    if failing_file is None:
        if heal_response is not None:
            failing_file = getattr(heal_response, "failing_file", None) or (heal_response.get("failing_file") if isinstance(heal_response, dict) else None)
            if not failing_file:
                patches = getattr(heal_response, "patches", None) or (heal_response.get("patches") if isinstance(heal_response, dict) else None)
                if patches and len(patches) > 0:
                    first_p = patches[0]
                    failing_file = getattr(first_p, "file_path", None) or (first_p.get("file_path") if isinstance(first_p, dict) else None)
        if not failing_file:
            failing_file = "target.py"

    # 4. Resolve LLM model engine
    if model_name is None:
        if heal_response is not None:
            model_name = (
                getattr(heal_response, "model_used", None)
                or getattr(heal_response, "model_name", None)
                or (heal_response.get("model_used") or heal_response.get("model_name") if isinstance(heal_response, dict) else None)
            )
        if not model_name:
            model_name = getattr(settings, "PRIMARY_MODEL", "gemini-3.6-flash")

    # 5. Resolve AST Dependency Scope
    if formatted_ast_tree is None:
        deps = ast_dependencies
        if deps is None and heal_response is not None:
            deps = getattr(heal_response, "ast_dependencies", None) or (heal_response.get("ast_dependencies") if isinstance(heal_response, dict) else None)
        if isinstance(deps, dict):
            deps = list(deps.keys())
        elif deps is None:
            deps = []

        tree_lines = []
        seen = set()
        if failing_file:
            tree_lines.append(f"- `{failing_file}`")
            seen.add(failing_file)
        for dep in deps:
            if dep not in seen:
                tree_lines.append(f"- `{dep}`")
                seen.add(dep)
        formatted_ast_tree = "\n".join(tree_lines) if tree_lines else f"- `{failing_file}`"

    # 6. Resolve Test Verification Suite
    if test_rows is None:
        ts = test_summary
        if ts is None and heal_response is not None:
            ts = getattr(heal_response, "test_summary", None) or (heal_response.get("test_summary") if isinstance(heal_response, dict) else None)

        if ts and isinstance(ts, dict):
            rows = []
            for t_name, t_stat in ts.items():
                if isinstance(t_stat, dict):
                    init_st = t_stat.get("initial") or t_stat.get("initial_run") or "Failed ❌"
                    fin_st = t_stat.get("final") or t_stat.get("sandbox_verification") or ("Passed ✅" if ("Passing" in status or "Passed" in status or "✅" in status) else "Failed ❌")
                elif isinstance(t_stat, (list, tuple)) and len(t_stat) >= 2:
                    init_st, fin_st = str(t_stat[0]), str(t_stat[1])
                else:
                    init_st = "Failed ❌"
                    fin_st = str(t_stat)
                rows.append(f"| `{t_name}` | {init_st} | {fin_st} |")
            test_rows = "\n".join(rows)
        else:
            target = test_target
            if target is None and heal_response is not None:
                target = getattr(heal_response, "test_target", None) or (heal_response.get("test_target") if isinstance(heal_response, dict) else None)
            if not target:
                target = f"tests/test_{Path(failing_file).name}" if not Path(failing_file).name.startswith("test_") else failing_file

            is_passed = (
                "Passing" in status or "Passed" in status or "✅" in status
                if status is not None
                else (getattr(heal_response, "success", True) if heal_response is not None else True)
            )
            sandbox_status = "Passed ✅" if is_passed else "Failed ❌"
            test_rows = f"| `{target}` | Failed ❌ | {sandbox_status} |"

    # 7. Resolve Language
    if language is None:
        if heal_response is not None:
            language = getattr(heal_response, "language", None) or (heal_response.get("language") if isinstance(heal_response, dict) else None)
        if not language:
            language = "python"

    # 8. Resolve Applied Code Patch
    if patched_code is None:
        if heal_response is not None:
            if getattr(heal_response, "patched_code", None):
                patched_code = getattr(heal_response, "patched_code")
            elif isinstance(heal_response, dict) and heal_response.get("patched_code"):
                patched_code = heal_response["patched_code"]
            else:
                patches = getattr(heal_response, "patches", None) or (heal_response.get("patches") if isinstance(heal_response, dict) else None)
                if patches and len(patches) > 0:
                    first_p = patches[0]
                    patched_code = getattr(first_p, "patched_content", None) or (first_p.get("patched_content") if isinstance(first_p, dict) else "")
                elif getattr(heal_response, "final_code", None):
                    patched_code = getattr(heal_response, "final_code")
                elif isinstance(heal_response, dict) and heal_response.get("final_code"):
                    patched_code = heal_response["final_code"]
                else:
                    patched_code = ""
        else:
            patched_code = ""

    # 9. Resolve Failure Logs
    if truncated_failure_logs is None:
        raw_fail = failure_logs
        if raw_fail is None and heal_response is not None:
            raw_fail = getattr(heal_response, "failure_logs", None) or (heal_response.get("failure_logs") if isinstance(heal_response, dict) else None)
            if not raw_fail and getattr(heal_response, "logs", None):
                for log in heal_response.logs:
                    if "FAIL" in getattr(log, "step_name", ""):
                        raw_fail = log.message
                        break
        truncated_failure_logs = truncate_failure_logs(raw_fail, max_chars=2000) if raw_fail else ""
    elif truncated_failure_logs:
        truncated_failure_logs = truncate_failure_logs(truncated_failure_logs, max_chars=2000)

    # Clean any surrounding markdown fences if present
    code_text = patched_code.strip()
    if code_text.startswith("```"):
        lines = code_text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        code_text = "\n".join(lines).strip()

    failure_section = ""
    if truncated_failure_logs:
        failure_section = f"""

---

<details>
<summary><b>View Initial Failure Logs & Traceback</b></summary>

```text
{truncated_failure_logs}
```
</details>"""

    report = f"""## 🤖 AutoFix Autonomous Healing Report

| Metric | Status |
| :--- | :--- |
| **Status** | {status} |
| **Iterations Required** | `{iteration} / {max_iterations}` |
| **Target Module** | `{failing_file}` |
| **LLM Engine** | `{model_name}` |

---

### 🔍 AST Dependency Scope
The following interdependent modules were extracted and analyzed to preserve system contracts:
{formatted_ast_tree}

---

### 🧪 Test Verification Suite
| Test Target | Initial Run | Sandbox Verification |
| :--- | :---: | :---: |
{test_rows}

---

<details>
<summary><b>View Applied Code Patch</b></summary>

```{language}
{code_text}
```
</details>{failure_section}"""

    return report.strip()



import shutil
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import traceback

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.services.github_service import GitHubService
from app.services.orchestrator import run_repo_healing_pipeline
from app.services.sandboxes import get_sandbox

logger = logging.getLogger(__name__)

router = APIRouter()


def verify_signature(payload_body: bytes, signature_header: str | None) -> bool:
    """Verify GitHub webhook payload signature against GITHUB_WEBHOOK_SECRET."""
    if not settings.GITHUB_WEBHOOK_SECRET:
        # Dev mode bypass if webhook secret is not configured
        return True

    if not signature_header:
        return False

    expected_signature = "sha256=" + hmac.new(
        settings.GITHUB_WEBHOOK_SECRET.encode("utf-8"),
        payload_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected_signature, signature_header)


def extract_failing_file(logs: str, repo_dir: Path | None = None) -> str:
    """Extract failing source file from test traceback or fallback to app/utils/payload_validator.py."""
    if not logs:
        return "app/utils/payload_validator.py"

    # 1. Match File ".../path.py", line \d+ or path.py:\d+:
    file_patterns = [
        r'File\s+"([^"]+\.py)"',
        r'([\w/\.\-]+\.py):\d+:',
    ]
    candidates = []
    for pat in file_patterns:
        for match in re.finditer(pat, logs):
            filepath = match.group(1)
            # Exclude virtualenv, site-packages, and test files
            if ".venv" in filepath or "site-packages" in filepath:
                continue
            if "/test_" in filepath or filepath.startswith("test_") or "tests/" in filepath:
                continue
            candidates.append(filepath)

    repo_path = Path(repo_dir).resolve() if repo_dir else None

    # Check candidates against repository filesystem if available
    if repo_path and repo_path.exists():
        for cand in reversed(candidates):
            try:
                cp = Path(cand)
                if not cp.is_absolute():
                    cp = (repo_path / cp).resolve()
                if cp.exists() and cp.is_file():
                    return cp.relative_to(repo_path).as_posix()
            except Exception:
                continue

    # If candidate is a relative path starting with app/ or src/
    for cand in reversed(candidates):
        norm = cand.replace("\\", "/")
        if "app/" in norm:
            idx = norm.find("app/")
            return norm[idx:]
        if "src/" in norm:
            idx = norm.find("src/")
            return norm[idx:]

    # 2. Check if specific test patterns or modules are referenced in the failure logs
    test_match = re.search(r'(?:FAILED|ERROR)\s+(?:[\w/\.\-]+/)?test_([\w_]+)\.py', logs)
    if test_match:
        module_name = test_match.group(1)
        if repo_path and repo_path.exists():
            matches = list(repo_path.rglob(f"{module_name}.py"))
            for m in matches:
                if not m.name.startswith("test_") and "tests" not in m.parts:
                    return m.relative_to(repo_path).as_posix()
        # Common known mapping or fallback
        if module_name == "event_formatter":
            return "app/utils/event_formatter.py"
        if module_name == "payload_validator":
            return "app/utils/payload_validator.py"

    if "event_formatter" in logs:
        return "app/utils/event_formatter.py"

    # 3. Default fallback
    return "app/utils/payload_validator.py"


async def process_pr_healing(
    repo_full_name: str,
    branch: str,
    pr_number: int,
    installation_id: int,
):
    """Background task to clone PR repository, run autonomous healing pipeline, push patch, and post PR report."""
    temp_dir = Path(f"workspace/pr_{pr_number}")
    try:
        msg = f"[GITHUB_WEBHOOK] Starting PR healing for {repo_full_name} PR #{pr_number} on branch {branch}..."
        print(msg, flush=True)
        logger.info(msg)

        github_service = GitHubService()

        print(f"[GITHUB_BOT] 1. Requesting token for installation {installation_id}...", flush=True)
        token = ""
        if installation_id:
            token = await github_service.get_installation_access_token(installation_id)
        if not token:
            err_msg = f"[GITHUB_BOT] Token is None or empty for installation {installation_id}."
            print(err_msg, flush=True)
            logger.error(err_msg)

        # 1. Clean workspace directory if it exists to avoid stale cache
        if temp_dir.exists():
            shutil.rmtree(temp_dir)

        # 2. Clone repo fresh
        clone_success = await github_service.clone_repository(
            repo_full_name=repo_full_name,
            branch=branch,
            destination=temp_dir,
            installation_id=installation_id,
        )
        if not clone_success:
            logger.error(f"[GITHUB_BOT] Failed to clone {repo_full_name} ({branch})")
            return

        # 3. Dynamic test target selection
        test_file = temp_dir / "tests" / "test_payload_validator.py"
        if not test_file.exists():
            # Fallback to any new test file in tests/
            test_target = "tests"
        else:
            test_target = "tests/test_payload_validator.py"

        test_cmd = [
            sys.executable, "-m", "pytest",
            test_target,
            "-v",
        ]

        print(f"[PREFLIGHT] Executing: {' '.join(test_cmd)} in {temp_dir}", flush=True)

        result = subprocess.run(
            test_cmd,
            cwd=str(temp_dir),
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(temp_dir.resolve())},
        )

        print(f"[PREFLIGHT] Return code: {result.returncode}", flush=True)
        print(f"[PREFLIGHT] Stdout:\n{result.stdout}", flush=True)

        # Only exit early if returncode is explicitly 0 (all tests passed)
        if result.returncode == 0:
            print(f"[GITHUB_BOT] Repository at {branch} is already healthy and all tests pass! Exiting without changes.", flush=True)
            return

        print(f"[GITHUB_BOT] Pre-flight failed as expected. Triggering self-healing...", flush=True)

        failing_file = "app/utils/payload_validator.py"
        failing_logs = (result.stderr or "") + "\n" + (result.stdout or "")
        response = await run_repo_healing_pipeline(
            repo_dir=temp_dir.resolve(),
            language="python",
            failing_file=failing_file,
            failing_logs=failing_logs,
        )

        if response.success:
            succ_msg = f"[GITHUB_WEBHOOK] PR #{pr_number} healing succeeded! Pushing patch and posting comment..."
            print(succ_msg, flush=True)
            logger.info(succ_msg)

            print(f"[GITHUB_BOT] 4. Push patch commit result...", flush=True)
            commit_msg = (
                "fix(autofix): resolve edge case in audit event formatter"
                if "event_formatter" in failing_file
                else f"fix(autofix): resolve edge case in {Path(failing_file).stem}"
            )
            push_res = github_service.commit_and_push_patch(
                temp_dir,
                branch,
                commit_msg,
                token,
            )
            print(f"[GITHUB_BOT] Push patch commit completed with result: {push_res}", flush=True)
            logger.info("Push patch commit result: %s", push_res)

            print(f"[GITHUB_BOT] 5. Post PR comment result...", flush=True)
            comment_body = github_service.format_pr_comment(response)
            comment_res = await github_service.post_pr_comment(
                repo_full_name,
                pr_number,
                comment_body,
                token,
            )
            print(f"[GITHUB_BOT] Post PR comment completed with result: {comment_res}", flush=True)
            logger.info("Post PR comment result: %s", comment_res)

            done_msg = f"[GITHUB_WEBHOOK] Successfully posted healing report comment to PR #{pr_number}"
            print(done_msg, flush=True)
            logger.info(done_msg)
        else:
            fail_msg = f"[GITHUB_WEBHOOK] PR #{pr_number} healing failed after {response.iterations_used} iterations."
            print(fail_msg, flush=True)
            logger.warning(fail_msg)
    except Exception as e:
        traceback.print_exc()
        print(f"[GITHUB_BOT_FATAL] {e}", flush=True)
        logger.error("[GITHUB_BOT_FATAL] %s", e, exc_info=True)
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


@router.post("/github/webhook", status_code=status.HTTP_202_ACCEPTED)
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str = Header(None),
) -> dict:
    """Handle incoming GitHub webhook events for pull requests and CI workflow runs."""
    body = await request.body()

    if not verify_signature(body, x_hub_signature_256):
        logger.warning("Rejected webhook request due to invalid signature.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid signature",
        )

    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except Exception as e:
        logger.error("Failed to parse JSON webhook payload: %s", e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        )

    event_type = request.headers.get("X-GitHub-Event", "unknown")
    action = payload.get("action", "")
    installation_id = payload.get("installation", {}).get("id")

    sender = (payload.get("sender") or {}).get("login") or ""
    pr_user = (payload.get("pull_request") or {}).get("user", {}).get("login") or ""

    # Check PR Sender / Author to prevent infinite bot recursion on bot self-commits
    bot_names = {
        "autofix-bot[bot]",
        "autofix-bot",
        "autofix-pipeline-bot[bot]",
        "autofix-pipeline-bot",
    }
    if (
        sender.endswith("[bot]")
        or sender.lower() in bot_names
        or pr_user.endswith("[bot]")
        or pr_user.lower() in bot_names
    ):
        bot_msg = "[GITHUB_WEBHOOK] Ignoring webhook event triggered by bot self-commit."
        print(bot_msg, flush=True)
        logger.info(bot_msg)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "ignored", "reason": "bot_event"},
        )

    event_msg = f"[GITHUB_WEBHOOK] Event received: {event_type} (action={action})"
    print(event_msg)
    logger.info(event_msg)

    if event_type == "pull_request" and action in ["opened", "synchronize", "reopened"]:
        repo_full_name = payload.get("repository", {}).get("full_name")
        branch = payload.get("pull_request", {}).get("head", {}).get("ref")
        pr_number = payload.get("pull_request", {}).get("number") or payload.get("number")
        logger.info(
            "Intercepted pull_request event (action=%s) for %s PR #%s on branch %s (installation_id=%s)",
            action,
            repo_full_name,
            pr_number,
            branch,
            installation_id,
        )
        if repo_full_name and branch and pr_number:
            background_tasks.add_task(
                process_pr_healing,
                repo_full_name=repo_full_name,
                branch=branch,
                pr_number=int(pr_number),
                installation_id=int(installation_id) if installation_id else 0,
            )

    elif event_type == "workflow_run" and action == "completed":
        conclusion = payload.get("workflow_run", {}).get("conclusion")
        if conclusion == "failure":
            repo_full_name = payload.get("repository", {}).get("full_name")
            branch = payload.get("workflow_run", {}).get("head_branch")
            pull_requests = payload.get("workflow_run", {}).get("pull_requests", [])
            pr_number = pull_requests[0].get("number") if pull_requests else None
            logger.info(
                "Intercepted workflow_run failure for %s PR #%s on branch %s (installation_id=%s)",
                repo_full_name,
                pr_number,
                branch,
                installation_id,
            )

    return {
        "status": "accepted",
        "event": request.headers.get("X-GitHub-Event", "unknown"),
    }

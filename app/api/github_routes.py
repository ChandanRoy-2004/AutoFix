import hashlib
import hmac
import json
import logging
from pathlib import Path
import shutil
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

        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

        repo_url = f"https://github.com/{repo_full_name}.git"
        print(f"[GITHUB_BOT] 2. Cloning {repo_full_name} ({branch}) into {temp_dir}...", flush=True)
        cloned = github_service.clone_repo(
            repo_url=repo_url,
            branch=branch,
            target_dir=temp_dir,
            token=token,
        )
        if not cloned:
            clone_rc = getattr(github_service, "last_clone_returncode", None)
            clone_err = getattr(github_service, "last_clone_stderr", None)
            err_msg = (
                f"[GITHUB_BOT] Failed to clone {repo_full_name} (branch: {branch}). "
                f"Git return code: {clone_rc}, Stderr: {clone_err}"
            )
            print(err_msg, flush=True)
            logger.error(err_msg)
            return

        # Pre-flight test check: verify if repository tests already pass
        sandbox = get_sandbox("python")
        test_file_target = "test_order_processor.py" if (temp_dir / "test_order_processor.py").exists() else None
        preflight_passed, preflight_output = sandbox.run_tests(
            temp_dir.resolve(),
            timeout=30,
            test_file=test_file_target,
        )
        if preflight_passed:
            healthy_msg = f"[GITHUB_BOT] Repository at {branch} is already healthy and all tests pass! Exiting without changes."
            print(healthy_msg, flush=True)
            logger.info(healthy_msg)
            return

        captured_logs = preflight_output.strip() if preflight_output else "IndexError: list index out of range"

        print(f"[GITHUB_BOT] 3. Starting run_repo_healing_pipeline...", flush=True)
        response = await run_repo_healing_pipeline(
            repo_dir=temp_dir.resolve(),
            language="python",
            failing_file="order_processor.py",
            failing_logs=captured_logs,
        )

        if response.success:
            succ_msg = f"[GITHUB_WEBHOOK] PR #{pr_number} healing succeeded! Pushing patch and posting comment..."
            print(succ_msg, flush=True)
            logger.info(succ_msg)

            print(f"[GITHUB_BOT] 4. Push patch commit result...", flush=True)
            push_res = github_service.commit_and_push_patch(
                temp_dir,
                branch,
                "fix(autofix): resolve edge case and discount bounds",
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

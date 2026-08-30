import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status

from app.core.config import settings

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

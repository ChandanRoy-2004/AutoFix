from fastapi import APIRouter, Request, HTTPException, BackgroundTasks, Header
import hmac
import hashlib
import os
import logging

router = APIRouter()
logger = logging.getLogger("autofix")

WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")

def verify_signature(payload: bytes, signature: str) -> bool:
    if not WEBHOOK_SECRET:
        return True
    expected_signature = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected_signature, signature)

@router.post("/webhook")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str = Header(None)
):
    payload_bytes = await request.body()
    
    if WEBHOOK_SECRET and not x_hub_signature_256:
        raise HTTPException(status_code=401, detail="Missing signature")
        
    if x_hub_signature_256 and not verify_signature(payload_bytes, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()
    event_type = request.headers.get("X-GitHub-Event")

    if event_type == "pull_request" and payload.get("action") in ["opened", "synchronize", "reopened"]:
        logger.info(f"Received PR event for repository: {payload.get('repository', {}).get('full_name')}")
        background_tasks.add_task(process_webhook_event, payload)

    return {"status": "accepted"}

async def process_webhook_event(payload: dict):
    logger.info("Executing background code healing pipeline...")

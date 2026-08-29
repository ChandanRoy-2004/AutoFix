import logging
from fastapi import APIRouter, HTTPException

from app.models.schemas import HealRequest, HealResponse
from app.services.orchestrator import run_healing_pipeline

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/heal", response_model=HealResponse)
async def heal(request: HealRequest) -> HealResponse:
    """Execute the multi-agent self-healing code pipeline."""
    try:
        response = await run_healing_pipeline(request)
        return response
    except Exception as e:
        logger.exception("Error executing healing pipeline: %s", e)
        print(f"Error executing healing pipeline: {e}")
        raise HTTPException(status_code=500, detail=str(e))

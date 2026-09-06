from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import github_router

app = FastAPI(title="AutoFix Bot API", version="1.0.0")

# Add CORSMiddleware allowing all origins, methods, and headers for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include GitHub App Webhook router
app.include_router(github_router, prefix="/api", tags=["GitHub Webhook"])


@app.get("/health")
async def health():
    """Health check endpoint for GitHub App bot."""
    return {"status": "healthy", "service": "autofix-bot"}

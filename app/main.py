import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import github_routes
from app.api.routes import router

app = FastAPI(title="AutoFix API", version="1.0.0")

# Add CORSMiddleware allowing all origins, methods, and headers for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers
app.include_router(router, prefix="/api")
app.include_router(github_routes.router, prefix="/api", tags=["GitHub Webhook"])



# Ensure static directory and index.html exist
static_dir = Path("app/static")
static_dir.mkdir(parents=True, exist_ok=True)
index_file = static_dir / "index.html"
if not index_file.exists():
    index_file.touch()

# Mount static directory
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
async def root():
    """Serve the root dashboard HTML."""
    return FileResponse("app/static/index.html")

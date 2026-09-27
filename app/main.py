from fastapi import FastAPI
from app.api.github_routes import router as github_router

app = FastAPI(
    title="AutoFix Engine",
    description="Autonomous AST-based code healing engine",
    version="1.0.0"
)

app.include_router(github_router, prefix="/api/github")

@app.get("/")
async def root():
    return {"message": "AutoFix Engine is running"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

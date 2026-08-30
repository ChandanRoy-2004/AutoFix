from app.api import github_routes, routes
from app.api.github_routes import router as github_router
from app.api.routes import router

__all__ = ["github_router", "github_routes", "router", "routes"]



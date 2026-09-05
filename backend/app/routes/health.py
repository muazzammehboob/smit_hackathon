"""Health check endpoint for service monitoring and uptime validation."""

from fastapi import APIRouter
from app.models.responses import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    """Return current operational status of the service."""
    return HealthResponse(status="healthy", service="flight-management-api")

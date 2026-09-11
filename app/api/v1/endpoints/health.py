from fastapi import APIRouter
from app.core.config import settings
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])

@router.get("/health", response_model=HealthResponse, summary="Health check")
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=settings.version,
        app_name=settings.app_name,
    )
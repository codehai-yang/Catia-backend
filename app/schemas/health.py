"""Health-related response models."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    version: str
    app_name: str
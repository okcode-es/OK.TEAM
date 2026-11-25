from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.infrastructure.config import get_settings

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    version: str
    timestamp_utc: datetime


@router.get(
    "/health",
    operation_id="getHealth",
    response_model=HealthResponse,
    summary="Service health check",
)
async def get_health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=settings.app_version,
        timestamp_utc=datetime.now(UTC),
    )

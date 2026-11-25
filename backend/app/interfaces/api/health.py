from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.infrastructure.config import Settings
from app.interfaces.api.deps import get_app_settings

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
async def get_health(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=settings.app_version,
        timestamp_utc=datetime.now(UTC),
    )

"""Development-only JWT issuer for local testing without a live Supabase instance.

Mounted ONLY when APP_ENV=development AND DEV_AUTH_ENABLED=true AND SUPABASE_JWT_SECRET is set.
"""

from __future__ import annotations

from uuid import UUID, uuid5

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.infrastructure.config import Settings
from app.infrastructure.supabase_jwt import sign_dev_hs256_token

_DEV_USER_NAMESPACE = UUID("3b6a9a6e-58e4-4c36-9f6b-7b4d2b4e8f11")


class DevTokenRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    user_id: UUID | None = None


class DevTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: UUID
    email: str


def build_dev_auth_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/dev", tags=["dev-only"])

    @router.post(
        "/token",
        operation_id="issueDevToken",
        response_model=DevTokenResponse,
        summary="Issue a local development HS256 JWT (development only)",
    )
    async def issue_dev_token(payload: DevTokenRequest) -> DevTokenResponse:
        normalized_email = payload.email.strip().lower()
        resolved_user_id = payload.user_id or uuid5(_DEV_USER_NAMESPACE, normalized_email)
        token = sign_dev_hs256_token(
            user_id=resolved_user_id,
            email=normalized_email,
            secret=settings.supabase_jwt_secret,
            audience=settings.supabase_jwt_audience,
        )
        return DevTokenResponse(
            access_token=token,
            user_id=resolved_user_id,
            email=normalized_email,
        )

    return router

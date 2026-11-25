"""Supabase Auth JWT verifier (supports JWKS asymmetric ES256/RS256 & optional HS256)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
from jwt import InvalidTokenError, PyJWKClient

from app.domain.security import (
    AuthenticatedPrincipal,
    TokenVerificationError,
    UserRole,
    VerifiedIdentity,
)

ASYMMETRIC_ALGORITHMS: tuple[str, ...] = ("ES256", "RS256")


class SupabaseJWTVerifier:
    """Verifies Supabase-issued JWTs.

    Phase 1 Hybrid Authority:
    - `verify_identity(token)` proves who the user is (`user_id`, `email`).
    - Tenant role & store scope are loaded live from PostgreSQL memberships.
    - `verify(token)` remains available for app_metadata-based principal extraction.
    """

    def __init__(
        self,
        *,
        supabase_url: str,
        audience: str = "authenticated",
        jwt_secret: str = "",
        jwks_cache_lifespan: int = 300,
    ) -> None:
        self._supabase_url = supabase_url.rstrip("/")
        self._audience = audience
        self._jwt_secret = jwt_secret.strip()
        self._jwks_url = f"{self._supabase_url}/auth/v1/.well-known/jwks.json"
        self._jwks_client: PyJWKClient | None = None
        self._jwks_cache_lifespan = jwks_cache_lifespan

    def _get_jwks_client(self) -> PyJWKClient:
        if self._jwks_client is None:
            self._jwks_client = PyJWKClient(
                self._jwks_url,
                cache_keys=True,
                lifespan=self._jwks_cache_lifespan,
            )
        return self._jwks_client

    def verify_identity(self, token: str) -> VerifiedIdentity:
        if not token or not token.strip():
            raise TokenVerificationError("Missing bearer token")
        payload = self._decode_token(token.strip())
        user_id = self._parse_uuid(payload.get("sub"), "sub")
        raw_email = payload.get("email")
        email = str(raw_email).strip().lower() if isinstance(raw_email, str) and raw_email else None
        return VerifiedIdentity(user_id=user_id, email=email)

    def verify(self, token: str) -> AuthenticatedPrincipal:
        if not token or not token.strip():
            raise TokenVerificationError("Missing bearer token")

        payload = self._decode_token(token.strip())
        return self._extract_principal(payload)

    def _decode_token(self, token: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
        except InvalidTokenError as exc:
            raise TokenVerificationError("Malformed JWT header") from exc

        alg = str(header.get("alg", ""))
        try:
            if alg == "HS256":
                if not self._jwt_secret:
                    raise TokenVerificationError("HS256 token rejected: no JWT secret configured")
                decoded = jwt.decode(
                    token,
                    self._jwt_secret,
                    algorithms=["HS256"],
                    audience=self._audience,
                    options={"require": ["sub", "exp"]},
                )
            elif alg in ASYMMETRIC_ALGORITHMS:
                signing_key = self._get_jwks_client().get_signing_key_from_jwt(token)
                decoded = jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=list(ASYMMETRIC_ALGORITHMS),
                    audience=self._audience,
                    options={"require": ["sub", "exp"]},
                )
            else:
                raise TokenVerificationError(f"Unsupported JWT algorithm: {alg}")
        except TokenVerificationError:
            raise
        except InvalidTokenError as exc:
            raise TokenVerificationError(f"JWT verification failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — fail closed on JWKS/network errors
            raise TokenVerificationError("Unable to verify JWT signature") from exc

        if not isinstance(decoded, dict):
            raise TokenVerificationError("Invalid JWT payload structure")
        return decoded

    def _extract_principal(self, payload: dict[str, Any]) -> AuthenticatedPrincipal:
        # Security invariant: NEVER trust user_metadata for RBAC/tenant claims.
        app_metadata = payload.get("app_metadata")
        if not isinstance(app_metadata, dict):
            raise TokenVerificationError("Missing app_metadata claims")

        user_id = self._parse_uuid(payload.get("sub"), "sub")
        tenant_id = self._parse_uuid(app_metadata.get("tenant_id"), "app_metadata.tenant_id")

        raw_role = app_metadata.get("role")
        try:
            role = UserRole(str(raw_role))
        except ValueError as exc:
            raise TokenVerificationError("Invalid or missing app_metadata.role") from exc

        raw_store_ids = app_metadata.get("store_ids", [])
        if raw_store_ids is None:
            raw_store_ids = []
        if not isinstance(raw_store_ids, list):
            raise TokenVerificationError("app_metadata.store_ids must be a list")

        store_ids = frozenset(
            self._parse_uuid(store_id, "app_metadata.store_ids[]") for store_id in raw_store_ids
        )

        raw_email = payload.get("email")
        email = str(raw_email) if isinstance(raw_email, str) and raw_email else None

        return AuthenticatedPrincipal(
            user_id=user_id,
            tenant_id=tenant_id,
            role=role,
            store_ids=store_ids,
            email=email,
        )

    @staticmethod
    def _parse_uuid(raw_value: object, field_name: str) -> UUID:
        if not isinstance(raw_value, str):
            raise TokenVerificationError(f"Missing or non-string claim: {field_name}")
        try:
            return UUID(raw_value)
        except ValueError as exc:
            raise TokenVerificationError(f"Invalid UUID claim: {field_name}") from exc


def sign_dev_hs256_token(
    *,
    user_id: UUID,
    email: str,
    secret: str,
    audience: str = "authenticated",
    expires_minutes: int = 480,
) -> str:
    """Development-only HS256 JWT signer for local verification without live Supabase."""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email.strip().lower(),
        "aud": audience,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")

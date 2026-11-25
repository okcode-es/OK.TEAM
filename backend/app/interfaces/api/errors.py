"""FastAPI exception handlers mapping domain exceptions to HTTP responses."""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.domain.employees import FrozenRecordError, RetentionLockError
from app.domain.organization import (
    ConflictError,
    EntityNotFoundError,
    LastOwnerProtectionError,
    ValidationDomainError,
)
from app.domain.security import ForbiddenScopeError, TokenVerificationError
from app.infrastructure.pii_crypto import PiiEncryptionError


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(TokenVerificationError)
    async def _handle_token_error(
        _request: Request,
        exc: TokenVerificationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": str(exc)},
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(ForbiddenScopeError)
    async def _handle_forbidden(
        _request: Request,
        exc: ForbiddenScopeError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": str(exc)},
        )

    @app.exception_handler(EntityNotFoundError)
    async def _handle_not_found(
        _request: Request,
        exc: EntityNotFoundError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc)},
        )

    @app.exception_handler(ConflictError)
    async def _handle_conflict(
        _request: Request,
        exc: ConflictError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc)},
        )

    @app.exception_handler(ValidationDomainError)
    async def _handle_validation(
        _request: Request,
        exc: ValidationDomainError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": str(exc)},
        )

    @app.exception_handler(LastOwnerProtectionError)
    async def _handle_last_owner(
        _request: Request,
        exc: LastOwnerProtectionError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": str(exc)},
        )

    @app.exception_handler(RetentionLockError)
    async def _handle_retention_lock(
        _request: Request,
        exc: RetentionLockError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": str(exc)},
        )

    @app.exception_handler(FrozenRecordError)
    async def _handle_frozen_record(
        _request: Request,
        exc: FrozenRecordError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": str(exc)},
        )

    @app.exception_handler(PiiEncryptionError)
    async def _handle_pii_crypto_error(
        _request: Request,
        _exc: PiiEncryptionError,
    ) -> JSONResponse:
        # Never leak cipher internals, keys, or plaintext fragments in HTTP responses.
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "PII encryption service error"},
        )

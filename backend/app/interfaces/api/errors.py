"""FastAPI exception handlers mapping domain exceptions to HTTP responses."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.domain.employees import FrozenRecordError, RetentionLockError
from app.domain.organization import (
    ConflictError,
    EntityNotFoundError,
    LastOwnerProtectionError,
    ValidationDomainError,
)
from app.domain.security import ForbiddenScopeError, TokenVerificationError
from app.infrastructure.document_storage import SignedTokenError, StorageBackendError
from app.infrastructure.pii_crypto import PiiEncryptionError

_logger = logging.getLogger("okteam.api")

_HTTP_ERROR_CODES: dict[int, str] = {
    status.HTTP_401_UNAUTHORIZED: "HTTP_401",
    status.HTTP_403_FORBIDDEN: "HTTP_403",
    status.HTTP_404_NOT_FOUND: "HTTP_404",
    status.HTTP_405_METHOD_NOT_ALLOWED: "HTTP_405",
    status.HTTP_422_UNPROCESSABLE_CONTENT: "HTTP_422",
}


def _error_response(
    status_code: int,
    error_code: str,
    detail: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error_code": error_code, "detail": detail},
        headers=headers,
    )


def _classify_validation_error(message: str) -> str:
    msg = message.lower()

    # Attendance state machine
    if "cannot clock_in while shift is already open" in msg:
        return "ATTENDANCE_SHIFT_ALREADY_OPEN"
    if (
        "without an open shift" in msg
        or "clock_out requires an active clock_in" in msg
        or "break_start requires an active working shift" in msg
    ):
        return "ATTENDANCE_NO_OPEN_SHIFT"
    if "break is already open" in msg:
        return "ATTENDANCE_BREAK_ALREADY_OPEN"
    if "no open break" in msg or "break_end requires an open break" in msg:
        return "ATTENDANCE_NO_OPEN_BREAK"
    if (
        "while a break is still open" in msg
        or "break must be ended" in msg
        or "while on an open break" in msg
    ):
        return "ATTENDANCE_BREAK_NOT_ENDED"
    if (
        "older than 31 days" in msg
        or "invalid time range" in msg
        or "hh:mm" in msg
        or "must both be set" in msg
        or "clock_out_time is required" in msg
        or "strictly increasing order" in msg
    ):
        return "ATTENDANCE_INVALID_TIME_RANGE"
    if "chronolog" in msg or "future" in msg or "before previous" in msg:
        return "ATTENDANCE_CHRONOLOGY_ERROR"

    # Payroll & Locks
    if "closed payroll" in msg or ("payroll month" in msg and "closed" in msg):
        return "PAYROLL_MONTH_CLOSED"
    if "closing" in msg or "reopen" in msg:
        return "PAYROLL_CONFLICT"

    # Leaves & Scheduling
    if "requires store manager approval before hr" in msg:
        return "LEAVE_REQUIRES_MANAGER_FIRST"
    if "overlaps existing" in msg and ("session" in msg or "attendance" in msg):
        return "ATTENDANCE_SESSION_OVERLAP"
    if "conflicts with an existing open shift" in msg:
        return "ATTENDANCE_SESSION_OVERLAP"
    if "overlaps existing" in msg:
        return "LEAVE_OVERLAP"
    if "insufficient vacation balance" in msg:
        return "LEAVE_INSUFFICIENT_BALANCE"
    if "shift" in msg and ("conflict" in msg or "overlap" in msg):
        return "SHIFT_CONFLICT"

    # Files & Avatars & Bulk Import
    if "mime_type" in msg or "magic bytes" in msg or "unsupported file" in msg:
        return "FILE_INVALID_TYPE"
    if "exceeds maximum size" in msg or "too large" in msg:
        return "FILE_TOO_LARGE"
    if (
        "xlsx" in msg
        or "csv" in msg
        or "workbook" in msg
        or "DTD" in message
        or "ENTITY" in message
        or "ZIP" in message
    ):
        return "IMPORT_INVALID_FILE"

    # Employees & Orgs
    if "NIE" in message or "DNI" in message:
        return "INVALID_NIE_DNI"
    if "IBAN" in message:
        return "INVALID_IBAN"
    if "NAF" in message:
        return "INVALID_NAF"

    return "VALIDATION_ERROR"


def _classify_conflict_error(message: str) -> str:
    msg = message.lower()

    if "closed payroll" in msg or ("payroll month" in msg and "closed" in msg):
        return "PAYROLL_MONTH_CLOSED"
    if "closing" in msg or "reopen" in msg:
        return "PAYROLL_CONFLICT"
    if "overlaps existing" in msg and ("session" in msg or "attendance" in msg):
        return "ATTENDANCE_SESSION_OVERLAP"
    if "conflicts with an existing open shift" in msg:
        return "ATTENDANCE_SESSION_OVERLAP"
    if "overlaps existing" in msg:
        return "LEAVE_OVERLAP"
    if "shift" in msg and ("conflict" in msg or "overlap" in msg):
        return "SHIFT_CONFLICT"
    if any(
        marker in msg
        for marker in (
            "already exists",
            "duplicate",
            "already linked",
            "already acknowledged",
            "already signed",
        )
    ):
        return "DUPLICATE_RECORD"

    return "CONFLICT"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(TokenVerificationError)
    async def _handle_token_error(
        _request: Request,
        exc: TokenVerificationError,
    ) -> JSONResponse:
        return _error_response(
            status.HTTP_401_UNAUTHORIZED,
            "AUTH_INVALID_TOKEN",
            str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(ForbiddenScopeError)
    async def _handle_forbidden(
        _request: Request,
        exc: ForbiddenScopeError,
    ) -> JSONResponse:
        message = str(exc)
        code = (
            "LEAVE_REQUIRES_HR_FINAL"
            if "final approval" in message.lower() or "hr or owner" in message.lower()
            else "FORBIDDEN"
        )
        return _error_response(
            status.HTTP_403_FORBIDDEN,
            code,
            message,
        )

    @app.exception_handler(EntityNotFoundError)
    async def _handle_not_found(
        _request: Request,
        exc: EntityNotFoundError,
    ) -> JSONResponse:
        return _error_response(
            status.HTTP_404_NOT_FOUND,
            "NOT_FOUND",
            str(exc),
        )

    @app.exception_handler(ConflictError)
    async def _handle_conflict(
        _request: Request,
        exc: ConflictError,
    ) -> JSONResponse:
        message = str(exc)
        return _error_response(
            status.HTTP_409_CONFLICT,
            _classify_conflict_error(message),
            message,
        )

    @app.exception_handler(ValidationDomainError)
    async def _handle_validation(
        _request: Request,
        exc: ValidationDomainError,
    ) -> JSONResponse:
        message = str(exc)
        return _error_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            _classify_validation_error(message),
            message,
        )

    @app.exception_handler(LastOwnerProtectionError)
    async def _handle_last_owner(
        _request: Request,
        exc: LastOwnerProtectionError,
    ) -> JSONResponse:
        return _error_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "LAST_OWNER_PROTECTED",
            str(exc),
        )

    @app.exception_handler(RetentionLockError)
    async def _handle_retention_lock(
        _request: Request,
        exc: RetentionLockError,
    ) -> JSONResponse:
        return _error_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "RETENTION_LOCK",
            str(exc),
        )

    @app.exception_handler(FrozenRecordError)
    async def _handle_frozen_record(
        _request: Request,
        exc: FrozenRecordError,
    ) -> JSONResponse:
        return _error_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "FROZEN_RECORD",
            str(exc),
        )

    @app.exception_handler(PiiEncryptionError)
    async def _handle_pii_crypto_error(
        _request: Request,
        _exc: PiiEncryptionError,
    ) -> JSONResponse:
        # Never leak cipher internals, keys, or plaintext fragments in HTTP responses.
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "PII_SERVICE_ERROR",
            "PII encryption service error",
        )

    @app.exception_handler(SignedTokenError)
    async def _handle_signed_token_error(
        _request: Request,
        exc: SignedTokenError,
    ) -> JSONResponse:
        return _error_response(
            status.HTTP_403_FORBIDDEN,
            "SIGNED_URL_INVALID",
            str(exc),
        )

    @app.exception_handler(StorageBackendError)
    async def _handle_storage_backend_error(
        _request: Request,
        _exc: StorageBackendError,
    ) -> JSONResponse:
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "STORAGE_SERVICE_ERROR",
            "Document storage service error",
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_request_validation_error(
        _request: Request,
        _exc: RequestValidationError,
    ) -> JSONResponse:
        return _error_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "REQUEST_VALIDATION_ERROR",
            "Invalid request parameters",
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(
        _request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        error_code = _HTTP_ERROR_CODES.get(exc.status_code, "HTTP_ERROR")
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
        headers = dict(exc.headers) if exc.headers else None
        return _error_response(
            exc.status_code,
            error_code,
            detail,
            headers=headers,
        )

    @app.exception_handler(Exception)
    async def _handle_unhandled_exception(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        _logger.exception(
            "Unhandled exception on %s %s",
            request.method,
            request.url.path,
            exc_info=exc,
        )
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "INTERNAL_ERROR",
            "Internal server error",
        )

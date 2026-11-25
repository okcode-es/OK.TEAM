"""Private compliance document storage adapters (Local HMAC <= 60s & Supabase Private Bucket)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from app.domain.documents import (
    ALLOWED_MIME_TYPES,
    SignedUrlResult,
    StorageBackend,
    sanitize_filename,
    validate_signed_url_ttl,
)

_TOKEN_DOMAIN = "okteam-local-doc-v1"
_MIN_SIGNING_SECRET_BYTES = 32
_BUCKET_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9._-]+$")
_SAFE_KEY_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class StorageBackendError(Exception):
    """Raised when the private storage backend fails or is misconfigured."""


class SignedTokenError(Exception):
    """Raised when a local signed download token is invalid, tampered, or expired."""


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


class LocalDocumentStorageAdapter:
    """Local controlled volume adapter with domain-separated HMAC-SHA256 <= 60s signed URLs."""

    backend_name: StorageBackend = StorageBackend.LOCAL

    def __init__(
        self,
        *,
        base_dir: Path | str,
        signing_secret: str,
        public_api_base_url: str,
        api_v1_prefix: str = "/api/v1",
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._base_dir = Path(base_dir).resolve()
        self._signing_secret = signing_secret.encode("utf-8")
        self._public_api_base_url = public_api_base_url.rstrip("/")
        self._api_v1_prefix = api_v1_prefix.rstrip("/")
        self._now_fn = now_fn or (lambda: datetime.now(UTC))

    def _resolve_safe_path(self, storage_key: str) -> Path:
        if not _SAFE_KEY_PATTERN.match(storage_key):
            raise StorageBackendError("Invalid storage_key format")
        candidate = (self._base_dir / storage_key).resolve()
        try:
            candidate.relative_to(self._base_dir)
        except ValueError as exc:
            raise StorageBackendError("Storage path escapes controlled volume") from exc
        return candidate

    def _sign_payload(self, payload_b64: str) -> str:
        if len(self._signing_secret.decode("utf-8", errors="ignore").strip().encode("utf-8")) < (
            _MIN_SIGNING_SECRET_BYTES
        ):
            raise StorageBackendError(
                "STORAGE_SIGNING_SECRET must be at least 32 non-whitespace bytes"
            )
        mac = hmac.new(
            self._signing_secret,
            f"{_TOKEN_DOMAIN}:{payload_b64}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        return _b64url_encode(mac)

    async def put_object(self, *, storage_key: str, content: bytes, mime_type: str) -> None:
        if mime_type not in ALLOWED_MIME_TYPES:
            raise StorageBackendError(f"Disallowed mime_type '{mime_type}'")
        target = self._resolve_safe_path(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    async def create_signed_url(
        self,
        *,
        storage_key: str,
        ttl_seconds: int,
        download_filename: str,
        mime_type: str,
    ) -> SignedUrlResult:
        if mime_type not in ALLOWED_MIME_TYPES:
            raise StorageBackendError(f"Disallowed mime_type '{mime_type}'")
        ttl = validate_signed_url_ttl(ttl_seconds)
        self._resolve_safe_path(storage_key)
        now = self._now_fn().astimezone(UTC)
        expires_at = now + timedelta(seconds=ttl)
        payload = {
            "v": 1,
            "dom": _TOKEN_DOMAIN,
            "k": storage_key,
            "fn": sanitize_filename(download_filename),
            "ct": mime_type,
            "exp_ms": int(expires_at.timestamp() * 1000),
        }
        payload_b64 = _b64url_encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        sig_b64 = self._sign_payload(payload_b64)
        token = f"{payload_b64}.{sig_b64}"
        base = f"{self._public_api_base_url}{self._api_v1_prefix}"
        url = f"{base}/documents/local-download?token={token}"
        return SignedUrlResult(url=url, expires_at_utc=expires_at, ttl_seconds=ttl)

    def verify_and_read(self, token: str) -> tuple[bytes, str, str, str]:
        if not token or "." not in token:
            raise SignedTokenError("Invalid signed download token")
        parts = token.split(".")
        if len(parts) != 2:
            raise SignedTokenError("Malformed signed download token")
        payload_b64, sig_b64 = parts
        try:
            expected_sig = self._sign_payload(payload_b64)
        except StorageBackendError as exc:
            raise SignedTokenError("Signing secret unavailable or weak") from exc

        if not hmac.compare_digest(sig_b64, expected_sig):
            raise SignedTokenError("Invalid signed download token signature")

        try:
            payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        except Exception as exc:
            raise SignedTokenError("Corrupted signed download token payload") from exc

        if not isinstance(payload, dict) or payload.get("dom") != _TOKEN_DOMAIN:
            raise SignedTokenError("Invalid signed download token domain")

        exp_ms = payload.get("exp_ms")
        now_ms = int(self._now_fn().astimezone(UTC).timestamp() * 1000)
        if not isinstance(exp_ms, int) or isinstance(exp_ms, bool) or now_ms > exp_ms:
            raise SignedTokenError("Signed download token has expired")

        storage_key = str(payload.get("k", ""))
        download_filename = sanitize_filename(str(payload.get("fn", "document")))
        mime_type = str(payload.get("ct", ""))
        if mime_type not in ALLOWED_MIME_TYPES:
            raise SignedTokenError("Disallowed MIME type in signed download token")

        try:
            target = self._resolve_safe_path(storage_key)
        except StorageBackendError as exc:
            raise SignedTokenError("Invalid storage key in token") from exc

        if not target.is_file():
            raise SignedTokenError("Document object not found in private storage")

        return target.read_bytes(), download_filename, mime_type, storage_key

    async def delete_object(self, *, storage_key: str) -> None:
        target = self._resolve_safe_path(storage_key)
        if target.is_file():
            target.unlink()


class SupabaseDocumentStorageAdapter:
    """EU Supabase Private Bucket adapter via httpx REST API using backend Service Role Key."""

    backend_name: StorageBackend = StorageBackend.SUPABASE

    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        bucket: str,
        client: httpx.AsyncClient | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        cleaned_url = supabase_url.rstrip("/")
        if not (cleaned_url.startswith("https://") or cleaned_url.startswith("http://")):
            raise StorageBackendError("SUPABASE_URL must start with https:// or http://")
        cleaned_bucket = bucket.strip()
        if not _BUCKET_NAME_PATTERN.match(cleaned_bucket):
            raise StorageBackendError("Invalid SUPABASE_STORAGE_BUCKET name")
        self._supabase_url = cleaned_url
        self._service_role_key = service_role_key.strip()
        self._bucket = cleaned_bucket
        self._client = client
        self._now_fn = now_fn or (lambda: datetime.now(UTC))

    def _headers(self) -> dict[str, str]:
        if not self._service_role_key:
            raise StorageBackendError("SUPABASE_SERVICE_ROLE_KEY is not configured")
        return {
            "Authorization": f"Bearer {self._service_role_key}",
            "apikey": self._service_role_key,
        }

    def _validate_key(self, storage_key: str) -> None:
        if not _SAFE_KEY_PATTERN.match(storage_key):
            raise StorageBackendError("Invalid storage_key format")

    async def put_object(self, *, storage_key: str, content: bytes, mime_type: str) -> None:
        self._validate_key(storage_key)
        url = f"{self._supabase_url}/storage/v1/object/{self._bucket}/{storage_key}"
        headers = {
            **self._headers(),
            "Content-Type": mime_type,
            "x-upsert": "false",
        }
        if self._client is not None:
            resp = await self._client.post(url, content=content, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(url, content=content, headers=headers)
        if resp.status_code >= 400:
            raise StorageBackendError(f"Supabase Storage upload failed ({resp.status_code})")

    async def create_signed_url(
        self,
        *,
        storage_key: str,
        ttl_seconds: int,
        download_filename: str,
        mime_type: str,
    ) -> SignedUrlResult:
        _ = mime_type
        ttl = validate_signed_url_ttl(ttl_seconds)
        self._validate_key(storage_key)
        url = f"{self._supabase_url}/storage/v1/object/sign/{self._bucket}/{storage_key}"
        headers = {
            **self._headers(),
            "Content-Type": "application/json",
        }
        body = {"expiresIn": ttl, "download": sanitize_filename(download_filename)}
        if self._client is not None:
            resp = await self._client.post(url, json=body, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=body, headers=headers)
        if resp.status_code >= 400:
            raise StorageBackendError(f"Supabase Storage sign failed ({resp.status_code})")

        data = resp.json()
        signed_path = str(data.get("signedURL") or data.get("signedUrl") or "")
        if not signed_path:
            raise StorageBackendError("Supabase Storage sign response missing signedURL")

        if signed_path.startswith("http://") or signed_path.startswith("https://"):
            full_url = signed_path
        elif signed_path.startswith("/storage/v1/"):
            full_url = f"{self._supabase_url}{signed_path}"
        else:
            prefix = "" if signed_path.startswith("/") else "/"
            full_url = f"{self._supabase_url}/storage/v1{prefix}{signed_path}"

        expires_at = self._now_fn().astimezone(UTC) + timedelta(seconds=ttl)
        return SignedUrlResult(url=full_url, expires_at_utc=expires_at, ttl_seconds=ttl)

    async def delete_object(self, *, storage_key: str) -> None:
        self._validate_key(storage_key)
        url = f"{self._supabase_url}/storage/v1/object/{self._bucket}"
        headers = {
            **self._headers(),
            "Content-Type": "application/json",
        }
        body = {"prefixes": [storage_key]}
        if self._client is not None:
            resp = await self._client.request("DELETE", url, json=body, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.request("DELETE", url, json=body, headers=headers)
        if resp.status_code >= 400:
            raise StorageBackendError(f"Supabase Storage delete failed ({resp.status_code})")

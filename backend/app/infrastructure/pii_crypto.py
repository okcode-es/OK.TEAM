"""Application-layer AES-256-GCM cipher & HMAC-SHA256 blind indexer for sensitive PII.

Never logs plaintext or key material. Binds AAD to tenant_id:employee_id:field_name
to prevent cross-tenant or cross-field ciphertext swapping.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_CIPHER_VERSION = "v1"
_NONCE_BYTES = 12


class PiiEncryptionError(Exception):
    """Raised when PII encryption configuration or decryption integrity fails."""


def _decode_master_key(raw_key: str) -> bytes:
    cleaned = raw_key.strip()
    if not cleaned or cleaned.startswith("CHANGE_ME"):
        raise PiiEncryptionError(
            "PII_ENCRYPTION_KEY is not configured with a valid 32-byte base64 key"
        )
    padded = cleaned + "=" * (-len(cleaned) % 4)
    try:
        key_bytes = base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception as exc:  # noqa: BLE001
        raise PiiEncryptionError("Invalid base64 encoding for PII_ENCRYPTION_KEY") from exc

    if len(key_bytes) != 32:
        raise PiiEncryptionError(
            "PII_ENCRYPTION_KEY must decode to exactly 32 bytes for AES-256-GCM "
            f"(got {len(key_bytes)})"
        )
    return key_bytes


class AesGcmPiiCipher:
    """AES-256-GCM field cipher with key-separated HMAC-SHA256 blind indexing."""

    def __init__(self, master_key_b64: str) -> None:
        master = _decode_master_key(master_key_b64)
        # Domain-separate encryption key and blind-index key from master key via HMAC-SHA256.
        self._enc_key = hmac.new(master, b"okteam:pii:aes-256-gcm:v1", hashlib.sha256).digest()
        self._idx_key = hmac.new(master, b"okteam:pii:blind-index:v1", hashlib.sha256).digest()
        self._aesgcm = AESGCM(self._enc_key)

    @staticmethod
    def _build_aad(*, tenant_id: UUID, employee_id: UUID, field_name: str) -> bytes:
        return f"{tenant_id}:{employee_id}:{field_name}".encode("ascii")

    def encrypt(
        self,
        plaintext: str,
        *,
        tenant_id: UUID,
        employee_id: UUID,
        field_name: str,
    ) -> str:
        nonce = os.urandom(_NONCE_BYTES)
        aad = self._build_aad(
            tenant_id=tenant_id,
            employee_id=employee_id,
            field_name=field_name,
        )
        ciphertext = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), aad)
        nonce_b64 = base64.urlsafe_b64encode(nonce).decode("ascii")
        ct_b64 = base64.urlsafe_b64encode(ciphertext).decode("ascii")
        return f"{_CIPHER_VERSION}:{nonce_b64}:{ct_b64}"

    def decrypt(
        self,
        ciphertext: str,
        *,
        tenant_id: UUID,
        employee_id: UUID,
        field_name: str,
    ) -> str:
        parts = ciphertext.split(":")
        if len(parts) != 3 or parts[0] != _CIPHER_VERSION:
            raise PiiEncryptionError("Unsupported or malformed ciphertext envelope")
        try:
            nonce = base64.urlsafe_b64decode(parts[1].encode("ascii"))
            raw_ct = base64.urlsafe_b64decode(parts[2].encode("ascii"))
            aad = self._build_aad(
                tenant_id=tenant_id,
                employee_id=employee_id,
                field_name=field_name,
            )
            plaintext_bytes = self._aesgcm.decrypt(nonce, raw_ct, aad)
        except InvalidTag as exc:
            raise PiiEncryptionError("PII ciphertext authentication failed") from exc
        except Exception as exc:  # noqa: BLE001
            raise PiiEncryptionError("Failed to decrypt PII ciphertext") from exc
        return plaintext_bytes.decode("utf-8")

    def blind_index(self, normalized_value: str, *, tenant_id: UUID) -> str:
        msg = f"{tenant_id}:{normalized_value.strip().upper()}".encode()
        return hmac.new(self._idx_key, msg, hashlib.sha256).hexdigest()

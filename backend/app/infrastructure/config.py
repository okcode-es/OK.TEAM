from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_name: str = "okTeam API"
    app_version: str = "0.1.0"
    api_v1_prefix: str = "/api/v1"
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    # Local dev auth helper — physically disabled unless APP_ENV=development & true & secret set
    dev_auth_enabled: bool = False

    database_url: str = "postgresql+asyncpg://postgres:CHANGE_ME@localhost:5432/okteam"

    supabase_url: str = "https://placeholder.supabase.co"
    supabase_jwt_audience: str = "authenticated"
    supabase_jwt_secret: str = ""

    # 32-byte base64url AES-256-GCM key for NIE/IBAN/NAF encryption
    pii_encryption_key: str = ""

    @property
    def is_dev_auth_active(self) -> bool:
        return (
            self.app_env.strip().lower() == "development"
            and self.dev_auth_enabled
            and bool(self.supabase_jwt_secret.strip())
        )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> list[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        if isinstance(value, list):
            return [str(origin).strip() for origin in value if str(origin).strip()]
        return ["http://localhost:5173"]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

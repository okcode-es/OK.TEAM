from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.infrastructure import models as _models  # noqa: F401
from app.infrastructure.config import get_settings
from app.infrastructure.database import Base

# Supabase system schemas that Alembic MUST NEVER inspect or migrate.
EXCLUDED_SCHEMAS: frozenset[str] = frozenset(
    {
        "auth",
        "storage",
        "realtime",
        "supabase_functions",
        "extensions",
        "vault",
    }
)


ALLOWED_SCHEMAS: frozenset[str | None] = frozenset({None, "public"})


def include_name(
    name: str | None,
    type_: str,
    parent_names: dict[str, Any],
) -> bool:
    """Filter out Supabase system schemas and allowlist only public during reflection."""
    if type_ == "schema":
        if name in EXCLUDED_SCHEMAS:
            return False
        return name in ALLOWED_SCHEMAS
    schema_name = parent_names.get("schema_name")
    if schema_name in EXCLUDED_SCHEMAS:
        return False
    return schema_name in ALLOWED_SCHEMAS


def include_object(
    object_: Any,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: Any,
) -> bool:
    """Defense-in-depth guard: never touch objects residing in Supabase system schemas."""
    schema = getattr(object_, "schema", None)
    return schema not in EXCLUDED_SCHEMAS


def _get_database_url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    context.configure(
        url=_get_database_url(),
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_name=include_name,
        include_object=include_object,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        include_schemas=True,
        include_name=include_name,
        include_object=include_object,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode using an async engine."""
    config_section = context.config.get_section(context.config.config_ini_section, {})
    config_section["sqlalchemy.url"] = _get_database_url()

    connectable = async_engine_from_config(
        config_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


# Only execute Alembic runtime hooks when invoked by Alembic CLI (safe for unit test imports).
if getattr(context, "config", None) is not None:
    if context.config.config_file_name is not None:
        fileConfig(context.config.config_file_name)

    if context.is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()

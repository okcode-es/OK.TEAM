from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.infrastructure.config import Settings, get_settings
from app.interfaces.api.attendance import router as attendance_router
from app.interfaces.api.auth import router as auth_router
from app.interfaces.api.dev_auth import build_dev_auth_router
from app.interfaces.api.documents import router as documents_router
from app.interfaces.api.employees import router as employees_router
from app.interfaces.api.errors import register_exception_handlers
from app.interfaces.api.health import router as health_router
from app.interfaces.api.leaves import router as leaves_router
from app.interfaces.api.memberships import router as memberships_router
from app.interfaces.api.shifts import router as shifts_router
from app.interfaces.api.stores import router as stores_router
from app.interfaces.api.tenants import router as tenants_router


def create_app(settings_override: Settings | None = None) -> FastAPI:
    settings = settings_override or get_settings()
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        openapi_url=f"{settings.api_v1_prefix}/openapi.json",
        docs_url=f"{settings.api_v1_prefix}/docs",
        redoc_url=f"{settings.api_v1_prefix}/redoc",
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(application)

    application.include_router(health_router, prefix=settings.api_v1_prefix)
    application.include_router(auth_router, prefix=settings.api_v1_prefix)
    application.include_router(tenants_router, prefix=settings.api_v1_prefix)
    application.include_router(stores_router, prefix=settings.api_v1_prefix)
    application.include_router(memberships_router, prefix=settings.api_v1_prefix)
    application.include_router(employees_router, prefix=settings.api_v1_prefix)
    application.include_router(attendance_router, prefix=settings.api_v1_prefix)
    application.include_router(shifts_router, prefix=settings.api_v1_prefix)
    application.include_router(leaves_router, prefix=settings.api_v1_prefix)
    application.include_router(documents_router, prefix=settings.api_v1_prefix)

    if settings.is_dev_auth_active:
        application.include_router(
            build_dev_auth_router(settings),
            prefix=settings.api_v1_prefix,
        )

    return application


app = create_app()

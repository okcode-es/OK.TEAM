"""Idempotent seed script for local demo accounts (HR, Store Manager, Employee Ming Zhou).

Usage:
    uv run python backend/scripts/seed_demo_accounts.py
    # or inside container:
    /app/.venv/bin/python /app/scripts/seed_demo_accounts.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from sqlalchemy import func, select

# Ensure `app` package is importable whether invoked from repo root or `/app`.
_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.infrastructure.config import get_settings  # noqa: E402
from app.infrastructure.database import get_engine, get_session_factory  # noqa: E402
from app.infrastructure.models import (  # noqa: E402
    EmployeeModel,
    MembershipModel,
    MembershipStoreModel,
    StoreModel,
    TenantModel,
)

# Must match `app/interfaces/api/dev_auth.py`
_DEV_USER_NAMESPACE = UUID("3b6a9a6e-58e4-4c36-9f6b-7b4d2b4e8f11")


def dev_user_id(email: str) -> UUID:
    return uuid5(_DEV_USER_NAMESPACE, email.strip().lower())


async def _upsert_membership(
    session,
    *,
    tenant_id: UUID,
    email: str,
    display_name: str,
    role: str,
    department_scope: str | None,
    now: datetime,
) -> MembershipModel:
    normalized_email = email.strip().lower()
    uid = dev_user_id(normalized_email)

    stmt = select(MembershipModel).where(
        MembershipModel.tenant_id == tenant_id,
        MembershipModel.user_id == uid,
        MembershipModel.deleted_at.is_(None),
    )
    membership = (await session.execute(stmt)).scalar_one_or_none()
    if membership is None:
        stmt_by_email = select(MembershipModel).where(
            MembershipModel.tenant_id == tenant_id,
            func.lower(MembershipModel.email) == normalized_email,
            MembershipModel.deleted_at.is_(None),
        )
        membership = (await session.execute(stmt_by_email)).scalar_one_or_none()

    if membership is None:
        membership = MembershipModel(
            id=uuid4(),
            tenant_id=tenant_id,
            user_id=uid,
            email=normalized_email,
            display_name=display_name,
            role=role,
            status="active",
            department_scope=department_scope,
            created_at=now,
            updated_at=now,
        )
        session.add(membership)
        await session.flush()
    else:
        membership.user_id = uid
        membership.email = normalized_email
        membership.display_name = display_name
        membership.role = role
        membership.status = "active"
        membership.department_scope = department_scope
        membership.updated_at = now
        await session.flush()

    return membership


async def seed_demo_accounts() -> None:
    settings = get_settings()
    if settings.app_env.strip().lower() == "production" or not settings.is_dev_auth_active:
        print(
            "Refusing to run demo account seed outside active development dev-auth mode "
            f"(app_env={settings.app_env!r}, is_dev_auth_active={settings.is_dev_auth_active}).",
            file=sys.stderr,
        )
        raise SystemExit(1)

    session_factory = get_session_factory()
    now = datetime.now(UTC)

    async with session_factory() as session:
        tenant = (
            await session.execute(
                select(TenantModel)
                .where(TenantModel.deleted_at.is_(None))
                .order_by(TenantModel.created_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if tenant is None:
            print("No active tenant found in database. Aborting seed.")
            return

        # 1. HR account (clean up any stray non-primary tenant memberships first)
        hr_uid = dev_user_id("hr@dragon.es")
        stray_hr_memberships = (
            await session.execute(
                select(MembershipModel).where(
                    MembershipModel.tenant_id != tenant.id,
                    (MembershipModel.user_id == hr_uid)
                    | (func.lower(MembershipModel.email) == "hr@dragon.es"),
                    MembershipModel.deleted_at.is_(None),
                )
            )
        ).scalars().all()
        for stray in stray_hr_memberships:
            stray.deleted_at = now
            stray.updated_at = now

        hr_membership = await _upsert_membership(
            session,
            tenant_id=tenant.id,
            email="hr@dragon.es",
            display_name="Elena Chen",
            role="hr",
            department_scope=None,
            now=now,
        )

        # 2. Store Manager account linked to ALL active stores
        manager_membership = await _upsert_membership(
            session,
            tenant_id=tenant.id,
            email="manager@dragon.es",
            display_name="Carlos Manager",
            role="store_manager",
            department_scope=None,
            now=now,
        )
        active_stores = list(
            (
                await session.execute(
                    select(StoreModel).where(
                        StoreModel.tenant_id == tenant.id,
                        StoreModel.is_active.is_(True),
                        StoreModel.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        existing_links = set(
            (
                await session.execute(
                    select(MembershipStoreModel.store_id).where(
                        MembershipStoreModel.membership_id == manager_membership.id,
                        MembershipStoreModel.tenant_id == tenant.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        linked_added = 0
        for store in active_stores:
            if store.id not in existing_links:
                session.add(
                    MembershipStoreModel(
                        membership_id=manager_membership.id,
                        store_id=store.id,
                        tenant_id=tenant.id,
                        created_at=now,
                    )
                )
                linked_added += 1

        # 3. Employee Ming Zhou + link EMP-002
        zhou_uid = dev_user_id("zhou@dragon.es")
        zhou_membership = await _upsert_membership(
            session,
            tenant_id=tenant.id,
            email="zhou@dragon.es",
            display_name="Ming Zhou",
            role="employee",
            department_scope=None,
            now=now,
        )
        emp_002 = (
            await session.execute(
                select(EmployeeModel).where(
                    EmployeeModel.tenant_id == tenant.id,
                    func.upper(EmployeeModel.employee_code) == "EMP-002",
                    EmployeeModel.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        emp_status_msg = "EMP-002 not found"
        if emp_002 is not None:
            emp_002.user_id = zhou_uid
            if not emp_002.department.strip():
                emp_002.department = "Sala"
            if not emp_002.job_title.strip():
                emp_002.job_title = "Camarero / 服务员"
            if emp_002.default_break_minutes is None:
                emp_002.default_break_minutes = 30
            emp_002.updated_at = now
            emp_status_msg = (
                f"EMP-002 linked to {zhou_uid} "
                f"(dept={emp_002.department!r}, job_title={emp_002.job_title!r})"
            )

        # 3b. Ensure HR Elena Chen has an active linked Employee record (EMP-003)
        if active_stores:
            emp_003 = (
                await session.execute(
                    select(EmployeeModel).where(
                        EmployeeModel.tenant_id == tenant.id,
                        func.upper(EmployeeModel.employee_code) == "EMP-003",
                        EmployeeModel.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if emp_003 is None:
                emp_003 = EmployeeModel(
                    id=uuid4(),
                    tenant_id=tenant.id,
                    primary_store_id=active_stores[0].id,
                    user_id=hr_uid,
                    employee_code="EMP-003",
                    full_name="Elena Chen",
                    preferred_name="林娜",
                    department="RR. HH.",
                    job_title="Responsable de RR. HH. / 人事主管",
                    email="hr@dragon.es",
                    phone="",
                    contract_type="indefinido",
                    work_schedule_type="full_time",
                    contracted_weekly_hours=Decimal("40.00"),
                    annual_vacation_days=30,
                    hire_date=date(2024, 1, 1),
                    employment_status="active",
                    created_at=now,
                    updated_at=now,
                )
                session.add(emp_003)
                await session.flush()
            else:
                emp_003.user_id = hr_uid
                emp_003.full_name = "Elena Chen"
                emp_003.preferred_name = "林娜"
                emp_003.department = "RR. HH."
                emp_003.job_title = "Responsable de RR. HH. / 人事主管"
                emp_003.email = "hr@dragon.es"
                emp_003.updated_at = now

            # 3c. Phase 5I: Carlos Manager (EMP-004, linked to manager@dragon.es)
            manager_uid = dev_user_id("manager@dragon.es")
            emp_004 = (
                await session.execute(
                    select(EmployeeModel).where(
                        EmployeeModel.tenant_id == tenant.id,
                        func.upper(EmployeeModel.employee_code) == "EMP-004",
                        EmployeeModel.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if emp_004 is None:
                emp_004 = EmployeeModel(
                    id=uuid4(),
                    tenant_id=tenant.id,
                    primary_store_id=active_stores[0].id,
                    user_id=manager_uid,
                    manager_id=emp_003.id,
                    employee_code="EMP-004",
                    full_name="Carlos Manager",
                    preferred_name="Carlos",
                    department="Operaciones",
                    job_title="Encargado de Tienda / 店长",
                    email="manager@dragon.es",
                    phone="",
                    contract_type="indefinido",
                    work_schedule_type="full_time",
                    contracted_weekly_hours=Decimal("40.00"),
                    annual_vacation_days=30,
                    hire_date=date(2024, 3, 1),
                    employment_status="active",
                    created_at=now,
                    updated_at=now,
                )
                session.add(emp_004)
                await session.flush()
            else:
                emp_004.user_id = manager_uid
                if emp_004.manager_id is None and emp_003.manager_id != emp_004.id:
                    emp_004.manager_id = emp_003.id
                emp_004.updated_at = now

            # Wire EMP-001 and EMP-002 under Carlos Manager only when manager_id is None
            emp_001 = (
                await session.execute(
                    select(EmployeeModel).where(
                        EmployeeModel.tenant_id == tenant.id,
                        func.upper(EmployeeModel.employee_code) == "EMP-001",
                        EmployeeModel.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if emp_001 is not None and emp_001.manager_id is None:
                emp_001.manager_id = emp_004.id
                emp_001.updated_at = now
            if emp_002 is not None and emp_002.manager_id is None:
                emp_002.manager_id = emp_004.id
                emp_002.updated_at = now

            # 3d. Seed 5 additional active store employees (EMP-005..EMP-009) under Carlos Manager
            # so Carlos has 6+ direct reports (testing the >5 compact grid)
            additional_reports = [
                (
                    "EMP-005",
                    "Lucía Fernández",
                    "Lucía",
                    "Sala",
                    "Camarera",
                    "lucia.fernandez@dragon.es",
                ),
                (
                    "EMP-006",
                    "Javier Morales",
                    "Javi",
                    "Cocina",
                    "Cocinero",
                    "javier.morales@dragon.es",
                ),
                (
                    "EMP-007",
                    "Sofía Wang",
                    "Sofía",
                    "Caja",
                    "Cajera",
                    "sofia.wang@dragon.es",
                ),
                (
                    "EMP-008",
                    "Pablo Ruiz",
                    "Pablo",
                    "Cocina",
                    "Ayudante de Cocina",
                    "pablo.ruiz@dragon.es",
                ),
                (
                    "EMP-009",
                    "Carmen Díaz",
                    "Carmen",
                    "Sala",
                    "Camarera",
                    "carmen.diaz@dragon.es",
                ),
            ]
            for idx, (code, full_name, preferred, dept, title, email) in enumerate(
                additional_reports
            ):
                store_id = active_stores[idx % len(active_stores)].id
                existing_emp = (
                    await session.execute(
                        select(EmployeeModel).where(
                            EmployeeModel.tenant_id == tenant.id,
                            func.upper(EmployeeModel.employee_code) == code,
                            EmployeeModel.deleted_at.is_(None),
                        )
                    )
                ).scalar_one_or_none()
                if existing_emp is None:
                    session.add(
                        EmployeeModel(
                            id=uuid4(),
                            tenant_id=tenant.id,
                            primary_store_id=store_id,
                            user_id=None,
                            manager_id=emp_004.id,
                            employee_code=code,
                            full_name=full_name,
                            preferred_name=preferred,
                            department=dept,
                            job_title=title,
                            email=email,
                            phone="",
                            contract_type="indefinido",
                            work_schedule_type="full_time",
                            contracted_weekly_hours=Decimal("40.00"),
                            annual_vacation_days=30,
                            hire_date=date(2025, 1, 15),
                            employment_status="active",
                            created_at=now,
                            updated_at=now,
                        )
                    )
                elif existing_emp.manager_id is None:
                    existing_emp.manager_id = emp_004.id
                    existing_emp.updated_at = now

            # 3e. Seed 1 standalone employee (EMP-010, manager_id=None, 0 reports)
            emp_010 = (
                await session.execute(
                    select(EmployeeModel).where(
                        EmployeeModel.tenant_id == tenant.id,
                        func.upper(EmployeeModel.employee_code) == "EMP-010",
                        EmployeeModel.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if emp_010 is None:
                session.add(
                    EmployeeModel(
                        id=uuid4(),
                        tenant_id=tenant.id,
                        primary_store_id=active_stores[0].id,
                        user_id=None,
                        manager_id=None,
                        employee_code="EMP-010",
                        full_name="Andrés Navarro",
                        preferred_name="Andrés",
                        department="Administración",
                        job_title="Asesor Externo",
                        email="andres.navarro@dragon.es",
                        phone="",
                        contract_type="indefinido",
                        work_schedule_type="part_time",
                        contracted_weekly_hours=Decimal("20.00"),
                        annual_vacation_days=30,
                        hire_date=date(2025, 6, 1),
                        employment_status="active",
                        created_at=now,
                        updated_at=now,
                    )
                )

        await session.commit()
        print(
            f"Seeded demo accounts on tenant {tenant.id} ({tenant.trade_name}):\n"
            f"  - hr@dragon.es: user_id={hr_membership.user_id}, role={hr_membership.role}\n"
            f"  - manager@dragon.es: user_id={manager_membership.user_id}, "
            f"stores={len(active_stores)} (+{linked_added} newly linked)\n"
            f"  - zhou@dragon.es: user_id={zhou_membership.user_id}, {emp_status_msg}"
        )


async def _main() -> None:
    try:
        await seed_demo_accounts()
    finally:
        await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(_main())

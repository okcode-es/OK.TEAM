"""Wave 1 / B5: Gestoria payroll CSV export with optional full NIE/DNI + NAF identifiers."""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.domain.employees import Employee, EmploymentStatus
from app.domain.organization import Membership
from app.domain.payroll import MonthlyClosing, MonthlyClosingItem, MonthlyClosingStatus
from app.domain.security import MembershipStatus, UserRole
from app.infrastructure.pii_crypto import AesGcmPiiCipher
from app.interfaces.api.deps import get_employee_repository, get_membership_repository
from tests.test_phase2_employees import TEST_PII_KEY
from tests.test_phase5e_workflows_and_reports import _build_env

_NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
_PLAIN_NIE = "12345678Z"
_PLAIN_NAF = "281234567890"
_MASKED_NIE = "****5678Z"


def _add_member(env: dict[str, Any], role: UserRole) -> UUID:
    repo = env["client"].app.dependency_overrides[get_membership_repository]()
    uid = uuid4()
    mid = uuid4()
    repo._state.memberships[mid] = Membership(
        id=mid,
        tenant_id=env["tenant_id"],
        user_id=uid,
        email=f"{role.value}-{str(uid)[:6]}@dragon.es",
        display_name=role.value,
        role=role,
        status=MembershipStatus.ACTIVE,
        store_ids=frozenset(),
        department_scope=None,
        created_at=_NOW,
        updated_at=_NOW,
    )
    return uid


def _item(env: dict[str, Any], closing: MonthlyClosing, emp: Employee) -> MonthlyClosingItem:
    return MonthlyClosingItem(
        id=uuid4(),
        tenant_id=env["tenant_id"],
        closing_id=closing.id,
        store_id=closing.store_id,
        period_month=closing.period_month,
        revision_number=closing.revision_number,
        employee_id=emp.id,
        employee_code=emp.employee_code,
        employee_full_name=emp.full_name,
        nie_dni_masked=emp.nie_dni_masked,
        contract_type=emp.contract_type,
        work_schedule_type=emp.work_schedule_type,
        contracted_weekly_hours=Decimal("40.00"),
        worked_days=20,
        total_net_hours=Decimal("160.00"),
        regular_hours=Decimal("160.00"),
        overtime_hours=Decimal("0.00"),
        annual_overtime_hours=Decimal("0.00"),
        total_break_minutes=600,
        vacation_days=0,
        sick_leave_days=0,
        paid_leave_days=0,
        unpaid_leave_days=0,
        warning_alerts_count=0,
        violation_alerts_count=0,
        attendance_report_document_id=None,
        retention_until=_NOW + timedelta(days=1460),
        created_at=_NOW,
    )


def _setup(
    tmp_path: Path,
    *,
    status: MonthlyClosingStatus = MonthlyClosingStatus.CLOSED,
) -> dict[str, Any]:
    env = _build_env(tmp_path)
    env["owner_id"] = _add_member(env, UserRole.OWNER)
    cipher = AesGcmPiiCipher(TEST_PII_KEY)
    tid = env["tenant_id"]
    emp_repo = env["client"].app.dependency_overrides[get_employee_repository]()

    # Hostile identifiers to verify CWE-1236 escaping of decrypted values
    evil = env["emp2"]
    evil_nie = '=HYPERLINK("http://x")'
    evil_naf = "+34123456789"
    evil = replace(
        evil,
        nie_dni_enc=cipher.encrypt(
            evil_nie, tenant_id=tid, employee_id=evil.id, field_name="nie_dni"
        ),
        naf_enc=cipher.encrypt(evil_naf, tenant_id=tid, employee_id=evil.id, field_name="naf"),
    )
    emp_repo.employees[evil.id] = evil
    env["evil"] = evil

    closing = MonthlyClosing(
        id=uuid4(),
        tenant_id=tid,
        store_id=env["store_a_id"],
        period_month="2026-10",
        status=status,
        revision_number=1,
        closed_by_user_id=env["hr_id"],
        closed_at_utc=_NOW,
        reopened_by_user_id=None,
        reopened_at_utc=None,
        reopen_reason="",
        notes="",
        retention_until=_NOW + timedelta(days=1460),
        created_at=_NOW,
        updated_at=_NOW,
    )
    env["payroll_repo"].closings.append(closing)
    env["payroll_repo"].items.extend([_item(env, closing, env["emp1"]), _item(env, closing, evil)])
    env["closing"] = closing
    env["emp_repo"] = emp_repo
    env["url"] = f"/api/v1/payroll/closings/{closing.id}/gestoria-export"
    return env


def _get(env: dict[str, Any], uid: UUID, full: bool | None):
    client: TestClient = env["client"]
    url = (
        env["url"] if full is None else f"{env['url']}?include_full_identifiers={str(full).lower()}"
    )
    return client.get(url, headers=env["headers"](uid))


def _rows(res: Any) -> tuple[list[str], list[list[str]]]:
    text = res.content.decode("utf-8")
    assert text.startswith("\ufeff")
    parsed = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    return parsed[0], parsed[1:]


@pytest.mark.parametrize("actor", ["owner_id", "hr_id", "acc_id"])
def test_full_identifiers_export_for_allowed_roles(tmp_path: Path, actor: str) -> None:
    env = _setup(tmp_path)
    res = _get(env, env[actor], True)
    assert res.status_code == 200, res.text
    assert res.headers["cache-control"] == "no-store"
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["content-type"].startswith("text/csv")

    header, rows = _rows(res)
    assert "NIE/DNI" in header
    assert "NAF (Nº Afiliación SS)" in header
    assert "NIE/DNI (Enmascarado)" not in header
    nie_idx = header.index("NIE/DNI")
    naf_idx = header.index("NAF (Nº Afiliación SS)")
    assert naf_idx == nie_idx + 1

    by_code = {r[header.index("Código Empleado")]: r for r in rows}
    assert by_code["EMP-001"][nie_idx] == _PLAIN_NIE
    assert by_code["EMP-001"][naf_idx] == _PLAIN_NAF
    assert _MASKED_NIE not in res.content.decode("utf-8")
    assert all(len(r) == len(header) for r in rows)


def test_masked_by_default_and_when_flag_false(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    for flag in (None, False):
        res = _get(env, env["hr_id"], flag)
        assert res.status_code == 200
        assert res.headers["cache-control"] == "no-store"
        header, rows = _rows(res)
        assert "NIE/DNI (Enmascarado)" in header
        assert "NIE/DNI" not in header
        assert not any("NAF" in h for h in header)
        text = res.content.decode("utf-8")
        assert _MASKED_NIE in text
        assert _PLAIN_NIE not in text
        assert _PLAIN_NAF not in text
        assert all(len(r) == len(header) for r in rows)


@pytest.mark.parametrize("actor", ["emp1_uid", "mgr_a_id"])
@pytest.mark.parametrize("flag", [None, False, True])
def test_employee_and_store_manager_get_403_strictly(
    tmp_path: Path, actor: str, flag: bool | None
) -> None:
    env = _setup(tmp_path)
    res = _get(env, env[actor], flag)
    assert res.status_code == 403
    body = res.text
    assert _PLAIN_NIE not in body
    assert _PLAIN_NAF not in body
    assert "text/csv" not in res.headers.get("content-type", "")


def test_unauthenticated_request_rejected(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    res = env["client"].get(f"{env['url']}?include_full_identifiers=true")
    assert res.status_code in (401, 403)


def test_decrypted_values_are_csv_injection_escaped(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    res = _get(env, env["owner_id"], True)
    header, rows = _rows(res)
    nie_idx = header.index("NIE/DNI")
    naf_idx = header.index("NAF (Nº Afiliación SS)")
    by_code = {r[header.index("Código Empleado")]: r for r in rows}
    evil_row = by_code["EMP-002"]
    assert evil_row[nie_idx].startswith("'=HYPERLINK(")
    assert evil_row[naf_idx] == "'+34123456789"
    # Existing escaping for other cells remains (hostile employee full name)
    assert evil_row[header.index("Nombre Completo")].startswith("'=CMD")
    # No cell in the sheet may start with a raw formula trigger character
    for row in rows:
        for cell in row:
            assert not cell.startswith(("=", "+", "@")), cell


def test_missing_or_anonymized_employee_yields_blank_identifiers(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    emp1: Employee = env["emp1"]
    env["emp_repo"].employees[emp1.id] = replace(
        emp1,
        employment_status=EmploymentStatus.ANONYMIZED,
        nie_dni_enc=None,
        naf_enc=None,
    )
    evil: Employee = env["evil"]
    del env["emp_repo"].employees[evil.id]

    res = _get(env, env["hr_id"], True)
    assert res.status_code == 200
    header, rows = _rows(res)
    nie_idx = header.index("NIE/DNI")
    naf_idx = header.index("NAF (Nº Afiliación SS)")
    for row in rows:
        assert row[nie_idx] == ""
        assert row[naf_idx] == ""


def test_employee_without_naf_ciphertext_yields_blank_naf(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    emp1: Employee = env["emp1"]
    env["emp_repo"].employees[emp1.id] = replace(emp1, naf_enc=None)
    res = _get(env, env["acc_id"], True)
    header, rows = _rows(res)
    row = next(r for r in rows if r[header.index("Código Empleado")] == "EMP-001")
    assert row[header.index("NIE/DNI")] == _PLAIN_NIE
    assert row[header.index("NAF (Nº Afiliación SS)")] == ""


def test_full_export_requires_closed_status_409(tmp_path: Path) -> None:
    env = _setup(tmp_path, status=MonthlyClosingStatus.REOPENED)
    res = _get(env, env["owner_id"], True)
    assert res.status_code == 409
    assert _PLAIN_NIE not in res.text


def test_unknown_closing_404(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    client: TestClient = env["client"]
    res = client.get(
        f"/api/v1/payroll/closings/{uuid4()}/gestoria-export?include_full_identifiers=true",
        headers=env["headers"](env["owner_id"]),
    )
    assert res.status_code == 404


def test_invalid_flag_value_is_422(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    client: TestClient = env["client"]
    res = client.get(
        f"{env['url']}?include_full_identifiers=maybe",
        headers=env["headers"](env["owner_id"]),
    )
    assert res.status_code == 422


def test_decrypted_pii_never_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    env = _setup(tmp_path)
    with caplog.at_level(logging.DEBUG):
        res = _get(env, env["owner_id"], True)
    assert res.status_code == 200
    log_text = caplog.text
    assert _PLAIN_NIE not in log_text
    assert _PLAIN_NAF not in log_text
    assert "HYPERLINK" not in log_text


def test_filename_and_disposition_unchanged_with_full_identifiers(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    res = _get(env, env["owner_id"], True)
    disp = res.headers["content-disposition"]
    assert disp.startswith("attachment;")
    assert "gestoria_nomina_MAD-01_2026-10_v1.csv" in disp

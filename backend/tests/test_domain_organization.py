from __future__ import annotations

from uuid import uuid4

import pytest

from app.domain.organization import (
    ValidationDomainError,
    normalize_cif_nif,
    normalize_store_code,
    validate_iana_timezone,
    validate_locale,
)
from app.domain.security import AuthenticatedPrincipal, ForbiddenScopeError, UserRole


def test_iana_timezone_validation_accepts_spain_zones_and_rejects_invalid() -> None:
    assert validate_iana_timezone("Europe/Madrid") == "Europe/Madrid"
    assert validate_iana_timezone("Atlantic/Canary") == "Atlantic/Canary"

    with pytest.raises(ValidationDomainError):
        validate_iana_timezone("Spain/NotReal")

    with pytest.raises(ValidationDomainError):
        validate_iana_timezone("   ")


def test_cif_nif_and_store_code_normalization() -> None:
    assert normalize_cif_nif(" b1234 5678 ") == "B12345678"
    assert normalize_store_code(" mad 01 ") == "MAD-01"

    with pytest.raises(ValidationDomainError):
        normalize_cif_nif("123")

    with pytest.raises(ValidationDomainError):
        normalize_store_code("MAD@01!")


def test_locale_validation() -> None:
    assert validate_locale("zh-CN") == "zh-CN"
    assert validate_locale("es-ES") == "es-ES"
    with pytest.raises(ValidationDomainError):
        validate_locale("fr-FR")


def test_rbac_principal_predicates() -> None:
    tenant_id = uuid4()
    store_a = uuid4()
    store_b = uuid4()

    owner = AuthenticatedPrincipal(uuid4(), tenant_id, UserRole.OWNER, frozenset())
    hr = AuthenticatedPrincipal(uuid4(), tenant_id, UserRole.HR, frozenset())
    manager = AuthenticatedPrincipal(
        uuid4(),
        tenant_id,
        UserRole.STORE_MANAGER,
        frozenset({store_a}),
    )
    accountant = AuthenticatedPrincipal(uuid4(), tenant_id, UserRole.ACCOUNTANT, frozenset())
    employee = AuthenticatedPrincipal(
        uuid4(),
        tenant_id,
        UserRole.EMPLOYEE,
        frozenset({store_a}),
    )

    assert owner.can_update_tenant() is True
    assert hr.can_update_tenant() is False
    assert hr.can_manage_stores() is True
    assert accountant.can_manage_stores() is False
    assert accountant.can_access_store(tenant_id, store_b) is True

    assert manager.can_access_store(tenant_id, store_a) is True
    assert manager.can_access_store(tenant_id, store_b) is False

    # Employee is strictly limited to assigned stores and cannot mutate org/stores/members
    assert employee.can_access_store(tenant_id, store_a) is True
    assert employee.can_access_store(tenant_id, store_b) is False
    assert employee.can_update_tenant() is False
    assert employee.can_manage_stores() is False
    assert employee.can_manage_members() is False
    with pytest.raises(ForbiddenScopeError):
        employee.require_store_access(tenant_id, store_b)
    with pytest.raises(ForbiddenScopeError):
        employee.require_member_manage()

    # HR cannot assign or modify OWNER
    hr.require_member_manage(target_role=UserRole.STORE_MANAGER)
    with pytest.raises(ForbiddenScopeError):
        hr.require_member_manage(target_role=UserRole.OWNER)

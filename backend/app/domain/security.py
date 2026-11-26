"""Domain security primitives: roles, verified identity, tenant principal, and scope guards.

Pure Python standard library only (enforced by scripts/check_domain_purity.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class UserRole(StrEnum):
    """Canonical Day-1 RBAC roles for okTeam."""

    OWNER = "owner"
    HR = "hr"
    STORE_MANAGER = "store_manager"
    ACCOUNTANT = "accountant"
    EMPLOYEE = "employee"


class MembershipStatus(StrEnum):
    """Lifecycle states of a tenant membership."""

    ACTIVE = "active"
    INVITED = "invited"
    DISABLED = "disabled"


TENANT_WIDE_ROLES: frozenset[UserRole] = frozenset(
    {
        UserRole.OWNER,
        UserRole.HR,
        UserRole.ACCOUNTANT,
    }
)

ORG_WRITE_ROLES: frozenset[UserRole] = frozenset({UserRole.OWNER})
ADMIN_ROLES: frozenset[UserRole] = frozenset({UserRole.OWNER, UserRole.HR})


class DomainAuthError(Exception):
    """Base exception for domain authentication/authorization violations."""


class TokenVerificationError(DomainAuthError):
    """Raised when an authentication token is missing, invalid, or lacks required claims."""


class ForbiddenScopeError(DomainAuthError):
    """Raised when a principal attempts an action or accesses a tenant/store outside its scope."""


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    """Verified user identity extracted purely from Supabase Auth JWT (no tenant scope yet)."""

    user_id: UUID
    email: str | None = None


class EmployeeScopeTarget(Protocol):
    """Minimal employee projection needed for store + department scope checks."""

    @property
    def primary_store_id(self) -> UUID: ...

    @property
    def department(self) -> str: ...

    @property
    def employment_status(self) -> str: ...

    @property
    def deleted_at(self) -> datetime | None: ...

    @property
    def user_id(self) -> UUID | None: ...


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Verified identity bound to a live DB-backed tenant membership and store scope."""

    user_id: UUID
    tenant_id: UUID
    role: UserRole
    store_ids: frozenset[UUID]
    email: str | None = None
    department_scope: str | None = None

    def can_manage_employee(self, employee: EmployeeScopeTarget) -> bool:
        """Unified store + department + legal_hold scope check for operational access."""
        if self.role in TENANT_WIDE_ROLES:
            return True
        if self.role == UserRole.STORE_MANAGER:
            if employee.deleted_at is not None:
                return False
            if str(employee.employment_status) in ("legal_hold", "anonymized"):
                return False
            if employee.primary_store_id not in self.store_ids:
                return False
            if self.department_scope and self.department_scope.strip():
                return (
                    employee.department.strip().casefold()
                    == self.department_scope.strip().casefold()
                )
            return True
        return employee.user_id is not None and employee.user_id == self.user_id

    def require_employee_in_scope(self, employee: EmployeeScopeTarget) -> None:
        if not self.can_manage_employee(employee):
            raise ForbiddenScopeError(
                "Employee is outside your authorized store, department, or legal_hold scope"
            )

    def can_access_tenant(self, tenant_id: UUID) -> bool:
        return self.tenant_id == tenant_id

    def can_access_store(self, tenant_id: UUID, store_id: UUID) -> bool:
        if not self.can_access_tenant(tenant_id):
            return False
        if self.role in TENANT_WIDE_ROLES:
            return True
        return store_id in self.store_ids

    def require_store_access(self, tenant_id: UUID, store_id: UUID) -> None:
        if not self.can_access_store(tenant_id, store_id):
            raise ForbiddenScopeError(
                f"Principal {self.user_id} lacks access to store {store_id} in tenant {tenant_id}"
            )

    def can_update_tenant(self) -> bool:
        return self.role in ORG_WRITE_ROLES

    def require_tenant_write(self) -> None:
        if not self.can_update_tenant():
            raise ForbiddenScopeError("Only tenant owners may update company settings")

    def can_manage_stores(self) -> bool:
        return self.role in ADMIN_ROLES

    def require_store_manage(self) -> None:
        if not self.can_manage_stores():
            raise ForbiddenScopeError("Only owners or HR may create, edit, or archive stores")

    def can_manage_members(self) -> bool:
        return self.role in ADMIN_ROLES

    def require_member_manage(self, *, target_role: UserRole | None = None) -> None:
        if not self.can_manage_members():
            raise ForbiddenScopeError("Only owners or HR may manage tenant memberships")
        if self.role == UserRole.HR and target_role == UserRole.OWNER:
            raise ForbiddenScopeError("HR cannot assign or modify the owner role")

    def can_manage_employees(self) -> bool:
        return self.role in ADMIN_ROLES

    def require_employee_manage(self) -> None:
        if not self.can_manage_employees():
            raise ForbiddenScopeError("Only owners or HR may create, update, or freeze employees")

    def can_view_sensitive_pii(self, *, target_user_id: UUID | None) -> bool:
        # GDPR Data Minimization: store_manager NEVER decrypts NIE/IBAN/NAF.
        if self.role in TENANT_WIDE_ROLES:
            return True
        if self.role == UserRole.EMPLOYEE and target_user_id is not None:
            return self.user_id == target_user_id
        return False

    def require_sensitive_pii_access(self, *, target_user_id: UUID | None) -> None:
        if not self.can_view_sensitive_pii(target_user_id=target_user_id):
            raise ForbiddenScopeError(
                "Current role is not authorized to decrypt employee sensitive PII"
            )

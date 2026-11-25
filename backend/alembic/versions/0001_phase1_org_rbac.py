"""Phase 1: tenants, stores, memberships, and membership_stores in public schema.

Revision ID: 0001_phase1_org_rbac
Revises:
Create Date: 2026-11-25 09:10:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_phase1_org_rbac"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("legal_name", sa.String(length=200), nullable=False),
        sa.Column("trade_name", sa.String(length=200), nullable=False),
        sa.Column("cif_nif", sa.String(length=32), nullable=False),
        sa.Column(
            "default_timezone",
            sa.String(length=64),
            server_default=sa.text("'Europe/Madrid'"),
            nullable=False,
        ),
        sa.Column(
            "default_locale",
            sa.String(length=10),
            server_default=sa.text("'zh-CN'"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
    )
    op.create_index(
        "uq_tenants_cif_nif_active",
        "tenants",
        [sa.text("upper(cif_nif)")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "stores",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column(
            "address",
            sa.String(length=300),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column(
            "city",
            sa.String(length=100),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column(
            "postal_code",
            sa.String(length=16),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_stores_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stores"),
    )
    op.create_index("ix_stores_tenant_id", "stores", ["tenant_id"], unique=False)
    op.create_index(
        "uq_stores_tenant_code_active",
        "stores",
        ["tenant_id", sa.text("upper(code)")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "role IN ('owner', 'hr', 'store_manager', 'accountant', 'employee')",
            name="ck_memberships_memberships_role_valid",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'invited', 'disabled')",
            name="ck_memberships_memberships_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_memberships_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_memberships"),
    )
    op.create_index("ix_memberships_tenant_id", "memberships", ["tenant_id"], unique=False)
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"], unique=False)
    op.create_index(
        "uq_memberships_tenant_user_active",
        "memberships",
        ["tenant_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "membership_stores",
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["membership_id"],
            ["memberships.id"],
            name="fk_membership_stores_membership_id_memberships",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name="fk_membership_stores_store_id_stores",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_membership_stores_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("membership_id", "store_id", name="pk_membership_stores"),
    )
    op.create_index(
        "ix_membership_stores_tenant_store",
        "membership_stores",
        ["tenant_id", "store_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_membership_stores_tenant_store", table_name="membership_stores")
    op.drop_table("membership_stores")

    op.drop_index("uq_memberships_tenant_user_active", table_name="memberships")
    op.drop_index("ix_memberships_user_id", table_name="memberships")
    op.drop_index("ix_memberships_tenant_id", table_name="memberships")
    op.drop_table("memberships")

    op.drop_index("uq_stores_tenant_code_active", table_name="stores")
    op.drop_index("ix_stores_tenant_id", table_name="stores")
    op.drop_table("stores")

    op.drop_index("uq_tenants_cif_nif_active", table_name="tenants")
    op.drop_table("tenants")

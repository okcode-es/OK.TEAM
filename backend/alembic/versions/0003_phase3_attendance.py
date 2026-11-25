"""Phase 3: append-only attendance_events table with PostgreSQL immutability trigger.

Revision ID: 0003_phase3_attendance
Revises: 0002_phase2_employees
Create Date: 2026-11-25 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_phase3_attendance"
down_revision: str | Sequence[str] | None = "0002_phase2_employees"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "attendance_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=20), nullable=False),
        sa.Column("occurred_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("store_timezone", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("recorded_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recorded_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supersedes_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("corrected_event_type", sa.String(length=20), nullable=True),
        sa.Column(
            "correction_reason",
            sa.String(length=300),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('clock_in', 'break_start', 'break_end', 'clock_out', 'correction')",
            name="ck_attendance_events_event_type_valid",
        ),
        sa.CheckConstraint(
            "source IN ('web', 'kiosk', 'manager_correction')",
            name="ck_attendance_events_source_valid",
        ),
        sa.CheckConstraint(
            "corrected_event_type IS NULL OR corrected_event_type IN "
            "('clock_in', 'break_start', 'break_end', 'clock_out', 'void')",
            name="ck_attendance_events_corrected_type_valid",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_attendance_events_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name="fk_attendance_events_store_id_stores",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name="fk_attendance_events_employee_id_employees",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_event_id"],
            ["attendance_events.id"],
            name="fk_attendance_events_supersedes_event_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_attendance_events"),
    )

    op.create_index(
        "ix_attendance_events_tenant_emp_time",
        "attendance_events",
        ["tenant_id", "employee_id", "occurred_at_utc"],
        unique=False,
    )
    op.create_index(
        "ix_attendance_events_tenant_store_time",
        "attendance_events",
        ["tenant_id", "store_id", "occurred_at_utc"],
        unique=False,
    )
    op.create_index(
        "ix_attendance_events_supersedes",
        "attendance_events",
        ["supersedes_event_id"],
        unique=False,
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_attendance_event_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'attendance_events is append-only (RDL 8/2019): UPDATE and DELETE are forbidden';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_attendance_events_immutable
        BEFORE UPDATE OR DELETE ON attendance_events
        FOR EACH ROW
        EXECUTE FUNCTION prevent_attendance_event_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_attendance_events_immutable ON attendance_events;")
    op.execute("DROP FUNCTION IF EXISTS prevent_attendance_event_mutation();")
    op.drop_index("ix_attendance_events_supersedes", table_name="attendance_events")
    op.drop_index("ix_attendance_events_tenant_store_time", table_name="attendance_events")
    op.drop_index("ix_attendance_events_tenant_emp_time", table_name="attendance_events")
    op.drop_table("attendance_events")

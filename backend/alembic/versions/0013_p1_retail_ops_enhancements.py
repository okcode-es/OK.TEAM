"""P1 retail ops: work permit / contract expiry dates and kiosk PIN hash on employees.

Adds three nullable columns to ``employees`` (public schema only):
- ``work_permit_expiry_date`` (NIE/TIE residence permit expiry)
- ``contract_end_date`` (temporary contract end)
- ``kiosk_pin_hash`` (salted PBKDF2-SHA256 hash; plaintext PIN is never stored)

Revision ID: 0013_p1_retail_ops_enhancements
Revises: 0012_employee_manager_id
Create Date: 2026-11-27 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_p1_retail_ops_enhancements"
down_revision: str | Sequence[str] | None = "0012_employee_manager_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("employees", sa.Column("work_permit_expiry_date", sa.Date(), nullable=True))
    op.add_column("employees", sa.Column("contract_end_date", sa.Date(), nullable=True))
    op.add_column("employees", sa.Column("kiosk_pin_hash", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("employees", "kiosk_pin_hash")
    op.drop_column("employees", "contract_end_date")
    op.drop_column("employees", "work_permit_expiry_date")

"""Pure domain models, CSV (BOM + formula-injection safe), and A4 %PDF-1.4 report generators.

Pure Python standard library only (enforced by scripts/check_domain_purity.py).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")

_PAGE_WIDTH = 595.28
_PAGE_HEIGHT = 841.89
_MARGIN_LEFT = 36.0
_MARGIN_RIGHT = 559.28
_BOTTOM_MARGIN = 52.0


@dataclass(frozen=True, slots=True)
class StoreReportSummary:
    store_id: UUID
    store_name: str
    active_employees: int
    worked_hours: Decimal
    overtime_hours: Decimal
    compliance_alerts: int
    pending_leaves: int


@dataclass(frozen=True, slots=True)
class ExecutiveSummary:
    from_date: date
    to_date: date
    total_active_employees: int
    full_time_count: int
    part_time_count: int
    contract_breakdown: dict[str, int]
    total_worked_hours: Decimal
    total_regular_hours: Decimal
    total_overtime_hours: Decimal
    total_compliance_alerts: int
    pending_manager_leaves: int
    pending_hr_leaves: int
    approved_vacation_days: int
    approved_sick_days: int | None
    announcements_pending_ack_count: int
    by_store: tuple[StoreReportSummary, ...]


@dataclass(frozen=True, slots=True)
class AttendanceSummaryRow:
    employee_code: str
    full_name: str
    department: str
    store_name: str
    worked_days: int
    regular_hours: Decimal
    overtime_hours: Decimal
    break_minutes: int
    target_hours: Decimal
    saldo_hours: Decimal
    compliance_alerts: int


@dataclass(frozen=True, slots=True)
class LeaveSummaryRow:
    employee_code: str
    full_name: str
    department: str
    store_name: str
    entitled_vacation_days: int
    used_vacation_days: int
    pending_vacation_days: int
    remaining_vacation_days: int
    sick_days: int | None
    medical_appointment_days: int | None
    other_absence_days: int


@dataclass(frozen=True, slots=True)
class HeadcountRosterRow:
    employee_code: str
    full_name: str
    job_title: str
    department: str
    store_name: str
    hire_date: date
    tenure_months: int
    contract_type: str
    schedule_type: str
    weekly_hours: Decimal
    status: str


def escape_csv_cell(value: object) -> str:
    """Convert cell to string and neutralize Excel/Calc formula injection (=, +, -, @, WS)."""
    text = "" if value is None else str(value)
    if text and (
        text.startswith(_FORMULA_PREFIXES)
        or text.lstrip(" \t\r\n").startswith(_FORMULA_PREFIXES)
    ):
        return "'" + text
    return text


def render_csv_utf8_bom(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> bytes:
    """Render RFC 4180 CSV with UTF-8 BOM (utf-8-sig) and formula-injection escaping."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow([escape_csv_cell(h) for h in headers])
    for row in rows:
        writer.writerow([escape_csv_cell(cell) for cell in row])
    return buf.getvalue().encode("utf-8-sig")


def _to_winansi_bytes(text: str) -> bytes:
    out = bytearray()
    for ch in text:
        if ch in ("\r", "\n", "\t"):
            out.append(0x20)
            continue
        code = ord(ch)
        if code < 0x20 or code == 0x7F:
            continue
        if ch == "\u20ac":
            out.append(0x80)
            continue
        try:
            out.extend(ch.encode("cp1252"))
        except UnicodeEncodeError:
            out.extend(b"?")
    return bytes(out)


def _pdf_literal(text: str) -> bytes:
    raw = _to_winansi_bytes(text)
    escaped = bytearray()
    for b in raw:
        if b in (0x5C, 0x28, 0x29):
            escaped.append(0x5C)
        escaped.append(b)
    return b"(" + bytes(escaped) + b")"


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max(1, max_chars - 3)] + "..."


class _PdfPage:
    def __init__(self) -> None:
        self.commands: list[bytes] = []

    def text(self, x: float, y: float, value: str, *, font: str = "F1", size: float = 8.0) -> None:
        cmd = (
            f"BT /{font} {size:.2f} Tf {x:.2f} {y:.2f} Td ".encode("ascii")
            + _pdf_literal(value)
            + b" Tj ET"
        )
        self.commands.append(cmd)

    def hline(self, y: float, *, x1: float = _MARGIN_LEFT, x2: float = _MARGIN_RIGHT) -> None:
        self.commands.append(f"0.50 w {x1:.2f} {y:.2f} m {x2:.2f} {y:.2f} l S".encode("ascii"))

    def stream_bytes(self) -> bytes:
        return b"\n".join(self.commands) + b"\n"


def render_table_pdf(
    *,
    title: str,
    subtitle: str,
    columns: Sequence[tuple[str, float, int]],
    rows: Sequence[Sequence[object]],
) -> bytes:
    """Render a zero-dependency A4 %PDF-1.4 tabular report.

    columns: sequence of (header_label, x_offset_pt, max_chars)
    """
    pages: list[_PdfPage] = []

    def _new_page(page_num: int) -> tuple[_PdfPage, float]:
        p = _PdfPage()
        y = 804.0
        p.text(_MARGIN_LEFT, y, title, font="F2", size=12.0)
        y -= 15.0
        p.text(_MARGIN_LEFT, y, subtitle, font="F1", size=8.5)
        y -= 10.0
        p.hline(y)
        y -= 14.0
        for label, col_x, max_chars in columns:
            p.text(col_x, y, _truncate(label, max_chars), font="F2", size=7.5)
        y -= 6.0
        p.hline(y)
        y -= 13.0
        p.text(_MARGIN_RIGHT - 45.0, 28.0, f"Pag. {page_num}", font="F1", size=7.5)
        pages.append(p)
        return p, y

    page, y = _new_page(1)
    for row in rows:
        if y < _BOTTOM_MARGIN:
            page, y = _new_page(len(pages) + 1)
        for (_, col_x, max_chars), cell in zip(columns, row, strict=False):
            page.text(col_x, y, _truncate(str(cell), max_chars), font="F1", size=7.5)
        y -= 13.0

    objects: dict[int, bytes] = {}
    page_ids: list[int] = []
    objects[3] = (
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>"
    )
    objects[4] = (
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
        b"/Encoding /WinAnsiEncoding >>"
    )
    for idx, p in enumerate(pages):
        page_obj_id = 5 + idx * 2
        content_obj_id = 6 + idx * 2
        page_ids.append(page_obj_id)
        stream = p.stream_bytes()
        objects[content_obj_id] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"endstream"
        )
        objects[page_obj_id] = (
            f"<< /Type /Page /Parent 2 0 R "
            f"/MediaBox [0 0 {_PAGE_WIDTH:.2f} {_PAGE_HEIGHT:.2f}] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
            f"/Contents {content_obj_id} 0 R >>"
        ).encode("ascii")

    kids_str = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[2] = f"<< /Type /Pages /Kids [{kids_str}] /Count {len(page_ids)} >>".encode("ascii")
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"

    total_objs = max(objects.keys())
    buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for obj_id in range(1, total_objs + 1):
        offsets[obj_id] = len(buf)
        buf.extend(f"{obj_id} 0 obj\n".encode("ascii"))
        buf.extend(objects[obj_id])
        buf.extend(b"\nendobj\n")

    xref_offset = len(buf)
    buf.extend(f"xref\n0 {total_objs + 1}\n".encode("ascii"))
    buf.extend(b"0000000000 65535 f \n")
    for obj_id in range(1, total_objs + 1):
        buf.extend(f"{offsets[obj_id]:010d} 00000 n \n".encode("ascii"))

    buf.extend(
        (
            f"trailer\n<< /Size {total_objs + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(buf)

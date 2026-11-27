"""A4 es-ES PDF generators for RDL 8/2019 / Art. 34.9 ET Monthly Attendance Reports.

Includes:
- ReportLabAttendancePdfRenderer: Unicode/CJK-capable A4 renderer using ReportLab with
  automatic Linux/Windows CJK font discovery and symmetric paper signature lines.
- PurePythonAttendancePdfRenderer: Legacy zero-dependency WinAnsi renderer kept for backwards
  compatibility.
"""

from __future__ import annotations

import io
import os
import threading
from collections.abc import Sequence
from datetime import UTC
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from app.domain.payroll import AttendancePdfDocumentInput

_PAGE_WIDTH = 595.28
_PAGE_HEIGHT = 841.89
_MARGIN_LEFT = 42.0
_MARGIN_RIGHT = 553.28
_BOTTOM_MARGIN = 62.0

_CONTRACT_LABELS_ES: dict[str, str] = {
    "indefinido": "Indefinido",
    "temporal": "Temporal",
    "fijo_discontinuo": "Fijo Discontinuo",
    "practicas": "Practicas",
}

_SCHEDULE_LABELS_ES: dict[str, str] = {
    "full_time": "Tiempo Completo",
    "part_time": "Tiempo Parcial",
}


def _to_winansi_bytes(text: str) -> bytes:
    """Encode text to WinAnsi (cp1252) after stripping control chars with safe ASCII fallback."""
    out = bytearray()
    for ch in text:
        if ch in ("\r", "\n", "\t"):
            out.append(0x20)
            continue
        code = ord(ch)
        if code < 0x20 or code == 0x7F:
            continue
        if ch == "\u20ac":  # Euro sign (0x80 in cp1252)
            out.append(0x80)
            continue
        try:
            encoded = ch.encode("cp1252")
            out.extend(encoded)
        except UnicodeEncodeError:
            out.extend(b"?")
    return bytes(out)


def _pdf_literal(text: str) -> bytes:
    """Escape PDF literal string bytes for WinAnsiEncoding."""
    raw = _to_winansi_bytes(text)
    escaped = bytearray()
    for b in raw:
        if b in (0x5C, 0x28, 0x29):  # \, (, )
            escaped.append(0x5C)
        escaped.append(b)
    return b"(" + bytes(escaped) + b")"


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max(1, max_chars - 3)] + "..."


class _PageBuilder:
    def __init__(self) -> None:
        self.commands: list[bytes] = []

    def text(
        self,
        x: float,
        y: float,
        value: str,
        *,
        font: str = "F1",
        size: float = 9.0,
    ) -> None:
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


def _build_pdf_document(pages: list[_PageBuilder]) -> bytes:
    """Assemble valid %PDF-1.4 binary with Font resources and exact xref table."""
    # Object numbering:
    # 1: Catalog
    # 2: Pages
    # 3: Font F1 (Helvetica /WinAnsiEncoding)
    # 4: Font F2 (Helvetica-Bold /WinAnsiEncoding)
    # 5 + 2*i: Page i
    # 6 + 2*i: Content stream i
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

    for idx, page in enumerate(pages):
        page_obj_id = 5 + idx * 2
        content_obj_id = 6 + idx * 2
        page_ids.append(page_obj_id)
        stream = page.stream_bytes()
        objects[content_obj_id] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"endstream"
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
    buf = bytearray()
    buf.extend(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

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


class PurePythonAttendancePdfRenderer:
    """Pure Python stdlib A4 es-ES RDL 8/2019 Monthly Attendance Report PDF renderer."""

    def render_monthly_attendance_pdf(self, data: AttendancePdfDocumentInput) -> bytes:
        pages: list[_PageBuilder] = []
        page = _PageBuilder()
        pages.append(page)

        def _draw_header(p: _PageBuilder, page_num: int) -> float:
            y_pos = 800.0
            p.text(
                _MARGIN_LEFT,
                y_pos,
                "RESUMEN MENSUAL DE REGISTRO DIARIO DE JORNADA (RDL 8/2019)",
                font="F2",
                size=11.5,
            )
            p.text(
                470.0,
                y_pos,
                f"Periodo: {data.period_month} (v{data.revision_number})",
                font="F2",
                size=9.0,
            )
            y_pos -= 14.0
            p.hline(y_pos)
            y_pos -= 14.0
            p.text(
                _MARGIN_LEFT,
                y_pos,
                f"Empresa: {_truncate(data.tenant_legal_name, 48)} | CIF: {data.tenant_cif}",
                font="F1",
                size=8.5,
            )
            p.text(495.0, y_pos, f"Pag. {page_num}", font="F1", size=8.5)
            y_pos -= 12.0
            p.text(
                _MARGIN_LEFT,
                y_pos,
                f"Centro: {data.store_code} - {_truncate(data.store_name, 38)} | "
                f"Zona Horaria: {data.store_timezone}",
                font="F1",
                size=8.5,
            )
            y_pos -= 14.0
            return y_pos

        y = _draw_header(page, len(pages))

        # Employee block on first page
        contract_label = _CONTRACT_LABELS_ES.get(data.contract_type.value, data.contract_type.value)
        schedule_label = _SCHEDULE_LABELS_ES.get(
            data.work_schedule_type.value, data.work_schedule_type.value
        )
        nie_display = data.nie_dni_masked or "No registrado"
        page.text(
            _MARGIN_LEFT,
            y,
            f"Trabajador: {_truncate(data.employee_full_name, 42)} ({data.employee_code})",
            font="F2",
            size=9.0,
        )
        page.text(370.0, y, f"NIE/DNI: {nie_display}", font="F1", size=9.0)
        y -= 12.0
        page.text(
            _MARGIN_LEFT,
            y,
            f"Contrato: {contract_label} | Jornada: {schedule_label} | "
            f"Horas/Sem: {data.contracted_weekly_hours:.2f}h",
            font="F1",
            size=8.5,
        )
        y -= 12.0
        page.hline(y)
        y -= 16.0

        def _draw_table_header(p: _PageBuilder, y_pos: float) -> float:
            p.text(_MARGIN_LEFT, y_pos, "Fecha", font="F2", size=8.5)
            p.text(112.0, y_pos, "Entrada", font="F2", size=8.5)
            p.text(178.0, y_pos, "Salida", font="F2", size=8.5)
            p.text(244.0, y_pos, "Pausa (min)", font="F2", size=8.5)
            p.text(314.0, y_pos, "Horas Netas", font="F2", size=8.5)
            p.text(386.0, y_pos, "Corr.", font="F2", size=8.5)
            p.text(430.0, y_pos, "Notas", font="F2", size=8.5)
            y_pos -= 5.0
            p.hline(y_pos)
            return y_pos - 13.0

        y = _draw_table_header(page, y)

        if not data.daily_rows:
            page.text(
                _MARGIN_LEFT,
                y,
                "Sin registros diarios de jornada en el periodo.",
                font="F1",
                size=8.5,
            )
            y -= 16.0
        else:
            for row in data.daily_rows:
                if y < _BOTTOM_MARGIN + 20.0:
                    page = _PageBuilder()
                    pages.append(page)
                    y = _draw_header(page, len(pages))
                    y = _draw_table_header(page, y)

                page.text(_MARGIN_LEFT, y, row.local_date.isoformat(), font="F1", size=8.5)
                page.text(112.0, y, row.clock_in_local, font="F1", size=8.5)
                page.text(178.0, y, row.clock_out_local, font="F1", size=8.5)
                page.text(255.0, y, str(row.break_minutes), font="F1", size=8.5)
                page.text(320.0, y, f"{row.net_hours:.2f}h", font="F1", size=8.5)
                page.text(390.0, y, "Si" if row.has_corrections else "No", font="F1", size=8.5)
                page.text(430.0, y, _truncate(row.notes, 24), font="F1", size=8.5)
                y -= 13.5

        # Summary & signature footer block (~110pt needed)
        if y < _BOTTOM_MARGIN + 115.0:
            page = _PageBuilder()
            pages.append(page)
            y = _draw_header(page, len(pages))

        y -= 6.0
        page.hline(y)
        y -= 15.0
        page.text(_MARGIN_LEFT, y, "RESUMEN MENSUAL Y AUSENCIAS", font="F2", size=9.0)
        y -= 13.0
        page.text(
            _MARGIN_LEFT,
            y,
            f"Dias Trabajados: {data.worked_days} | Horas Netas: {data.total_net_hours:.2f}h | "
            f"Ordinarias: {data.regular_hours:.2f}h | Extra: {data.overtime_hours:.2f}h",
            font="F1",
            size=8.5,
        )
        y -= 12.0
        if data.redact_health:
            other_absences = data.sick_leave_days + data.paid_leave_days + data.unpaid_leave_days
            absence_line = (
                f"Pausa Total: {data.total_break_minutes} min | "
                f"Vacaciones: {data.vacation_days}d | Otras Ausencias: {other_absences}d"
            )
        else:
            absence_line = (
                f"Pausa Total: {data.total_break_minutes} min | "
                f"Vacaciones: {data.vacation_days}d | "
                f"Baja Medica (IT): {data.sick_leave_days}d | "
                f"Perm. Retrib.: {data.paid_leave_days}d | No Retrib.: {data.unpaid_leave_days}d"
            )
        page.text(
            _MARGIN_LEFT,
            y,
            absence_line,
            font="F1",
            size=8.5,
        )
        y -= 12.0
        page.text(
            _MARGIN_LEFT,
            y,
            f"Alertas Legales -> Avisos: {data.warning_alerts_count} | "
            f"Infracciones: {data.violation_alerts_count} | "
            f"Extra Anual Acumulada: {data.annual_overtime_hours:.2f}h",
            font="F1",
            size=8.5,
        )
        y -= 16.0
        page.hline(y)
        y -= 13.0
        gen_ts = data.generated_at_utc.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
        page.text(
            _MARGIN_LEFT,
            y,
            "ACUSE DE RECIBO ELECTRONICO (Art. 34.9 ET / RDL 8/2019): Documento inmutable "
            "para firma del trabajador con huella SHA-256.",
            font="F2",
            size=8.0,
        )
        y -= 11.0
        page.text(
            _MARGIN_LEFT,
            y,
            f"Generado UTC: {gen_ts} | Conservacion legal obligatoria >= 4 anos.",
            font="F1",
            size=8.0,
        )

        return _build_pdf_document(pages)


_CJK_REGULAR_FONT_NAME = "OkTeamCJKRegular"
_CJK_BOLD_FONT_NAME = "OkTeamCJKBold"
_FONT_REGISTER_LOCK = threading.Lock()
_RESOLVED_CJK_FONTS: tuple[str, str] | None = None

_SIG_BOX_WIDTH = 210.0


def _sanitize_unicode_text(text: str) -> str:
    """Strip ASCII control chars while preserving Unicode (CJK, accents, Euro sign)."""
    chars: list[str] = []
    for ch in text:
        if ch in ("\r", "\n", "\t"):
            chars.append(" ")
            continue
        code = ord(ch)
        if code < 0x20 or code == 0x7F:
            continue
        chars.append(ch)
    return "".join(chars)


def _default_cjk_font_candidates() -> tuple[
    tuple[tuple[str, int | None], ...],
    tuple[tuple[str, int | None], ...],
]:
    win_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    regular: tuple[tuple[str, int | None], ...] = (
        # Linux: Noto Sans CJK (Debian/Ubuntu/Alpine) & WenQuanYi
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 2),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
        ("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc", 2),
        ("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc", 0),
        ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", 2),
        ("/usr/share/fonts/truetype/noto/NotoSansSC-Regular.ttf", None),
        ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
        ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0),
        # Windows: Microsoft YaHei / SimHei / SimSun
        (str(win_fonts / "msyh.ttc"), 0),
        (str(win_fonts / "msyh.ttf"), None),
        (str(win_fonts / "simhei.ttf"), None),
        (str(win_fonts / "simsun.ttc"), 0),
    )
    bold: tuple[tuple[str, int | None], ...] = (
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 2),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 0),
        ("/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc", 2),
        ("/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc", 0),
        ("/usr/share/fonts/truetype/noto/NotoSansSC-Bold.ttf", None),
        (str(win_fonts / "msyhbd.ttc"), 0),
        (str(win_fonts / "msyhbd.ttf"), None),
    )
    return regular, bold


def _try_register_ttfont(
    font_name: str,
    font_path: str,
    subfont_index: int | None,
) -> bool:
    if not os.path.isfile(font_path):
        return False
    if font_name in pdfmetrics.getRegisteredFontNames():
        return True
    try:
        if subfont_index is not None:
            font = TTFont(font_name, font_path, subfontIndex=subfont_index)
        else:
            font = TTFont(font_name, font_path)
        pdfmetrics.registerFont(font)
        return True
    except Exception:
        return False


def _resolve_reportlab_fonts(
    *,
    regular_candidates: Sequence[tuple[str, int | None]] | None = None,
    bold_candidates: Sequence[tuple[str, int | None]] | None = None,
) -> tuple[str, str]:
    """Detect and register system CJK TrueType fonts, falling back to Helvetica."""
    global _RESOLVED_CJK_FONTS
    use_cache = regular_candidates is None and bold_candidates is None
    with _FONT_REGISTER_LOCK:
        if use_cache and _RESOLVED_CJK_FONTS is not None:
            return _RESOLVED_CJK_FONTS

        default_reg, default_bold = _default_cjk_font_candidates()
        reg_list = default_reg if regular_candidates is None else tuple(regular_candidates)
        bold_list = default_bold if bold_candidates is None else tuple(bold_candidates)

        regular_name: str | None = None
        for path, subfont_idx in reg_list:
            if _try_register_ttfont(_CJK_REGULAR_FONT_NAME, path, subfont_idx):
                regular_name = _CJK_REGULAR_FONT_NAME
                break

        if regular_name is None:
            result = ("Helvetica", "Helvetica-Bold")
        else:
            bold_name = regular_name
            for path, subfont_idx in bold_list:
                if _try_register_ttfont(_CJK_BOLD_FONT_NAME, path, subfont_idx):
                    bold_name = _CJK_BOLD_FONT_NAME
                    break
            result = (regular_name, bold_name)

        if use_cache:
            _RESOLVED_CJK_FONTS = result
        return result


def _format_absence_line(data: AttendancePdfDocumentInput) -> str:
    """Format monthly absence summary line, strictly redacting health data when required."""
    if data.redact_health:
        other_absences = data.sick_leave_days + data.paid_leave_days + data.unpaid_leave_days
        return (
            f"Pausa Total: {data.total_break_minutes} min | "
            f"Vacaciones: {data.vacation_days}d | Otras Ausencias: {other_absences}d"
        )
    return (
        f"Pausa Total: {data.total_break_minutes} min | "
        f"Vacaciones: {data.vacation_days}d | "
        f"Baja Médica (IT): {data.sick_leave_days}d | "
        f"Perm. Retrib.: {data.paid_leave_days}d | No Retrib.: {data.unpaid_leave_days}d"
    )


class ReportLabAttendancePdfRenderer:
    """ReportLab A4 es-ES/CJK RDL 8/2019 Monthly Attendance Report PDF renderer."""

    def __init__(
        self,
        *,
        regular_font_candidates: Sequence[tuple[str, int | None]] | None = None,
        bold_font_candidates: Sequence[tuple[str, int | None]] | None = None,
    ) -> None:
        self._regular_font_candidates = regular_font_candidates
        self._bold_font_candidates = bold_font_candidates

    def render_monthly_attendance_pdf(self, data: AttendancePdfDocumentInput) -> bytes:
        font_regular, font_bold = _resolve_reportlab_fonts(
            regular_candidates=self._regular_font_candidates,
            bold_candidates=self._bold_font_candidates,
        )
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        c.setLineWidth(0.5)
        page_num = 1

        def _text(
            x: float,
            y_pos: float,
            value: str,
            *,
            bold: bool = False,
            size: float = 8.5,
        ) -> None:
            c.setFont(font_bold if bold else font_regular, size)
            c.drawString(x, y_pos, _sanitize_unicode_text(value))

        def _text_right(
            x_right: float,
            y_pos: float,
            value: str,
            *,
            bold: bool = False,
            size: float = 8.5,
        ) -> None:
            c.setFont(font_bold if bold else font_regular, size)
            c.drawRightString(x_right, y_pos, _sanitize_unicode_text(value))

        def _text_center(
            x_center: float,
            y_pos: float,
            value: str,
            *,
            bold: bool = False,
            size: float = 8.5,
        ) -> None:
            c.setFont(font_bold if bold else font_regular, size)
            c.drawCentredString(x_center, y_pos, _sanitize_unicode_text(value))

        def _hline(
            y_pos: float,
            *,
            x1: float = _MARGIN_LEFT,
            x2: float = _MARGIN_RIGHT,
        ) -> None:
            c.line(x1, y_pos, x2, y_pos)

        def _draw_header(current_page: int) -> float:
            y_pos = 800.0
            _text(
                _MARGIN_LEFT,
                y_pos,
                "RESUMEN MENSUAL DE REGISTRO DIARIO DE JORNADA (RDL 8/2019)",
                bold=True,
                size=10.5,
            )
            _text_right(
                _MARGIN_RIGHT,
                y_pos,
                f"Periodo: {data.period_month} (v{data.revision_number})",
                bold=True,
                size=8.5,
            )
            y_pos -= 14.0
            _hline(y_pos)
            y_pos -= 14.0
            _text(
                _MARGIN_LEFT,
                y_pos,
                f"Empresa: {_truncate(data.tenant_legal_name, 48)} | CIF: {data.tenant_cif}",
                size=8.5,
            )
            _text_right(_MARGIN_RIGHT, y_pos, f"Pág. {current_page}", size=8.5)
            y_pos -= 12.0
            _text(
                _MARGIN_LEFT,
                y_pos,
                f"Centro: {data.store_code} - {_truncate(data.store_name, 38)} | "
                f"Zona Horaria: {data.store_timezone}",
                size=8.5,
            )
            y_pos -= 14.0
            return y_pos

        def _draw_table_header(y_pos: float) -> float:
            _text(_MARGIN_LEFT, y_pos, "Fecha", bold=True, size=8.5)
            _text(112.0, y_pos, "Entrada", bold=True, size=8.5)
            _text(178.0, y_pos, "Salida", bold=True, size=8.5)
            _text(244.0, y_pos, "Pausa (min)", bold=True, size=8.5)
            _text(314.0, y_pos, "Horas Netas", bold=True, size=8.5)
            _text(386.0, y_pos, "Corr.", bold=True, size=8.5)
            _text(430.0, y_pos, "Notas", bold=True, size=8.5)
            y_pos -= 5.0
            _hline(y_pos)
            return y_pos - 13.0

        y = _draw_header(page_num)

        # Employee block on first page
        contract_label = _CONTRACT_LABELS_ES.get(data.contract_type.value, data.contract_type.value)
        schedule_label = _SCHEDULE_LABELS_ES.get(
            data.work_schedule_type.value, data.work_schedule_type.value
        )
        nie_display = data.nie_dni_masked or "No registrado"
        _text(
            _MARGIN_LEFT,
            y,
            f"Trabajador: {_truncate(data.employee_full_name, 42)} ({data.employee_code})",
            bold=True,
            size=9.0,
        )
        _text_right(_MARGIN_RIGHT, y, f"NIE/DNI: {nie_display}", size=9.0)
        y -= 12.0
        _text(
            _MARGIN_LEFT,
            y,
            f"Contrato: {contract_label} | Jornada: {schedule_label} | "
            f"Horas/Sem: {data.contracted_weekly_hours:.2f}h",
            size=8.5,
        )
        y -= 12.0
        _hline(y)
        y -= 16.0

        y = _draw_table_header(y)

        if not data.daily_rows:
            _text(
                _MARGIN_LEFT,
                y,
                "Sin registros diarios de jornada en el periodo.",
                size=8.5,
            )
            y -= 16.0
        else:
            for row in data.daily_rows:
                if y < _BOTTOM_MARGIN + 20.0:
                    c.showPage()
                    c.setLineWidth(0.5)
                    page_num += 1
                    y = _draw_header(page_num)
                    y = _draw_table_header(y)

                _text(_MARGIN_LEFT, y, row.local_date.isoformat(), size=8.5)
                _text(112.0, y, row.clock_in_local, size=8.5)
                _text(178.0, y, row.clock_out_local, size=8.5)
                _text(255.0, y, str(row.break_minutes), size=8.5)
                _text(320.0, y, f"{row.net_hours:.2f}h", size=8.5)
                _text(390.0, y, "Sí" if row.has_corrections else "No", size=8.5)
                _text(430.0, y, _truncate(row.notes, 24), size=8.5)
                y -= 13.5

        # Summary + legal notice + paper signature block (~185pt needed)
        if y < _BOTTOM_MARGIN + 185.0:
            c.showPage()
            c.setLineWidth(0.5)
            page_num += 1
            y = _draw_header(page_num)

        y -= 6.0
        _hline(y)
        y -= 15.0
        _text(_MARGIN_LEFT, y, "RESUMEN MENSUAL Y AUSENCIAS", bold=True, size=9.0)
        y -= 13.0
        _text(
            _MARGIN_LEFT,
            y,
            f"Días Trabajados: {data.worked_days} | Horas Netas: {data.total_net_hours:.2f}h | "
            f"Ordinarias: {data.regular_hours:.2f}h | Extra: {data.overtime_hours:.2f}h",
            size=8.5,
        )
        y -= 12.0
        _text(_MARGIN_LEFT, y, _format_absence_line(data), size=8.5)
        y -= 12.0
        _text(
            _MARGIN_LEFT,
            y,
            f"Alertas Legales -> Avisos: {data.warning_alerts_count} | "
            f"Infracciones: {data.violation_alerts_count} | "
            f"Extra Anual Acumulada: {data.annual_overtime_hours:.2f}h",
            size=8.5,
        )
        y -= 16.0
        _hline(y)
        y -= 13.0
        gen_ts = data.generated_at_utc.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
        _text(
            _MARGIN_LEFT,
            y,
            "ACUSE DE RECIBO ELECTRÓNICO (Art. 34.9 ET / RDL 8/2019): Documento inmutable "
            "para firma del trabajador con huella SHA-256.",
            bold=True,
            size=8.0,
        )
        y -= 11.0
        _text(
            _MARGIN_LEFT,
            y,
            f"Generado UTC: {gen_ts} | Conservación legal obligatoria >= 4 años.",
            size=8.0,
        )

        # Symmetric left/right paper signature boxes
        sig_line_y = y - 54.0
        left_x1 = _MARGIN_LEFT
        left_x2 = _MARGIN_LEFT + _SIG_BOX_WIDTH
        right_x1 = _MARGIN_RIGHT - _SIG_BOX_WIDTH
        right_x2 = _MARGIN_RIGHT

        _hline(sig_line_y, x1=left_x1, x2=left_x2)
        _hline(sig_line_y, x1=right_x1, x2=right_x2)

        sig_label_y = sig_line_y - 13.0
        _text_center(
            (left_x1 + left_x2) / 2.0,
            sig_label_y,
            "Firma y sello de la Empresa",
            bold=True,
            size=8.5,
        )
        _text_center(
            (right_x1 + right_x2) / 2.0,
            sig_label_y,
            "Firma del Trabajador / Recibí",
            bold=True,
            size=8.5,
        )

        c.save()
        return buf.getvalue()

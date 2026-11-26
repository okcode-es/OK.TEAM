"""Pure Python 3.12 stdlib OOXML (.xlsx) reader & writer with Zip-Bomb and XXE guards."""

from __future__ import annotations

import io
import re
import zipfile
import zlib
from xml.etree import ElementTree as ET
from xml.parsers import expat
from xml.sax.saxutils import escape

from app.domain.organization import ValidationDomainError

MAX_XLSX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024  # 20 MiB
MAX_XLSX_ROWS = 500
MAX_ZIP_ENTRIES = 128
MAX_COLUMNS = 64
_XXE_FORBIDDEN = re.compile(rb"<!(?:DOCTYPE|ENTITY)", re.IGNORECASE)
_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _col_letter(idx: int) -> str:
    """Convert 0-based column index to Excel column letters (A, B, ..., Z, AA, ...)."""
    result = ""
    n = idx + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def _col_index_from_ref(ref: str) -> int | None:
    """Parse cell reference like 'C2' -> 0-based column index 2."""
    match = re.match(r"^([A-Za-z]+)[0-9]+$", ref.strip())
    if not match:
        return None
    letters = match.group(1).upper()
    if len(letters) > 3:
        raise ValidationDomainError(f"XLSX column reference '{letters}' exceeds {MAX_COLUMNS} cols")
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    col_idx = idx - 1
    if col_idx >= MAX_COLUMNS:
        raise ValidationDomainError(f"XLSX column index exceeds maximum of {MAX_COLUMNS} columns")
    return col_idx


def _reject_xml_dtd_and_entities(raw: bytes) -> None:
    """Reject any DTD or ENTITY declaration regardless of encoding using expat."""
    if _XXE_FORBIDDEN.search(raw):
        raise ValidationDomainError(
            "XLSX contains forbidden XML DOCTYPE or ENTITY declaration (XXE rejected)"
        )

    def _forbid(*_args: object, **_kwargs: object) -> None:
        raise ValidationDomainError(
            "XLSX contains forbidden XML DOCTYPE or ENTITY declaration (XXE rejected)"
        )

    parser = expat.ParserCreate()
    parser.StartDoctypeDeclHandler = _forbid
    parser.EntityDeclHandler = _forbid
    parser.UnparsedEntityDeclHandler = _forbid
    parser.ExternalEntityRefHandler = lambda *_a, **_kw: 0
    try:
        parser.Parse(raw, True)
    except ValidationDomainError:
        raise
    except expat.ExpatError as exc:
        raise ValidationDomainError("Malformed XML in XLSX archive") from exc


def generate_xlsx(sheet_name: str, headers: list[str], rows: list[list[str]]) -> bytes:
    """Generate a minimal valid OOXML .xlsx workbook using pure Python stdlib."""
    safe_sheet = escape(sheet_name[:31] or "Employees")
    all_rows = [headers, *rows]
    sheet_rows_xml: list[str] = []
    for r_idx, row in enumerate(all_rows, start=1):
        cells_xml: list[str] = []
        for c_idx, val in enumerate(row):
            ref = f"{_col_letter(c_idx)}{r_idx}"
            cells_xml.append(
                f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(val))}</t></is></c>'
            )
        sheet_rows_xml.append(f'<row r="{r_idx}">{"".join(cells_xml)}</row>')

    worksheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows_xml)}</sheetData>'
        "</worksheet>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{safe_sheet}" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )
    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    root_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml.encode("utf-8"))
        zf.writestr("_rels/.rels", root_rels_xml.encode("utf-8"))
        zf.writestr("xl/workbook.xml", workbook_xml.encode("utf-8"))
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml.encode("utf-8"))
        zf.writestr("xl/worksheets/sheet1.xml", worksheet_xml.encode("utf-8"))
    return buf.getvalue()


def _safe_read_zip_member(
    zf: zipfile.ZipFile,
    name: str,
    *,
    budget_remaining: list[int],
) -> bytes:
    """Stream-read a zip entry enforcing the global uncompressed byte budget & XXE check."""
    chunks: list[bytes] = []
    with zf.open(name, "r") as stream:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            budget_remaining[0] -= len(chunk)
            if budget_remaining[0] < 0:
                raise ValidationDomainError(
                    "XLSX archive exceeds maximum uncompressed size of 20 MiB (Zip-Bomb rejected)"
                )
            chunks.append(chunk)
    raw = b"".join(chunks)
    if name.lower().endswith((".xml", ".rels")):
        _reject_xml_dtd_and_entities(raw)
    return raw


def parse_xlsx(
    content: bytes,
    *,
    max_uncompressed_bytes: int = MAX_XLSX_UNCOMPRESSED_BYTES,
    max_rows: int = MAX_XLSX_ROWS,
) -> tuple[list[str], list[list[str]]]:
    """Parse the first worksheet of an .xlsx file into (headers, data_rows)."""
    try:
        with zipfile.ZipFile(io.BytesIO(content), "r") as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                raise ValidationDomainError(
                    f"XLSX archive exceeds maximum of {MAX_ZIP_ENTRIES} ZIP entries"
                )
            total_declared = sum(info.file_size for info in infos)
            if total_declared > max_uncompressed_bytes:
                raise ValidationDomainError(
                    "XLSX archive exceeds maximum uncompressed size of 20 MiB (Zip-Bomb rejected)"
                )

            budget = [max_uncompressed_bytes]
            names = [info.filename for info in infos]
            xml_cache: dict[str, bytes] = {}
            for name in names:
                if name.lower().endswith((".xml", ".rels")):
                    xml_cache[name] = _safe_read_zip_member(zf, name, budget_remaining=budget)

            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in xml_cache:
                ss_root = ET.fromstring(xml_cache["xl/sharedStrings.xml"])
                for si in ss_root.findall(f"{{{_NS_MAIN}}}si"):
                    texts = [t.text or "" for t in si.iter(f"{{{_NS_MAIN}}}t")]
                    shared_strings.append("".join(texts))

            sheet_candidates = sorted(
                n
                for n in xml_cache
                if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")
            )
            if not sheet_candidates:
                raise ValidationDomainError("XLSX workbook contains no worksheets")

            sheet_root = ET.fromstring(xml_cache[sheet_candidates[0]])
            sheet_data = sheet_root.find(f"{{{_NS_MAIN}}}sheetData")
            if sheet_data is None:
                raise ValidationDomainError("XLSX worksheet is empty")

            parsed_rows: list[list[str]] = []
            for row_el in sheet_data.findall(f"{{{_NS_MAIN}}}row"):
                row_values: list[str] = []
                for cell_el in row_el.findall(f"{{{_NS_MAIN}}}c"):
                    ref = cell_el.attrib.get("r", "")
                    col_idx = _col_index_from_ref(ref)
                    if col_idx is None:
                        col_idx = len(row_values)
                    if col_idx >= MAX_COLUMNS:
                        raise ValidationDomainError(
                            f"XLSX column index exceeds maximum of {MAX_COLUMNS} columns"
                        )
                    while len(row_values) <= col_idx:
                        row_values.append("")

                    cell_type = cell_el.attrib.get("t", "")
                    val = ""
                    if cell_type == "inlineStr":
                        texts = [t.text or "" for t in cell_el.iter(f"{{{_NS_MAIN}}}t")]
                        val = "".join(texts)
                    elif cell_type == "s":
                        v_el = cell_el.find(f"{{{_NS_MAIN}}}v")
                        if v_el is not None and v_el.text is not None:
                            idx = int(v_el.text)
                            val = shared_strings[idx] if 0 <= idx < len(shared_strings) else ""
                    else:
                        v_el = cell_el.find(f"{{{_NS_MAIN}}}v")
                        if v_el is not None and v_el.text is not None:
                            val = v_el.text
                    row_values[col_idx] = val.strip()

                if any(cell for cell in row_values):
                    parsed_rows.append(row_values)
                    if len(parsed_rows) > max_rows + 1:
                        raise ValidationDomainError(
                            f"Import file exceeds maximum of {max_rows} employee rows"
                        )

            if not parsed_rows:
                raise ValidationDomainError("XLSX worksheet is empty")

            return parsed_rows[0], parsed_rows[1:]
    except ValidationDomainError:
        raise
    except (
        ET.ParseError,
        ValueError,
        zlib.error,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        RuntimeError,
        NotImplementedError,
        EOFError,
        KeyError,
        UnicodeDecodeError,
    ) as exc:
        raise ValidationDomainError(f"Invalid or corrupted .xlsx file: {exc}") from exc

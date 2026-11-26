"""Pure stdlib Spanish national & autonomous community holiday engine.

Pure Python standard library only (enforced by scripts/check_domain_purity.py).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

SUPPORTED_REGION_CODES: frozenset[str] = frozenset(
    {
        "ES-MD",  # Comunidad de Madrid
        "ES-CT",  # Cataluña
        "ES-VC",  # Comunitat Valenciana
        "ES-AN",  # Andalucía
        "ES-PV",  # País Vasco
        "ES-GA",  # Galicia
        "ES-CL",  # Castilla y León
        "ES-CN",  # Canarias
        "ES-IB",  # Illes Balears
    }
)


class HolidayScope(StrEnum):
    NATIONAL = "national"
    REGIONAL = "regional"


@dataclass(frozen=True, slots=True)
class Holiday:
    date: date
    name_es: str
    name_zh: str
    scope: HolidayScope
    region_code: str | None = None


def compute_easter_sunday(year: int) -> date:
    """Anonymous Gregorian Computus algorithm for Western Easter Sunday."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    easter_l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * easter_l) // 451
    month = (h + easter_l - 7 * m + 114) // 31
    day = ((h + easter_l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


_FIXED_NATIONAL_HOLIDAYS: tuple[tuple[int, int, str, str], ...] = (
    (1, 1, "Año Nuevo", "元旦"),
    (1, 6, "Epifanía del Señor", "三王节"),
    (5, 1, "Fiesta del Trabajo", "国际劳动节"),
    (8, 15, "Asunción de la Virgen", "圣母升天节"),
    (10, 12, "Fiesta Nacional de España", "西班牙国庆节"),
    (11, 1, "Todos los Santos", "万圣节"),
    (12, 6, "Día de la Constitución Española", "西班牙宪法日"),
    (12, 8, "Inmaculada Concepción", "圣母无染原罪节"),
    (12, 25, "Natividad del Señor", "圣诞节"),
)

# (month, day, name_es, name_zh)
_FIXED_REGIONAL_HOLIDAYS: dict[str, tuple[tuple[int, int, str, str], ...]] = {
    "ES-MD": (
        (5, 2, "Fiesta de la Comunidad de Madrid", "马德里自治区日"),
        (7, 25, "Santiago Apóstol", "圣地亚哥节"),
    ),
    "ES-CT": (
        (6, 24, "Sant Joan", "圣胡安节"),
        (9, 11, "Diada Nacional de Catalunya", "加泰罗尼亚民族日"),
        (12, 26, "Sant Esteve", "圣埃斯特万节"),
    ),
    "ES-VC": (
        (3, 19, "San José", "圣何塞节"),
        (6, 24, "San Juan", "圣胡安节"),
        (10, 9, "Día de la Comunitat Valenciana", "瓦伦西亚自治区日"),
    ),
    "ES-AN": (
        (2, 28, "Día de Andalucía", "安达卢西亚日"),
    ),
    "ES-PV": (
        (7, 25, "Santiago Apóstol", "圣地亚哥节"),
    ),
    "ES-GA": (
        (5, 17, "Día das Letras Galegas", "加利西亚文学日"),
        (7, 25, "Día Nacional de Galicia", "加利西亚日"),
    ),
    "ES-CL": (
        (4, 23, "Día de Castilla y León", "卡斯蒂利亚-莱昂日"),
    ),
    "ES-CN": (
        (5, 30, "Día de Canarias", "加那利群岛日"),
    ),
    "ES-IB": (
        (3, 1, "Dia de les Illes Balears", "巴利阿里群岛日"),
        (12, 26, "Segona Festa de Nadal", "圣诞次日"),
    ),
}

_JUEVES_SANTO_REGIONS: frozenset[str] = frozenset(
    {"ES-MD", "ES-AN", "ES-PV", "ES-GA", "ES-CL", "ES-CN", "ES-IB"}
)
_LUNES_PASCUA_REGIONS: frozenset[str] = frozenset({"ES-CT", "ES-VC", "ES-PV", "ES-IB"})


def list_holidays_for_year(year: int, region_code: str | None = None) -> list[Holiday]:
    """Return sorted Spanish national + optional autonomous community holidays for a year."""
    easter = compute_easter_sunday(year)
    holidays: list[Holiday] = [
        Holiday(
            date=date(year, month, day),
            name_es=name_es,
            name_zh=name_zh,
            scope=HolidayScope.NATIONAL,
            region_code=None,
        )
        for month, day, name_es, name_zh in _FIXED_NATIONAL_HOLIDAYS
    ]
    holidays.append(
        Holiday(
            date=easter - timedelta(days=2),
            name_es="Viernes Santo",
            name_zh="耶稣受难日",
            scope=HolidayScope.NATIONAL,
            region_code=None,
        )
    )

    normalized_region = (region_code or "").strip().upper()
    if normalized_region in SUPPORTED_REGION_CODES:
        if normalized_region in _JUEVES_SANTO_REGIONS:
            holidays.append(
                Holiday(
                    date=easter - timedelta(days=3),
                    name_es="Jueves Santo",
                    name_zh="圣周四",
                    scope=HolidayScope.REGIONAL,
                    region_code=normalized_region,
                )
            )
        if normalized_region in _LUNES_PASCUA_REGIONS:
            holidays.append(
                Holiday(
                    date=easter + timedelta(days=1),
                    name_es="Lunes de Pascua",
                    name_zh="复活节星期一",
                    scope=HolidayScope.REGIONAL,
                    region_code=normalized_region,
                )
            )
        for month, day, name_es, name_zh in _FIXED_REGIONAL_HOLIDAYS.get(normalized_region, ()):
            holidays.append(
                Holiday(
                    date=date(year, month, day),
                    name_es=name_es,
                    name_zh=name_zh,
                    scope=HolidayScope.REGIONAL,
                    region_code=normalized_region,
                )
            )

    holidays.sort(key=lambda h: (h.date, h.scope.value, h.name_es))
    return holidays


def list_upcoming_holidays(
    start_date: date,
    days: int,
    region_codes: Iterable[str],
) -> list[Holiday]:
    """Return deduplicated upcoming holidays in [start_date, start_date + days]."""
    end_date = start_date + timedelta(days=days)
    years = range(start_date.year, end_date.year + 1)
    normalized_regions = sorted(
        {
            r.strip().upper()
            for r in region_codes
            if r and r.strip().upper() in SUPPORTED_REGION_CODES
        }
    )
    if not normalized_regions:
        normalized_regions = [""]

    seen: set[tuple[date, str, str | None]] = set()
    result: list[Holiday] = []
    for yr in years:
        for reg in normalized_regions:
            for h in list_holidays_for_year(yr, reg or None):
                if start_date <= h.date <= end_date:
                    key = (h.date, h.name_es, h.region_code)
                    if key not in seen:
                        seen.add(key)
                        result.append(h)
    result.sort(key=lambda h: (h.date, h.scope.value, h.name_es))
    return result

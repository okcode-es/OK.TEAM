"""Pure domain entities and resolution logic for 3-tier custom break policies.

Pure Python standard library only (enforced by scripts/check_domain_purity.py).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from app.domain.organization import ValidationDomainError

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class BreakScopeType(StrEnum):
    TENANT = "tenant"
    STORE = "store"
    EMPLOYEE = "employee"


class BreakMode(StrEnum):
    FIXED_DURATION = "fixed_duration"
    FIXED_WINDOW = "fixed_window"


class BreakTrackingMode(StrEnum):
    MANUAL_PUNCH = "manual_punch"
    AUTO_DEDUCT = "auto_deduct"


@dataclass(frozen=True, slots=True)
class BreakWindow:
    start: str
    end: str


@dataclass(frozen=True, slots=True)
class BreakPolicy:
    id: UUID
    tenant_id: UUID
    scope_type: BreakScopeType
    store_id: UUID | None
    employee_id: UUID | None
    break_mode: BreakMode
    duration_minutes: int | None
    windows: tuple[BreakWindow, ...]
    is_paid: bool
    tracking_mode: BreakTrackingMode
    min_shift_minutes_for_auto_deduct: int
    auto_end_manual_break: bool
    is_active: bool
    updated_by_user_id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class EffectiveBreakPolicy:
    policy_id: UUID | None
    inherited_from: Literal["employee", "store", "tenant", "legacy_default", "none"]
    break_mode: BreakMode
    duration_minutes: int | None
    windows: tuple[BreakWindow, ...]
    is_paid: bool
    tracking_mode: BreakTrackingMode
    min_shift_minutes_for_auto_deduct: int
    auto_end_manual_break: bool

    @property
    def effective_duration_minutes(self) -> int | None:
        if self.break_mode == BreakMode.FIXED_DURATION:
            return self.duration_minutes
        if self.windows:
            return sum(
                parse_hhmm_minutes(w.end) - parse_hhmm_minutes(w.start) for w in self.windows
            )
        return None


def parse_hhmm_minutes(raw: str) -> int:
    match = _HHMM_RE.match(raw.strip())
    if match is None:
        raise ValidationDomainError(f"Invalid HH:MM time '{raw}'")
    return int(match.group(1)) * 60 + int(match.group(2))


def validate_break_windows(windows: Sequence[BreakWindow]) -> tuple[BreakWindow, ...]:
    if not (1 <= len(windows) <= 4):
        raise ValidationDomainError("fixed_window break policy requires between 1 and 4 windows")

    parsed: list[tuple[int, int, BreakWindow]] = []
    for w in windows:
        start_m = parse_hhmm_minutes(w.start)
        end_m = parse_hhmm_minutes(w.end)
        if end_m <= start_m:
            raise ValidationDomainError(
                f"Break window end '{w.end}' must be strictly after start '{w.start}'"
            )
        parsed.append((start_m, end_m, BreakWindow(start=w.start.strip(), end=w.end.strip())))

    parsed.sort(key=lambda item: (item[0], item[1]))
    total_minutes = 0
    for idx, (start_m, end_m, _) in enumerate(parsed):
        if idx > 0 and start_m < parsed[idx - 1][1]:
            raise ValidationDomainError("Break windows must not overlap")
        total_minutes += end_m - start_m

    if total_minutes < 5 or total_minutes > 240:
        raise ValidationDomainError(
            "Total break window duration must be between 5 and 240 minutes"
        )
    return tuple(item[2] for item in parsed)


def validate_break_policy_config(
    *,
    break_mode: BreakMode,
    duration_minutes: int | None,
    windows: Sequence[BreakWindow],
    min_shift_minutes_for_auto_deduct: int,
) -> tuple[int | None, tuple[BreakWindow, ...]]:
    if min_shift_minutes_for_auto_deduct < 60 or min_shift_minutes_for_auto_deduct > 720:
        raise ValidationDomainError(
            "min_shift_minutes_for_auto_deduct must be between 60 and 720 minutes"
        )

    if break_mode == BreakMode.FIXED_DURATION:
        if duration_minutes is None or duration_minutes < 5 or duration_minutes > 240:
            raise ValidationDomainError(
                "fixed_duration break policy requires duration_minutes between 5 and 240"
            )
        if len(windows) > 0:
            raise ValidationDomainError(
                "fixed_duration break policy must not specify windows"
            )
        return (duration_minutes, ())

    if duration_minutes is not None:
        raise ValidationDomainError(
            "fixed_window break policy must not specify duration_minutes"
        )
    return (None, validate_break_windows(windows))


def _active_policy(policy: BreakPolicy | None) -> BreakPolicy | None:
    if policy is None or policy.deleted_at is not None or not policy.is_active:
        return None
    return policy


def resolve_effective_break_policy(
    tenant_policy: BreakPolicy | None,
    store_policy: BreakPolicy | None,
    employee_policy: BreakPolicy | None,
    legacy_default_break_minutes: int | None,
) -> EffectiveBreakPolicy:
    """Resolve 3-tier break policy precedence: employee > store > tenant > legacy_default > none."""
    for source, candidate in (
        ("employee", _active_policy(employee_policy)),
        ("store", _active_policy(store_policy)),
        ("tenant", _active_policy(tenant_policy)),
    ):
        if candidate is not None:
            return EffectiveBreakPolicy(
                policy_id=candidate.id,
                inherited_from=source,  # type: ignore[arg-type]
                break_mode=candidate.break_mode,
                duration_minutes=candidate.duration_minutes,
                windows=candidate.windows,
                is_paid=candidate.is_paid,
                tracking_mode=candidate.tracking_mode,
                min_shift_minutes_for_auto_deduct=candidate.min_shift_minutes_for_auto_deduct,
                auto_end_manual_break=candidate.auto_end_manual_break,
            )

    if legacy_default_break_minutes is not None:
        return EffectiveBreakPolicy(
            policy_id=None,
            inherited_from="legacy_default",
            break_mode=BreakMode.FIXED_DURATION,
            duration_minutes=legacy_default_break_minutes,
            windows=(),
            is_paid=False,
            tracking_mode=BreakTrackingMode.MANUAL_PUNCH,
            min_shift_minutes_for_auto_deduct=360,
            auto_end_manual_break=True,
        )

    return EffectiveBreakPolicy(
        policy_id=None,
        inherited_from="none",
        break_mode=BreakMode.FIXED_DURATION,
        duration_minutes=None,
        windows=(),
        is_paid=False,
        tracking_mode=BreakTrackingMode.MANUAL_PUNCH,
        min_shift_minutes_for_auto_deduct=360,
        auto_end_manual_break=False,
    )

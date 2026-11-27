"""Pure domain builder for the Interactive Organization Chart (Phase 5I).

Pure Python standard library only (enforced by scripts/check_domain_purity.py).
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from app.domain.employees import Employee, EmploymentStatus

_ORG_CHART_ACTIVE_STATUSES: frozenset[EmploymentStatus] = frozenset(
    {
        EmploymentStatus.ACTIVE,
        EmploymentStatus.ON_LEAVE,
    }
)


@dataclass(frozen=True, slots=True)
class OrgChartNode:
    """GDPR-minimized organization chart node (exact 12 public fields)."""

    id: UUID
    full_name: str
    avatar_url: str | None
    job_title: str
    department_id: str | None
    department_name: str
    store_id: UUID
    store_name: str
    work_email: str
    manager_id: UUID | None
    direct_reports_count: int
    total_reports_count: int


@dataclass(frozen=True, slots=True)
class OrgChartResult:
    """Deterministic organization chart projection for a tenant."""

    nodes: tuple[OrgChartNode, ...]
    root_ids: tuple[UUID, ...]
    unassigned_ids: tuple[UUID, ...]
    current_employee_id: UUID | None
    total_active_employees: int
    cycle_broken_count: int


def build_org_chart(
    employees: Sequence[Employee],
    *,
    store_names: Mapping[UUID, str],
    avatar_urls: Mapping[UUID, str | None],
    current_employee_id: UUID | None = None,
) -> OrgChartResult:
    """Build a deterministic, cycle-free organization chart from active employees."""
    active = sorted(
        (
            emp
            for emp in employees
            if emp.deleted_at is None and emp.employment_status in _ORG_CHART_ACTIVE_STATUSES
        ),
        key=lambda emp: (emp.full_name.casefold(), str(emp.id)),
    )
    active_ids = {emp.id for emp in active}
    ordered_ids = [emp.id for emp in active]

    effective_manager: dict[UUID, UUID | None] = {}
    for emp in active:
        mgr = emp.manager_id
        if mgr is None or mgr == emp.id or mgr not in active_ids:
            effective_manager[emp.id] = None
        else:
            effective_manager[emp.id] = mgr

    # Iterative O(N) 3-color cycle detection (0=unvisited, 1=visiting, 2=done).
    state: dict[UUID, int] = {uid: 0 for uid in ordered_ids}
    cycle_broken_count = 0
    for start_id in ordered_ids:
        if state[start_id] != 0:
            continue
        path: list[UUID] = []
        path_pos: dict[UUID, int] = {}
        curr: UUID | None = start_id
        while curr is not None and state[curr] == 0:
            state[curr] = 1
            path_pos[curr] = len(path)
            path.append(curr)
            curr = effective_manager[curr]

        if curr is not None and state[curr] == 1:
            cycle_nodes = path[path_pos[curr] :]
            break_node = min(cycle_nodes, key=lambda uid: str(uid))
            effective_manager[break_node] = None
            cycle_broken_count += 1

        for node_id in path:
            state[node_id] = 2

    children: dict[UUID, list[UUID]] = defaultdict(list)
    for uid in ordered_ids:
        mgr = effective_manager[uid]
        if mgr is not None:
            children[mgr].append(uid)

    direct_reports_count: dict[UUID, int] = {uid: len(children[uid]) for uid in ordered_ids}

    # Iterative BFS + reverse topological accumulation for total_reports_count.
    topo_order: list[UUID] = []
    queue: deque[UUID] = deque(uid for uid in ordered_ids if effective_manager[uid] is None)
    while queue:
        uid = queue.popleft()
        topo_order.append(uid)
        for child_id in children[uid]:
            queue.append(child_id)

    total_reports_count: dict[UUID, int] = {uid: 0 for uid in ordered_ids}
    for uid in reversed(topo_order):
        mgr = effective_manager[uid]
        if mgr is not None:
            total_reports_count[mgr] += 1 + total_reports_count[uid]

    nodes: list[OrgChartNode] = []
    for emp in active:
        department_name = emp.department.strip()
        department_id = department_name.casefold() if department_name else None
        nodes.append(
            OrgChartNode(
                id=emp.id,
                full_name=emp.full_name,
                avatar_url=avatar_urls.get(emp.id),
                job_title=emp.job_title,
                department_id=department_id,
                department_name=department_name,
                store_id=emp.primary_store_id,
                store_name=store_names.get(emp.primary_store_id, ""),
                work_email=emp.email.strip(),
                manager_id=effective_manager[emp.id],
                direct_reports_count=direct_reports_count[emp.id],
                total_reports_count=total_reports_count[emp.id],
            )
        )

    root_ids = tuple(
        uid
        for uid in ordered_ids
        if effective_manager[uid] is None and direct_reports_count[uid] > 0
    )
    unassigned_ids = tuple(
        uid
        for uid in ordered_ids
        if effective_manager[uid] is None and direct_reports_count[uid] == 0
    )
    resolved_current_id = current_employee_id if current_employee_id in active_ids else None

    return OrgChartResult(
        nodes=tuple(nodes),
        root_ids=root_ids,
        unassigned_ids=unassigned_ids,
        current_employee_id=resolved_current_id,
        total_active_employees=len(active),
        cycle_broken_count=cycle_broken_count,
    )

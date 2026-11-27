import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import {
  AlertCircle,
  Briefcase,
  Building2,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  ChevronsDown,
  ChevronsUp,
  Crosshair,
  Download,
  Filter,
  Focus,
  Home,
  Mail,
  Maximize2,
  Minus,
  Plus,
  RefreshCw,
  Search,
  SlidersHorizontal,
  Sparkles,
  Store as StoreIcon,
  Users,
  X,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { apiClient, extractApiError } from '@/api/client'
import type { OrgChartNodePublic, OrgChartResponse } from '@/api/types'
import { EmptyState } from '@/components/common/EmptyState'
import { UserAvatar } from '@/components/common/UserAvatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { useAuth } from '@/features/auth/AuthContext'
import { normalizeDepartment } from '@/features/employees/department'
import {
  exportOrgChartPng,
  exportOrgChartSvg,
} from '@/features/employees/orgChartExport'
import {
  computeOrgChartLayout,
  type OrgChartVisibleFields,
  type PositionedOrgNode,
} from '@/features/employees/orgChartLayout'
import { OrgChartPublicDrawer } from '@/features/employees/OrgChartPublicDrawer'
import { isImeComposing } from '@/lib/ime'
import { cn } from '@/lib/utils'

interface OrgChartViewProps {
  onOpenFullProfile?: (employeeId: string) => void
}

type ToolbarPanel =
  | 'customize'
  | 'highlight'
  | 'focus'
  | 'filter'
  | 'export'
  | null

type HighlightTarget =
  | { type: 'none' }
  | { type: 'department'; key: string; label: string }
  | { type: 'store'; id: string; label: string }

const MIN_ZOOM = 0.25
const MAX_ZOOM = 2
const ZOOM_STEP = 0.15

function clampZoom(z: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Math.round(z * 100) / 100))
}

export function OrgChartView({ onOpenFullProfile }: OrgChartViewProps) {
  const { t, i18n } = useTranslation()
  const { activeTenant } = useAuth()
  const locale = i18n.language || 'zh-CN'

  const [status, setStatus] = useState<'loading' | 'ready' | 'failed'>('loading')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [data, setData] = useState<OrgChartResponse | null>(null)

  // Tree interaction state
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set())
  const [unassignedExpanded, setUnassignedExpanded] = useState(false)
  const [focusRootId, setFocusRootId] = useState<string | null>(null)
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [highlightedNodeId, setHighlightedNodeId] = useState<string | null>(
    null,
  )
  const [drawerOpen, setDrawerOpen] = useState(false)

  // Toolbar controls state
  const [openPanel, setOpenPanel] = useState<ToolbarPanel>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const [visibleFields, setVisibleFields] = useState<OrgChartVisibleFields>({
    jobTitle: true,
    department: true,
    store: true,
    workEmail: false,
  })
  const [highlightTarget, setHighlightTarget] = useState<HighlightTarget>({
    type: 'none',
  })
  const [storeFilter, setStoreFilter] = useState<string>('all')
  const [departmentFilter, setDepartmentFilter] = useState<string | undefined>(
    undefined,
  )
  const [exporting, setExporting] = useState(false)
  const [statusAnnouncement, setStatusAnnouncement] = useState('')

  // Pan & Zoom state
  const [transform, setTransform] = useState({ tx: 40, ty: 40, zoom: 1 })
  const [isPanning, setIsPanning] = useState(false)
  const [animateStage, setAnimateStage] = useState(false)
  const panRef = useRef<{
    pointerId: number
    startX: number
    startY: number
    originTx: number
    originTy: number
  } | null>(null)

  const canvasRef = useRef<HTMLDivElement | null>(null)
  const toolbarRef = useRef<HTMLDivElement | null>(null)
  const searchBoxRef = useRef<HTMLDivElement | null>(null)
  const didInitialFitRef = useRef(false)

  const loadOrgChart = useCallback(
    async (signal?: AbortSignal) => {
      setStatus('loading')
      setErrorMessage(null)
      try {
        const res = await apiClient.GET('/api/v1/organization/org-chart', {
          signal,
        })
        if (signal?.aborted) return
        if (res.error || !res.data) {
          setStatus('failed')
          setErrorMessage(extractApiError(res.error, t('common.error')))
          return
        }
        setData(res.data)
        setExpandedIds(new Set(res.data.root_ids))
        setUnassignedExpanded(res.data.root_ids.length === 0)
        setFocusRootId(null)
        setSelectedNodeId(null)
        setHighlightedNodeId(null)
        didInitialFitRef.current = false
        setStatus('ready')
      } catch (err) {
        if (signal?.aborted) return
        setStatus('failed')
        setErrorMessage(extractApiError(err, t('common.error')))
      }
    },
    [t],
  )

  useEffect(() => {
    const controller = new AbortController()
    void loadOrgChart(controller.signal)
    return () => controller.abort()
  }, [loadOrgChart, activeTenant?.tenant_id])

  // Close toolbar popovers & search dropdown on outside click or Escape
  useEffect(() => {
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null
      if (!target) return
      if (toolbarRef.current && !toolbarRef.current.contains(target)) {
        setOpenPanel(null)
      }
      if (searchBoxRef.current && !searchBoxRef.current.contains(target)) {
        setSearchOpen(false)
      }
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpenPanel(null)
        setSearchOpen(false)
      }
    }
    document.addEventListener('pointerdown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [])

  const nodeMap = useMemo(() => {
    const map = new Map<string, OrgChartNodePublic>()
    for (const n of data?.nodes ?? []) {
      map.set(n.id, n)
    }
    return map
  }, [data?.nodes])

  const childrenMap = useMemo(() => {
    const map = new Map<string, string[]>()
    for (const n of data?.nodes ?? []) {
      if (n.manager_id && nodeMap.has(n.manager_id)) {
        const list = map.get(n.manager_id)
        if (list) list.push(n.id)
        else map.set(n.manager_id, [n.id])
      }
    }
    return map
  }, [data?.nodes, nodeMap])

  const storeOptions = useMemo(() => {
    const map = new Map<string, string>()
    for (const n of data?.nodes ?? []) {
      if (n.store_id && n.store_name && !map.has(n.store_id)) {
        map.set(n.store_id, n.store_name)
      }
    }
    return [...map.entries()].sort((a, b) => a[1].localeCompare(b[1], locale))
  }, [data?.nodes, locale])

  const departmentOptions = useMemo(() => {
    const map = new Map<string, string>()
    for (const n of data?.nodes ?? []) {
      const key = normalizeDepartment(n.department_name)
      if (key && !map.has(key)) {
        map.set(key, n.department_name.trim())
      }
    }
    return [...map.entries()].sort((a, b) => a[1].localeCompare(b[1], locale))
  }, [data?.nodes, locale])

  const layout = useMemo(() => {
    if (!data) {
      return computeOrgChartLayout({
        nodes: [],
        rootIds: [],
        unassignedIds: [],
        expandedIds,
        focusRootId,
        storeFilter,
        departmentFilter,
        visibleFields,
        unassignedExpanded,
      })
    }
    return computeOrgChartLayout({
      nodes: data.nodes,
      rootIds: data.root_ids,
      unassignedIds: data.unassigned_ids,
      expandedIds,
      focusRootId,
      storeFilter,
      departmentFilter,
      visibleFields,
      unassignedExpanded,
    })
  }, [
    data,
    expandedIds,
    focusRootId,
    storeFilter,
    departmentFilter,
    visibleFields,
    unassignedExpanded,
  ])

  const fitToScreen = useCallback(
    (targetLayout = layout, smooth = true) => {
      const container = canvasRef.current
      if (!container || targetLayout.bounds.width <= 0) return
      const rect = container.getBoundingClientRect()
      if (rect.width <= 0 || rect.height <= 0) return

      const pad = 48
      const availW = Math.max(200, rect.width - pad * 2)
      const availH = Math.max(200, rect.height - pad * 2)
      const scaleX = availW / targetLayout.bounds.width
      const scaleY = availH / targetLayout.bounds.height
      const zoom = clampZoom(Math.min(1, scaleX, scaleY))

      const contentW = targetLayout.bounds.width * zoom
      const contentH = targetLayout.bounds.height * zoom
      const tx = (rect.width - contentW) / 2 - targetLayout.bounds.minX * zoom
      const ty =
        Math.max(32, (rect.height - contentH) / 2) -
        targetLayout.bounds.minY * zoom

      if (smooth) {
        setAnimateStage(true)
        window.setTimeout(() => setAnimateStage(false), 320)
      }
      setTransform({ tx, ty, zoom })
    },
    [layout],
  )

  // Fit on first ready render
  useEffect(() => {
    if (status === 'ready' && !didInitialFitRef.current && layout.bounds.width > 0) {
      didInitialFitRef.current = true
      fitToScreen(layout, false)
    }
  }, [status, layout, fitToScreen])

  // Non-passive wheel zoom centered on cursor
  useEffect(() => {
    const el = canvasRef.current
    if (!el) return
    const handleWheel = (event: WheelEvent) => {
      event.preventDefault()
      const rect = el.getBoundingClientRect()
      const mouseX = event.clientX - rect.left
      const mouseY = event.clientY - rect.top
      const deltaFactor = event.deltaY < 0 ? 1.1 : 0.9
      setTransform((prev) => {
        const nextZoom = clampZoom(prev.zoom * deltaFactor)
        if (nextZoom === prev.zoom) return prev
        const ratio = nextZoom / prev.zoom
        const tx = mouseX - (mouseX - prev.tx) * ratio
        const ty = mouseY - (mouseY - prev.ty) * ratio
        return { tx, ty, zoom: nextZoom }
      })
    }
    el.addEventListener('wheel', handleWheel, { passive: false })
    return () => el.removeEventListener('wheel', handleWheel)
  }, [status])

  const centerOnNodeById = useCallback(
    (
      targetId: string,
      customZoom = 1,
      nextExpandedIds?: Set<string>,
      nextUnassignedExpanded?: boolean,
      nextFocusRootId?: string | null,
    ) => {
      if (!data) return
      const targetLayout = computeOrgChartLayout({
        nodes: data.nodes,
        rootIds: data.root_ids,
        unassignedIds: data.unassigned_ids,
        expandedIds: nextExpandedIds ?? expandedIds,
        focusRootId:
          nextFocusRootId !== undefined ? nextFocusRootId : focusRootId,
        storeFilter,
        departmentFilter,
        visibleFields,
        unassignedExpanded:
          nextUnassignedExpanded !== undefined
            ? nextUnassignedExpanded
            : unassignedExpanded,
      })

      const positioned =
        targetLayout.nodes.find((n) => n.node.id === targetId) ??
        targetLayout.unassignedSection?.nodes.find((n) => n.node.id === targetId)

      const container = canvasRef.current
      if (!positioned || !container) return

      const rect = container.getBoundingClientRect()
      const zoom = clampZoom(customZoom)
      const cx = positioned.x + positioned.width / 2
      const cy = positioned.y + positioned.height / 2
      const tx = rect.width / 2 - cx * zoom
      const ty = rect.height / 2 - cy * zoom

      setAnimateStage(true)
      window.setTimeout(() => setAnimateStage(false), 320)
      setTransform({ tx, ty, zoom })
    },
    [
      data,
      expandedIds,
      focusRootId,
      storeFilter,
      departmentFilter,
      visibleFields,
      unassignedExpanded,
    ],
  )

  const revealAndFocusNode = useCallback(
    (targetId: string, openDrawer = false) => {
      if (!data || !nodeMap.has(targetId)) return
      const nextExpanded = new Set(expandedIds)
      let curr = nodeMap.get(targetId)?.manager_id ?? null
      const seen = new Set<string>([targetId])
      let isUnderFocusRoot = focusRootId === null || targetId === focusRootId
      while (curr && nodeMap.has(curr) && !seen.has(curr)) {
        seen.add(curr)
        nextExpanded.add(curr)
        if (focusRootId && curr === focusRootId) {
          isUnderFocusRoot = true
        }
        curr = nodeMap.get(curr)?.manager_id ?? null
      }

      const nextFocusRoot = isUnderFocusRoot ? focusRootId : null
      const isUnassigned = data.unassigned_ids.includes(targetId)
      const nextUnassignedExpanded = isUnassigned ? true : unassignedExpanded

      setExpandedIds(nextExpanded)
      if (isUnassigned) setUnassignedExpanded(true)
      if (!isUnderFocusRoot) setFocusRootId(null)
      setHighlightedNodeId(targetId)
      setSelectedNodeId(targetId)
      if (openDrawer) setDrawerOpen(true)

      centerOnNodeById(
        targetId,
        1,
        nextExpanded,
        nextUnassignedExpanded,
        nextFocusRoot,
      )
      const node = nodeMap.get(targetId)
      if (node) {
        setStatusAnnouncement(
          t('orgChart.announcements.located', { name: node.full_name }),
        )
      }
    },
    [data, nodeMap, expandedIds, focusRootId, unassignedExpanded, centerOnNodeById, t],
  )

  const searchResults = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    if (!q || !data) return []
    return data.nodes
      .filter(
        (n) =>
          n.full_name.toLowerCase().includes(q) ||
          n.job_title.toLowerCase().includes(q) ||
          n.department_name.toLowerCase().includes(q) ||
          n.store_name.toLowerCase().includes(q),
      )
      .slice(0, 8)
  }, [data, searchQuery])

  const handleSearchKeyDown = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !isImeComposing(e)) {
      e.preventDefault()
      if (searchResults.length > 0) {
        revealAndFocusNode(searchResults[0].id, false)
        setSearchOpen(false)
      }
    }
  }

  const handleToggleExpand = (nodeId: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      if (next.has(nodeId)) next.delete(nodeId)
      else next.add(nodeId)
      return next
    })
  }

  const handleExpandNextLevel = () => {
    const next = new Set(expandedIds)
    let added = false
    for (const pn of layout.nodes) {
      if (pn.hasVisibleChildren && !pn.isExpanded) {
        next.add(pn.node.id)
        added = true
      }
    }
    if (added) {
      setExpandedIds(next)
      setStatusAnnouncement(t('orgChart.announcements.expandedLevel'))
    }
  }

  const handleCollapseOneLevel = () => {
    const expandable = new Set<string>()
    for (const pn of layout.nodes) {
      if (pn.isExpanded) {
        const children = childrenMap.get(pn.node.id) ?? []
        const hasExpandedChild = children.some((cid) => expandedIds.has(cid))
        if (!hasExpandedChild) {
          expandable.add(pn.node.id)
        }
      }
    }
    if (expandable.size > 0) {
      const next = new Set(expandedIds)
      for (const id of expandable) {
        next.delete(id)
      }
      setExpandedIds(next)
      setStatusAnnouncement(t('orgChart.announcements.collapsedLevel'))
    }
  }

  const handleResetHome = () => {
    if (!data) return
    const nextExpanded = new Set(data.root_ids)
    setFocusRootId(null)
    setExpandedIds(nextExpanded)
    setHighlightedNodeId(null)
    const nextLayout = computeOrgChartLayout({
      nodes: data.nodes,
      rootIds: data.root_ids,
      unassignedIds: data.unassigned_ids,
      expandedIds: nextExpanded,
      focusRootId: null,
      storeFilter,
      departmentFilter,
      visibleFields,
      unassignedExpanded,
    })
    fitToScreen(nextLayout, true)
    setStatusAnnouncement(t('orgChart.announcements.resetView'))
  }

  const handleLocateMe = () => {
    const selfId = data?.current_employee_id
    if (!selfId || !nodeMap.has(selfId)) {
      setStatusAnnouncement(t('orgChart.announcements.locateMeUnavailable'))
      return
    }
    revealAndFocusNode(selfId, false)
  }

  const handleFocusMyTeam = () => {
    const selfId = data?.current_employee_id
    if (!selfId || !nodeMap.has(selfId)) {
      setStatusAnnouncement(t('orgChart.announcements.myTeamUnavailable'))
      setOpenPanel(null)
      return
    }
    const selfNode = nodeMap.get(selfId)!
    const targetId =
      selfNode.direct_reports_count > 0
        ? selfNode.id
        : selfNode.manager_id && nodeMap.has(selfNode.manager_id)
          ? selfNode.manager_id
          : null

    if (!targetId) {
      setStatusAnnouncement(t('orgChart.announcements.myTeamUnavailable'))
      setOpenPanel(null)
      return
    }

    const nextExpanded = new Set(expandedIds)
    nextExpanded.add(targetId)
    setExpandedIds(nextExpanded)
    setFocusRootId(targetId)
    setHighlightedNodeId(selfId)
    setOpenPanel(null)
    centerOnNodeById(targetId, 1, nextExpanded, unassignedExpanded, targetId)
    setStatusAnnouncement(
      t('orgChart.announcements.focusedBranch', {
        name: nodeMap.get(targetId)?.full_name ?? '',
      }),
    )
  }

  const handleFocusSelected = () => {
    if (!selectedNodeId || !nodeMap.has(selectedNodeId)) return
    const nextExpanded = new Set(expandedIds)
    nextExpanded.add(selectedNodeId)
    setExpandedIds(nextExpanded)
    setFocusRootId(selectedNodeId)
    setOpenPanel(null)
    centerOnNodeById(
      selectedNodeId,
      1,
      nextExpanded,
      unassignedExpanded,
      selectedNodeId,
    )
    setStatusAnnouncement(
      t('orgChart.announcements.focusedBranch', {
        name: nodeMap.get(selectedNodeId)?.full_name ?? '',
      }),
    )
  }

  const handleZoomBy = (delta: number) => {
    const el = canvasRef.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    const cx = rect.width / 2
    const cy = rect.height / 2
    setTransform((prev) => {
      const nextZoom = clampZoom(prev.zoom + delta)
      if (nextZoom === prev.zoom) return prev
      const ratio = nextZoom / prev.zoom
      return {
        tx: cx - (cx - prev.tx) * ratio,
        ty: cy - (cy - prev.ty) * ratio,
        zoom: nextZoom,
      }
    })
  }

  const handleCanvasPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    const target = e.target as HTMLElement | null
    if (target?.closest('[data-interactive="true"], button, a, input')) {
      return
    }
    e.currentTarget.setPointerCapture(e.pointerId)
    panRef.current = {
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      originTx: transform.tx,
      originTy: transform.ty,
    }
    setIsPanning(true)
  }

  const handleCanvasPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const pan = panRef.current
    if (!pan || pan.pointerId !== e.pointerId) return
    const dx = e.clientX - pan.startX
    const dy = e.clientY - pan.startY
    setTransform((prev) => ({
      ...prev,
      tx: pan.originTx + dx,
      ty: pan.originTy + dy,
    }))
  }

  const handleCanvasPointerUp = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (panRef.current && panRef.current.pointerId === e.pointerId) {
      panRef.current = null
      setIsPanning(false)
    }
  }

  const handleExport = async (format: 'png' | 'svg') => {
    if (!data || exporting) return
    setExporting(true)
    setOpenPanel(null)
    try {
      const exportDateLabel = new Intl.DateTimeFormat(locale, {
        dateStyle: 'medium',
      }).format(new Date())
      const params = {
        layout,
        tenantTradeName: activeTenant?.tenant_trade_name ?? t('brand'),
        exportDateLabel,
        visibleFields,
        unassignedTitle: t('orgChart.unassigned.title'),
        totalEmployeesLabel: t('orgChart.activeCount', {
          count: data.total_active_employees,
        }),
      }
      if (format === 'svg') {
        exportOrgChartSvg(params)
        setStatusAnnouncement(t('orgChart.announcements.exportedSvg'))
      } else {
        await exportOrgChartPng(params)
        setStatusAnnouncement(t('orgChart.announcements.exportedPng'))
      }
    } finally {
      setExporting(false)
    }
  }

  const activeFiltersCount =
    (storeFilter !== 'all' ? 1 : 0) + (departmentFilter !== undefined ? 1 : 0)

  const isNodeHighlightMatch = (node: OrgChartNodePublic): boolean => {
    if (highlightTarget.type === 'department') {
      return normalizeDepartment(node.department_name) === highlightTarget.key
    }
    if (highlightTarget.type === 'store') {
      return node.store_id === highlightTarget.id
    }
    return false
  }

  const selectedDrawerNode = selectedNodeId
    ? (nodeMap.get(selectedNodeId) ?? null)
    : null

  const renderNodeCard = (item: PositionedOrgNode) => {
    const { node, x, y, width, height, isBridge, hasVisibleChildren, isExpanded } =
      item
    const isHighlighted = highlightedNodeId === node.id
    const isSelected = selectedNodeId === node.id
    const dimActive = highlightTarget.type !== 'none'
    const dimMatch = dimActive && isNodeHighlightMatch(node)

    return (
      <div
        key={node.id}
        style={{
          left: `${x}px`,
          top: `${y}px`,
          width: `${width}px`,
          height: `${height}px`,
        }}
        className="absolute select-none"
      >
        <div
          role="button"
          tabIndex={0}
          data-interactive="true"
          onClick={() => {
            setSelectedNodeId(node.id)
            setDrawerOpen(true)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              setSelectedNodeId(node.id)
              setDrawerOpen(true)
            }
          }}
          className={cn(
            'group relative flex h-full w-full cursor-pointer flex-col rounded-xl border bg-white p-3.5 text-left shadow-sm focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:outline-none motion-safe:transition-all',
            isBridge
              ? 'border-dashed border-slate-300 bg-slate-50/80 opacity-65'
              : 'border-slate-200/80 hover:border-blue-300',
            (isHighlighted || isSelected || dimMatch) &&
              'border-blue-600 ring-2 ring-blue-600',
            dimMatch && 'bg-blue-50/20',
            dimActive && !dimMatch && 'opacity-35',
          )}
        >
          {/* Card Header */}
          <div className="flex items-center gap-2.5">
            <UserAvatar
              avatarUrl={node.avatar_url}
              name={node.full_name}
              size="sm"
              className="h-9 w-9"
            />
            <div className="min-w-0 flex-1">
              <div className="truncate text-[13px] font-semibold text-slate-900">
                {node.full_name}
              </div>
              {isBridge && (
                <span className="inline-block text-[10px] font-medium text-slate-600">
                  {t('orgChart.bridgeLabel')}
                </span>
              )}
            </div>
          </div>

          {/* Customized Visible Fields */}
          <div className="mt-2 space-y-1 text-[11.5px] text-slate-600">
            {visibleFields.jobTitle && (
              <div className="flex items-center gap-1.5">
                <Briefcase
                  className="h-3.5 w-3.5 shrink-0 text-slate-500"
                  aria-hidden="true"
                />
                <span className="truncate">{node.job_title || '—'}</span>
              </div>
            )}
            {visibleFields.department && (
              <div className="flex items-center gap-1.5">
                <Building2
                  className="h-3.5 w-3.5 shrink-0 text-slate-500"
                  aria-hidden="true"
                />
                <span className="truncate">{node.department_name || '—'}</span>
              </div>
            )}
            {visibleFields.store && (
              <div className="flex items-center gap-1.5">
                <StoreIcon
                  className="h-3.5 w-3.5 shrink-0 text-slate-500"
                  aria-hidden="true"
                />
                <span className="truncate">{node.store_name || '—'}</span>
              </div>
            )}
            {visibleFields.workEmail && (
              <div className="flex items-center gap-1.5">
                <Mail
                  className="h-3.5 w-3.5 shrink-0 text-slate-500"
                  aria-hidden="true"
                />
                <span className="truncate">{node.work_email || '—'}</span>
              </div>
            )}
          </div>

          {/* Bottom-center direct (total) capsule */}
          <div className="mt-auto flex justify-center pt-1.5">
            {hasVisibleChildren ? (
              <button
                type="button"
                data-interactive="true"
                aria-expanded={isExpanded}
                aria-label={
                  isExpanded
                    ? t('orgChart.collapseNode', { name: node.full_name })
                    : t('orgChart.expandNode', { name: node.full_name })
                }
                onClick={(e) => {
                  e.stopPropagation()
                  handleToggleExpand(node.id)
                }}
                className="inline-flex items-center gap-1 rounded-full border border-slate-200/80 bg-slate-100 px-2.5 py-0.5 text-[11px] font-medium text-slate-700 tabular-nums hover:border-blue-200 hover:bg-blue-50 hover:text-blue-700 focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:outline-none motion-safe:transition-colors"
              >
                <span>
                  {node.direct_reports_count} ({node.total_reports_count})
                </span>
                {isExpanded ? (
                  <ChevronUp className="h-3 w-3" aria-hidden="true" />
                ) : (
                  <ChevronDown className="h-3 w-3" aria-hidden="true" />
                )}
              </button>
            ) : (
              <span className="inline-flex items-center rounded-full border border-slate-200/80 bg-slate-100 px-2.5 py-0.5 text-[11px] font-medium text-slate-700 tabular-nums">
                {node.direct_reports_count} ({node.total_reports_count})
              </span>
            )}
          </div>
        </div>
      </div>
    )
  }

  if (status === 'loading') {
    return (
      <div className="space-y-3">
        <Skeleton className="h-12 w-full rounded-xl" />
        <Skeleton className="h-[560px] w-full rounded-xl" />
      </div>
    )
  }

  if (status === 'failed') {
    return (
      <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-rose-200 bg-white p-10 text-center shadow-sm">
        <AlertCircle className="h-8 w-8 text-rose-600" aria-hidden="true" />
        <div className="space-y-1">
          <h2 className="text-sm font-semibold text-slate-900">
            {t('orgChart.loadFailed')}
          </h2>
          <p className="text-xs text-slate-600">
            {errorMessage || t('common.error')}
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void loadOrgChart()}
          className="gap-1.5"
        >
          <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
          <span>{t('orgChart.retry')}</span>
        </Button>
      </div>
    )
  }

  if (!data || data.nodes.length === 0) {
    return (
      <EmptyState
        icon={Users}
        title={t('orgChart.emptyTitle')}
        description={t('orgChart.emptyDescription')}
      />
    )
  }

  return (
    <div className="space-y-3">
      {/* Persistent SR-only status live region outside toolbars */}
      <p role="status" className="sr-only">
        {statusAnnouncement}
      </p>

      {/* Top Compact Toolbar */}
      <div
        ref={toolbarRef}
        role="toolbar"
        aria-label={t('orgChart.toolbar.label')}
        className="relative flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-200/80 bg-white p-2.5 shadow-sm"
      >
        <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
          {/* Group 1: Search & Locate */}
          <div
            ref={searchBoxRef}
            role="group"
            aria-label={t('orgChart.toolbar.searchGroup')}
            className="relative w-full sm:w-auto"
          >
            <Search
              className="pointer-events-none absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-slate-500"
              aria-hidden="true"
            />
            <Input
              type="search"
              value={searchQuery}
              onFocus={() => setSearchOpen(true)}
              onChange={(e) => {
                setSearchQuery(e.target.value)
                setSearchOpen(true)
              }}
              onKeyDown={handleSearchKeyDown}
              placeholder={t('orgChart.toolbar.searchPlaceholder')}
              aria-label={t('orgChart.toolbar.searchPlaceholder')}
              className="h-8 w-full pl-8 text-xs sm:w-64"
            />

            {searchOpen && searchQuery.trim().length > 0 && (
              <div className="absolute top-9 left-0 z-40 w-72 rounded-xl border border-slate-200 bg-white p-1.5 shadow-md">
                {searchResults.length === 0 ? (
                  <p className="px-3 py-2 text-xs text-slate-600">
                    {t('orgChart.toolbar.noSearchResults')}
                  </p>
                ) : (
                  <div className="max-h-64 space-y-0.5 overflow-y-auto">
                    {searchResults.map((item) => (
                      <button
                        key={item.id}
                        type="button"
                        onClick={() => {
                          revealAndFocusNode(item.id, false)
                          setSearchOpen(false)
                        }}
                        className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left hover:bg-blue-50/60 focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:outline-none"
                      >
                        <UserAvatar
                          avatarUrl={item.avatar_url}
                          name={item.full_name}
                          size="xs"
                        />
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-xs font-semibold text-slate-900">
                            {item.full_name}
                          </div>
                          <div className="truncate text-[11px] text-slate-600">
                            {[item.job_title, item.store_name || item.department_name]
                              .filter(Boolean)
                              .join(' · ') || '—'}
                          </div>
                        </div>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          <div
            role="separator"
            aria-orientation="vertical"
            className="hidden h-5 w-px bg-slate-200 sm:block"
          />

          {/* Group 2: View & Highlight Controls */}
          <div
            role="group"
            aria-label={t('orgChart.toolbar.viewGroup')}
            className="flex flex-wrap items-center gap-1.5"
          >
            {/* Customize Card Fields */}
            <div className="relative">
              <Button
                type="button"
                variant="outline"
                size="sm"
                aria-expanded={openPanel === 'customize'}
                onClick={() =>
                  setOpenPanel((p) => (p === 'customize' ? null : 'customize'))
                }
                className="h-8 gap-1.5 bg-white text-xs text-slate-700"
              >
                <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden="true" />
                <span>{t('orgChart.toolbar.customize')}</span>
              </Button>
              {openPanel === 'customize' && (
                <div className="absolute top-9 left-0 z-40 w-56 rounded-xl border border-slate-200 bg-white p-3 shadow-md">
                  <div className="mb-2 text-xs font-semibold text-slate-900">
                    {t('orgChart.customize.title')}
                  </div>
                  <div className="space-y-2">
                    {(
                      [
                        ['jobTitle', t('orgChart.fields.jobTitle')],
                        ['department', t('orgChart.fields.department')],
                        ['store', t('orgChart.fields.store')],
                        ['workEmail', t('orgChart.fields.workEmail')],
                      ] as const
                    ).map(([key, label]) => (
                      <label
                        key={key}
                        className="flex cursor-pointer items-center gap-2 text-xs text-slate-700"
                      >
                        <Checkbox
                          checked={visibleFields[key]}
                          onCheckedChange={(checked) =>
                            setVisibleFields((prev) => ({
                              ...prev,
                              [key]: Boolean(checked),
                            }))
                          }
                        />
                        <span>{label}</span>
                      </label>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Highlight by Dimension */}
            <div className="relative">
              <Button
                type="button"
                variant="outline"
                size="sm"
                aria-expanded={openPanel === 'highlight'}
                onClick={() =>
                  setOpenPanel((p) => (p === 'highlight' ? null : 'highlight'))
                }
                className={cn(
                  'h-8 gap-1.5 bg-white text-xs',
                  highlightTarget.type !== 'none'
                    ? 'border-blue-300 bg-blue-50/70 text-[#2563EB]'
                    : 'text-slate-700',
                )}
              >
                <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
                <span>{t('orgChart.toolbar.highlight')}</span>
              </Button>
              {openPanel === 'highlight' && (
                <div className="absolute top-9 left-0 z-40 w-64 rounded-xl border border-slate-200 bg-white p-3 shadow-md">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-xs font-semibold text-slate-900">
                      {t('orgChart.highlight.title')}
                    </span>
                    {highlightTarget.type !== 'none' && (
                      <button
                        type="button"
                        onClick={() => setHighlightTarget({ type: 'none' })}
                        className="text-[11px] font-medium text-[#2563EB] hover:underline"
                      >
                        {t('orgChart.highlight.clear')}
                      </button>
                    )}
                  </div>
                  <div className="space-y-3">
                    <div className="space-y-1">
                      <Label className="text-[11px] text-slate-600">
                        {t('orgChart.fields.department')}
                      </Label>
                      <Select
                        value={
                          highlightTarget.type === 'department'
                            ? `dept:${highlightTarget.key}`
                            : 'none'
                        }
                        onValueChange={(val) => {
                          if (val === 'none') {
                            setHighlightTarget({ type: 'none' })
                          } else {
                            const key = val.slice('dept:'.length)
                            const label =
                              departmentOptions.find(([k]) => k === key)?.[1] ??
                              key
                            setHighlightTarget({
                              type: 'department',
                              key,
                              label,
                            })
                          }
                        }}
                      >
                        <SelectTrigger className="h-8 bg-white text-xs">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="none">
                            {t('orgChart.highlight.none')}
                          </SelectItem>
                          {departmentOptions.map(([key, label]) => (
                            <SelectItem key={key} value={`dept:${key}`}>
                              {label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>

                    <div className="space-y-1">
                      <Label className="text-[11px] text-slate-600">
                        {t('orgChart.fields.store')}
                      </Label>
                      <Select
                        value={
                          highlightTarget.type === 'store'
                            ? `store:${highlightTarget.id}`
                            : 'none'
                        }
                        onValueChange={(val) => {
                          if (val === 'none') {
                            setHighlightTarget({ type: 'none' })
                          } else {
                            const id = val.slice('store:'.length)
                            const label =
                              storeOptions.find(([sid]) => sid === id)?.[1] ??
                              id
                            setHighlightTarget({ type: 'store', id, label })
                          }
                        }}
                      >
                        <SelectTrigger className="h-8 bg-white text-xs">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="none">
                            {t('orgChart.highlight.none')}
                          </SelectItem>
                          {storeOptions.map(([id, label]) => (
                            <SelectItem key={id} value={`store:${id}`}>
                              {label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Focus Branch */}
            <div className="relative">
              <Button
                type="button"
                variant="outline"
                size="sm"
                aria-expanded={openPanel === 'focus'}
                onClick={() =>
                  setOpenPanel((p) => (p === 'focus' ? null : 'focus'))
                }
                className={cn(
                  'h-8 gap-1.5 bg-white text-xs',
                  focusRootId
                    ? 'border-blue-300 bg-blue-50/70 text-[#2563EB]'
                    : 'text-slate-700',
                )}
              >
                <Focus className="h-3.5 w-3.5" aria-hidden="true" />
                <span>{t('orgChart.toolbar.focus')}</span>
              </Button>
              {openPanel === 'focus' && (
                <div className="absolute top-9 left-0 z-40 w-60 rounded-xl border border-slate-200 bg-white p-2 shadow-md">
                  <button
                    type="button"
                    onClick={handleFocusMyTeam}
                    className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs font-medium text-slate-700 hover:bg-blue-50/60 hover:text-[#2563EB] focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:outline-none"
                  >
                    <Users className="h-3.5 w-3.5" aria-hidden="true" />
                    <span>{t('orgChart.focus.myDirectTeam')}</span>
                  </button>
                  <button
                    type="button"
                    aria-disabled={!selectedNodeId}
                    onClick={() => {
                      if (!selectedNodeId) return
                      handleFocusSelected()
                    }}
                    className={cn(
                      'flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs font-medium focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:outline-none',
                      selectedNodeId
                        ? 'text-slate-700 hover:bg-blue-50/60 hover:text-[#2563EB]'
                        : 'cursor-not-allowed text-slate-400',
                    )}
                  >
                    <Crosshair className="h-3.5 w-3.5" aria-hidden="true" />
                    <span>{t('orgChart.focus.selectedPerson')}</span>
                  </button>
                </div>
              )}
            </div>

            {/* Filter */}
            <div className="relative">
              <Button
                type="button"
                variant="outline"
                size="sm"
                aria-expanded={openPanel === 'filter'}
                onClick={() =>
                  setOpenPanel((p) => (p === 'filter' ? null : 'filter'))
                }
                className={cn(
                  'h-8 gap-1.5 bg-white text-xs',
                  activeFiltersCount > 0
                    ? 'border-blue-300 bg-blue-50/70 text-[#2563EB]'
                    : 'text-slate-700',
                )}
              >
                <Filter className="h-3.5 w-3.5" aria-hidden="true" />
                <span>{t('orgChart.toolbar.filter')}</span>
                {activeFiltersCount > 0 && (
                  <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-[#2563EB] px-1 text-[10px] font-semibold text-white tabular-nums">
                    {activeFiltersCount}
                  </span>
                )}
              </Button>
              {openPanel === 'filter' && (
                <div className="absolute top-9 left-0 z-40 w-64 rounded-xl border border-slate-200 bg-white p-3 shadow-md">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-xs font-semibold text-slate-900">
                      {t('orgChart.filter.title')}
                    </span>
                    {activeFiltersCount > 0 && (
                      <button
                        type="button"
                        onClick={() => {
                          setStoreFilter('all')
                          setDepartmentFilter(undefined)
                        }}
                        className="text-[11px] font-medium text-[#2563EB] hover:underline"
                      >
                        {t('orgChart.filter.clearAll')}
                      </button>
                    )}
                  </div>
                  <div className="space-y-3">
                    <div className="space-y-1">
                      <Label className="text-[11px] text-slate-600">
                        {t('orgChart.fields.store')}
                      </Label>
                      <Select value={storeFilter} onValueChange={setStoreFilter}>
                        <SelectTrigger className="h-8 bg-white text-xs">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="all">
                            {t('dashboard.allStores')}
                          </SelectItem>
                          {storeOptions.map(([id, label]) => (
                            <SelectItem key={id} value={id}>
                              {label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>

                    <div className="space-y-1">
                      <Label className="text-[11px] text-slate-600">
                        {t('orgChart.fields.department')}
                      </Label>
                      <Select
                        value={
                          departmentFilter === undefined
                            ? 'all'
                            : `dept:${departmentFilter}`
                        }
                        onValueChange={(val) =>
                          setDepartmentFilter(
                            val === 'all' ? undefined : val.slice('dept:'.length),
                          )
                        }
                      >
                        <SelectTrigger className="h-8 bg-white text-xs">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="all">
                            {t('orgChart.filter.allDepartments')}
                          </SelectItem>
                          {departmentOptions.map(([key, label]) => (
                            <SelectItem key={key} value={`dept:${key}`}>
                              {label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>

          <span className="hidden px-1 text-xs font-medium text-slate-600 tabular-nums lg:inline">
            {t('orgChart.activeCount', { count: data.total_active_employees })}
          </span>
        </div>

        {/* Right-aligned Export */}
        <div
          role="group"
          aria-label={t('orgChart.toolbar.exportGroup')}
          className="relative"
        >
          <Button
            type="button"
            variant="outline"
            size="sm"
            aria-expanded={openPanel === 'export'}
            aria-disabled={exporting}
            onClick={() => {
              if (exporting) return
              setOpenPanel((p) => (p === 'export' ? null : 'export'))
            }}
            className="h-8 gap-1.5 bg-white text-xs text-slate-700"
          >
            <Download className="h-3.5 w-3.5" aria-hidden="true" />
            <span>{t('orgChart.toolbar.export')}</span>
          </Button>
          {openPanel === 'export' && (
            <div className="absolute top-9 right-0 z-40 w-52 rounded-xl border border-slate-200 bg-white p-1.5 shadow-md">
              <button
                type="button"
                onClick={() => void handleExport('png')}
                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs font-medium text-slate-700 hover:bg-blue-50/60 hover:text-[#2563EB] focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:outline-none"
              >
                <Download className="h-3.5 w-3.5" aria-hidden="true" />
                <span>{t('orgChart.export.png')}</span>
              </button>
              <button
                type="button"
                onClick={() => void handleExport('svg')}
                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs font-medium text-slate-700 hover:bg-blue-50/60 hover:text-[#2563EB] focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:outline-none"
              >
                <Download className="h-3.5 w-3.5" aria-hidden="true" />
                <span>{t('orgChart.export.svg')}</span>
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Active Focus / Filter Indicators */}
      {(focusRootId || activeFiltersCount > 0 || highlightTarget.type !== 'none') && (
        <div className="flex flex-wrap items-center gap-2">
          {focusRootId && (
            <div className="inline-flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs font-medium text-[#2563EB]">
              <Focus className="h-3.5 w-3.5" aria-hidden="true" />
              <span>
                {t('orgChart.focus.banner', {
                  name: nodeMap.get(focusRootId)?.full_name ?? '',
                })}
              </span>
              <button
                type="button"
                onClick={handleResetHome}
                className="ml-1 rounded bg-[#2563EB] px-2 py-0.5 text-[11px] font-semibold text-white hover:bg-blue-700"
              >
                {t('orgChart.focus.resetFullView')}
              </button>
            </div>
          )}

          {storeFilter !== 'all' && (
            <Badge
              variant="outline"
              className="gap-1 border-blue-200 bg-blue-50 text-xs text-[#2563EB]"
            >
              <span>
                {t('orgChart.fields.store')}:{' '}
                {storeOptions.find(([id]) => id === storeFilter)?.[1] ??
                  storeFilter}
              </span>
              <button
                type="button"
                onClick={() => setStoreFilter('all')}
                aria-label={t('orgChart.filter.removeStore')}
              >
                <X className="h-3 w-3" aria-hidden="true" />
              </button>
            </Badge>
          )}

          {departmentFilter !== undefined && (
            <Badge
              variant="outline"
              className="gap-1 border-blue-200 bg-blue-50 text-xs text-[#2563EB]"
            >
              <span>
                {t('orgChart.fields.department')}:{' '}
                {departmentOptions.find(([k]) => k === departmentFilter)?.[1] ??
                  departmentFilter}
              </span>
              <button
                type="button"
                onClick={() => setDepartmentFilter(undefined)}
                aria-label={t('orgChart.filter.removeDepartment')}
              >
                <X className="h-3 w-3" aria-hidden="true" />
              </button>
            </Badge>
          )}

          {highlightTarget.type !== 'none' && (
            <Badge
              variant="outline"
              className="gap-1 border-blue-200 bg-blue-50 text-xs text-[#2563EB]"
            >
              <span>
                {t('orgChart.highlight.activeBadge', {
                  label: highlightTarget.label,
                })}
              </span>
              <button
                type="button"
                onClick={() => setHighlightTarget({ type: 'none' })}
                aria-label={t('orgChart.highlight.clear')}
              >
                <X className="h-3 w-3" aria-hidden="true" />
              </button>
            </Badge>
          )}
        </div>
      )}

      {/* Interactive Pan & Zoom Canvas */}
      <div
        ref={canvasRef}
        onPointerDown={handleCanvasPointerDown}
        onPointerMove={handleCanvasPointerMove}
        onPointerUp={handleCanvasPointerUp}
        onPointerCancel={handleCanvasPointerUp}
        style={{
          backgroundColor: '#F3F5F9',
          backgroundImage:
            'radial-gradient(circle, rgba(148,163,184,0.34) 1px, transparent 1px)',
          backgroundSize: '20px 20px',
        }}
        className={cn(
          'relative h-[calc(100vh-270px)] min-h-[560px] w-full overflow-hidden rounded-xl border border-slate-200/80 shadow-sm select-none',
          isPanning ? 'cursor-grabbing' : 'cursor-grab',
        )}
      >
        {layout.nodes.length === 0 && !layout.unassignedSection ? (
          <div className="flex h-full items-center justify-center p-6">
            <EmptyState
              icon={Users}
              title={t('orgChart.emptyFilteredTitle')}
              description={t('orgChart.emptyFilteredDescription')}
            />
          </div>
        ) : (
          <div
            style={{
              transform: `translate3d(${transform.tx}px, ${transform.ty}px, 0) scale(${transform.zoom})`,
              transformOrigin: '0 0',
            }}
            className={cn(
              'absolute top-0 left-0 will-change-transform',
              animateStage && 'motion-safe:transition-transform motion-safe:duration-300',
            )}
          >
            {/* Orthogonal SVG Connector Layer */}
            <svg
              width={Math.max(1, layout.bounds.maxX - Math.min(0, layout.bounds.minX) + 200)}
              height={Math.max(1, layout.bounds.maxY - Math.min(0, layout.bounds.minY) + 200)}
              style={{
                left: `${Math.min(0, layout.bounds.minX) - 100}px`,
                top: `${Math.min(0, layout.bounds.minY) - 100}px`,
              }}
              className="pointer-events-none absolute overflow-visible"
              aria-hidden="true"
            >
              <g
                transform={`translate(${100 - Math.min(0, layout.bounds.minX)}, ${100 - Math.min(0, layout.bounds.minY)})`}
              >
                {layout.connectors.map((c) => (
                  <path
                    key={c.id}
                    d={c.d}
                    stroke="#CBD5E1"
                    strokeWidth={1.5}
                    fill="none"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                ))}
              </g>
            </svg>

            {/* Main Tree Cards */}
            {layout.nodes.map(renderNodeCard)}

            {/* Collapsible Unassigned Reporting Line Group */}
            {layout.unassignedSection && (
              <>
                <div
                  style={{
                    left: `${layout.unassignedSection.x}px`,
                    top: `${layout.unassignedSection.y}px`,
                    width: `${layout.unassignedSection.width}px`,
                    height: `${layout.unassignedSection.height}px`,
                  }}
                  className="absolute rounded-xl border border-dashed border-slate-300 bg-white/75 shadow-sm"
                >
                  <button
                    type="button"
                    data-interactive="true"
                    aria-expanded={unassignedExpanded}
                    onClick={() => setUnassignedExpanded((v) => !v)}
                    className="flex h-12 w-full items-center justify-between px-5 text-left text-xs font-semibold text-slate-700 hover:text-[#2563EB] focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:outline-none"
                  >
                    <span className="inline-flex items-center gap-2">
                      {unassignedExpanded ? (
                        <ChevronDown className="h-4 w-4" aria-hidden="true" />
                      ) : (
                        <ChevronRight className="h-4 w-4" aria-hidden="true" />
                      )}
                      <span>{t('orgChart.unassigned.title')}</span>
                    </span>
                    <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-[11px] font-semibold text-slate-700 tabular-nums">
                      {layout.unassignedSection.totalCount}
                    </span>
                  </button>
                </div>
                {layout.unassignedSection.nodes.map(renderNodeCard)}
              </>
            )}
          </div>
        )}

        {/* Bottom-Right Floating Canvas Control Bar */}
        <div
          role="toolbar"
          aria-label={t('orgChart.controls.label')}
          data-interactive="true"
          className="absolute right-4 bottom-4 z-20 flex flex-wrap items-center gap-1 rounded-xl border border-slate-200/80 bg-white p-1.5 shadow-sm"
        >
          {/* Zoom Group */}
          <div
            role="group"
            aria-label={t('orgChart.controls.zoomGroup')}
            className="flex items-center gap-0.5"
          >
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-disabled={transform.zoom <= MIN_ZOOM}
              aria-label={t('orgChart.controls.zoomOut')}
              title={t('orgChart.controls.zoomOut')}
              onClick={() => {
                if (transform.zoom <= MIN_ZOOM) return
                handleZoomBy(-ZOOM_STEP)
              }}
              className="h-7 w-7 p-0 text-slate-700"
            >
              <Minus className="h-3.5 w-3.5" aria-hidden="true" />
            </Button>
            <span className="min-w-11 text-center text-xs font-medium text-slate-700 tabular-nums">
              {Math.round(transform.zoom * 100)}%
            </span>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-disabled={transform.zoom >= MAX_ZOOM}
              aria-label={t('orgChart.controls.zoomIn')}
              title={t('orgChart.controls.zoomIn')}
              onClick={() => {
                if (transform.zoom >= MAX_ZOOM) return
                handleZoomBy(ZOOM_STEP)
              }}
              className="h-7 w-7 p-0 text-slate-700"
            >
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
            </Button>
          </div>

          <div
            role="separator"
            aria-orientation="vertical"
            className="h-4 w-px bg-slate-200"
          />

          {/* Navigation & Depth Group */}
          <div
            role="group"
            aria-label={t('orgChart.controls.navigationGroup')}
            className="flex items-center gap-0.5"
          >
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label={t('orgChart.controls.fit')}
              title={t('orgChart.controls.fit')}
              onClick={() => fitToScreen(layout, true)}
              className="h-7 w-7 p-0 text-slate-700"
            >
              <Maximize2 className="h-3.5 w-3.5" aria-hidden="true" />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-disabled={!data.current_employee_id}
              aria-label={t('orgChart.controls.locateMe')}
              title={t('orgChart.controls.locateMe')}
              onClick={handleLocateMe}
              className={cn(
                'h-7 w-7 p-0',
                data.current_employee_id
                  ? 'text-slate-700 hover:text-[#2563EB]'
                  : 'cursor-not-allowed text-slate-400',
              )}
            >
              <Crosshair className="h-3.5 w-3.5" aria-hidden="true" />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label={t('orgChart.controls.expandLevel')}
              title={t('orgChart.controls.expandLevel')}
              onClick={handleExpandNextLevel}
              className="h-7 w-7 p-0 text-slate-700"
            >
              <ChevronsDown className="h-3.5 w-3.5" aria-hidden="true" />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label={t('orgChart.controls.collapseLevel')}
              title={t('orgChart.controls.collapseLevel')}
              onClick={handleCollapseOneLevel}
              className="h-7 w-7 p-0 text-slate-700"
            >
              <ChevronsUp className="h-3.5 w-3.5" aria-hidden="true" />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label={t('orgChart.controls.resetHome')}
              title={t('orgChart.controls.resetHome')}
              onClick={handleResetHome}
              className="h-7 w-7 p-0 text-slate-700"
            >
              <Home className="h-3.5 w-3.5" aria-hidden="true" />
            </Button>
          </div>
        </div>
      </div>

      {/* Role-Graded Public Card Drawer */}
      <OrgChartPublicDrawer
        node={selectedDrawerNode}
        allNodes={data.nodes}
        open={drawerOpen}
        onOpenChange={setDrawerOpen}
        onSelectNode={(nodeId) => revealAndFocusNode(nodeId, true)}
        onFocusBranch={(nodeId) => {
          const nextExpanded = new Set(expandedIds)
          nextExpanded.add(nodeId)
          setExpandedIds(nextExpanded)
          setFocusRootId(nodeId)
          setHighlightedNodeId(nodeId)
          centerOnNodeById(
            nodeId,
            1,
            nextExpanded,
            unassignedExpanded,
            nodeId,
          )
        }}
        onOpenFullProfile={onOpenFullProfile}
      />
    </div>
  )
}

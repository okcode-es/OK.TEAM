import type { OrgChartNodePublic } from '@/api/types'
import { normalizeDepartment } from '@/features/employees/department'

export interface OrgChartVisibleFields {
  jobTitle: boolean
  department: boolean
  store: boolean
  workEmail: boolean
}

export interface PositionedOrgNode {
  node: OrgChartNodePublic
  x: number
  y: number
  width: number
  height: number
  isBridge: boolean
  hasVisibleChildren: boolean
  isExpanded: boolean
}

export interface OrgChartConnector {
  id: string
  d: string
}

export interface UnassignedSectionLayout {
  x: number
  y: number
  width: number
  height: number
  headerHeight: number
  totalCount: number
  expanded: boolean
  nodes: PositionedOrgNode[]
}

export interface OrgChartLayoutResult {
  nodes: PositionedOrgNode[]
  connectors: OrgChartConnector[]
  unassignedSection: UnassignedSectionLayout | null
  bounds: {
    minX: number
    minY: number
    maxX: number
    maxY: number
    width: number
    height: number
  }
  visibleNodeIds: Set<string>
}

export interface ComputeOrgChartLayoutParams {
  nodes: OrgChartNodePublic[]
  rootIds: string[]
  unassignedIds: string[]
  expandedIds: Set<string>
  focusRootId: string | null
  storeFilter: string
  departmentFilter: string | undefined
  visibleFields: OrgChartVisibleFields
  unassignedExpanded: boolean
}

export const ORG_CARD_WIDTH = 240
const H_GAP = 28
const V_GAP = 56
const GRID_ROW_GAP = 40
const ROOT_GAP = 64
const GRID_MAX_COLS = 5
const UNASSIGNED_HEADER_HEIGHT = 48
const UNASSIGNED_PADDING = 20

export function computeOrgCardHeight(visibleFields: OrgChartVisibleFields): number {
  const fieldCount =
    (visibleFields.jobTitle ? 1 : 0) +
    (visibleFields.department ? 1 : 0) +
    (visibleFields.store ? 1 : 0) +
    (visibleFields.workEmail ? 1 : 0)
  return Math.max(118, 58 + fieldCount * 20 + 34)
}

interface SubtreeMeta {
  id: string
  childrenIds: string[]
  useCompactGrid: boolean
  subtreeWidth: number
  childrenTotalWidth: number
  gridCols: number
  gridRows: number
}

export function computeOrgChartLayout({
  nodes,
  rootIds,
  unassignedIds,
  expandedIds,
  focusRootId,
  storeFilter,
  departmentFilter,
  visibleFields,
  unassignedExpanded,
}: ComputeOrgChartLayoutParams): OrgChartLayoutResult {
  const cardHeight = computeOrgCardHeight(visibleFields)
  const nodeMap = new Map<string, OrgChartNodePublic>()
  for (const n of nodes) {
    nodeMap.set(n.id, n)
  }

  // Build parent -> children adjacency list preserving server order
  const childrenMap = new Map<string, string[]>()
  for (const n of nodes) {
    if (n.manager_id && nodeMap.has(n.manager_id)) {
      const list = childrenMap.get(n.manager_id)
      if (list) {
        list.push(n.id)
      } else {
        childrenMap.set(n.manager_id, [n.id])
      }
    }
  }

  const hasActiveFilter =
    storeFilter !== 'all' || departmentFilter !== undefined

  const matchesFilter = (n: OrgChartNodePublic): boolean => {
    if (storeFilter !== 'all' && n.store_id !== storeFilter) return false
    if (
      departmentFilter !== undefined &&
      normalizeDepartment(n.department_name) !== departmentFilter
    ) {
      return false
    }
    return true
  }

  // Determine which nodes are kept in the tree when filters are active
  // (matching nodes + their ancestors as bridge nodes)
  const keptInTree = new Set<string>()
  const matchedSet = new Set<string>()
  for (const n of nodes) {
    if (!hasActiveFilter || matchesFilter(n)) {
      matchedSet.add(n.id)
      keptInTree.add(n.id)
      let curr = n.manager_id
      const seen = new Set<string>([n.id])
      while (curr && nodeMap.has(curr) && !seen.has(curr)) {
        seen.add(curr)
        keptInTree.add(curr)
        curr = nodeMap.get(curr)?.manager_id ?? null
      }
    }
  }

  const effectiveRoots =
    focusRootId && nodeMap.has(focusRootId)
      ? [focusRootId]
      : rootIds.filter((id) => nodeMap.has(id) && keptInTree.has(id))

  const getVisibleChildren = (id: string): string[] => {
    const raw = childrenMap.get(id) ?? []
    return raw.filter((cid) => keptInTree.has(cid))
  }

  const metaMap = new Map<string, SubtreeMeta>()
  const visitedForMeta = new Set<string>()

  const measureSubtree = (id: string): SubtreeMeta => {
    const existing = metaMap.get(id)
    if (existing) return existing
    if (visitedForMeta.has(id)) {
      const fallback: SubtreeMeta = {
        id,
        childrenIds: [],
        useCompactGrid: false,
        subtreeWidth: ORG_CARD_WIDTH,
        childrenTotalWidth: 0,
        gridCols: 0,
        gridRows: 0,
      }
      return fallback
    }
    visitedForMeta.add(id)

    const allVisibleChildren = getVisibleChildren(id)
    const isExpanded = expandedIds.has(id)
    const visibleChildren = isExpanded ? allVisibleChildren : []

    if (visibleChildren.length === 0) {
      const leaf: SubtreeMeta = {
        id,
        childrenIds: [],
        useCompactGrid: false,
        subtreeWidth: ORG_CARD_WIDTH,
        childrenTotalWidth: 0,
        gridCols: 0,
        gridRows: 0,
      }
      metaMap.set(id, leaf)
      return leaf
    }

    // Anti-Explosion 5-Column Compact Grid Rule:
    // > 5 visible direct reports AND none of those direct reports are themselves expanded with visible children
    const anyChildExpanded = visibleChildren.some(
      (cid) => expandedIds.has(cid) && getVisibleChildren(cid).length > 0,
    )
    const useCompactGrid =
      visibleChildren.length > GRID_MAX_COLS && !anyChildExpanded

    if (useCompactGrid) {
      const gridCols = GRID_MAX_COLS
      const gridRows = Math.ceil(visibleChildren.length / gridCols)
      const gridWidth =
        gridCols * ORG_CARD_WIDTH + (gridCols - 1) * H_GAP
      // Add 24px left gutter allowance when multi-row so trunk connector never collides with sibling trees
      const totalWidth = gridRows > 1 ? gridWidth + 24 : gridWidth
      const meta: SubtreeMeta = {
        id,
        childrenIds: visibleChildren,
        useCompactGrid: true,
        subtreeWidth: Math.max(ORG_CARD_WIDTH, totalWidth),
        childrenTotalWidth: gridWidth,
        gridCols,
        gridRows,
      }
      metaMap.set(id, meta)
      return meta
    }

    let childrenSum = 0
    for (let i = 0; i < visibleChildren.length; i++) {
      const cMeta = measureSubtree(visibleChildren[i])
      childrenSum += cMeta.subtreeWidth
      if (i > 0) childrenSum += H_GAP
    }

    const meta: SubtreeMeta = {
      id,
      childrenIds: visibleChildren,
      useCompactGrid: false,
      subtreeWidth: Math.max(ORG_CARD_WIDTH, childrenSum),
      childrenTotalWidth: childrenSum,
      gridCols: 0,
      gridRows: 0,
    }
    metaMap.set(id, meta)
    return meta
  }

  for (const rId of effectiveRoots) {
    measureSubtree(rId)
  }

  const positionedNodes: PositionedOrgNode[] = []
  const connectors: OrgChartConnector[] = []
  const visibleNodeIds = new Set<string>()
  const placedIds = new Set<string>()

  const placeSubtree = (id: string, leftX: number, topY: number) => {
    if (placedIds.has(id)) return
    const node = nodeMap.get(id)
    const meta = metaMap.get(id)
    if (!node || !meta) return
    placedIds.add(id)
    visibleNodeIds.add(id)

    const ux = leftX + (meta.subtreeWidth - ORG_CARD_WIDTH) / 2
    const uy = topY
    const allVisibleChildren = getVisibleChildren(id)
    const isExpanded = expandedIds.has(id)

    positionedNodes.push({
      node,
      x: ux,
      y: uy,
      width: ORG_CARD_WIDTH,
      height: cardHeight,
      isBridge: hasActiveFilter && !matchedSet.has(id),
      hasVisibleChildren: allVisibleChildren.length > 0,
      isExpanded: isExpanded && allVisibleChildren.length > 0,
    })

    if (meta.childrenIds.length === 0) return

    const pCenterX = ux + ORG_CARD_WIDTH / 2
    const pBottomY = uy + cardHeight

    if (meta.useCompactGrid) {
      const gridLeftX =
        leftX +
        (meta.subtreeWidth - meta.childrenTotalWidth) / 2 +
        (meta.gridRows > 1 ? 12 : 0)
      const gridTopY = pBottomY + V_GAP
      const row0BusY = pBottomY + Math.round(V_GAP / 2)
      const trunkX = gridLeftX - 18

      const rowCards: Array<Array<{ id: string; x: number; y: number; cx: number }>> = []

      for (let i = 0; i < meta.childrenIds.length; i++) {
        const cid = meta.childrenIds[i]
        const cNode = nodeMap.get(cid)
        if (!cNode || placedIds.has(cid)) continue
        placedIds.add(cid)
        visibleNodeIds.add(cid)

        const row = Math.floor(i / meta.gridCols)
        const col = i % meta.gridCols
        const cx = gridLeftX + col * (ORG_CARD_WIDTH + H_GAP)
        const cy = gridTopY + row * (cardHeight + GRID_ROW_GAP)

        const cVisibleChildren = getVisibleChildren(cid)
        positionedNodes.push({
          node: cNode,
          x: cx,
          y: cy,
          width: ORG_CARD_WIDTH,
          height: cardHeight,
          isBridge: hasActiveFilter && !matchedSet.has(cid),
          hasVisibleChildren: cVisibleChildren.length > 0,
          isExpanded: false,
        })

        if (!rowCards[row]) rowCards[row] = []
        rowCards[row].push({
          id: cid,
          x: cx,
          y: cy,
          cx: cx + ORG_CARD_WIDTH / 2,
        })
      }

      // Row 0 orthogonal connectors
      const row0 = rowCards[0] ?? []
      if (row0.length > 0) {
        const minCx = Math.min(
          row0[0].cx,
          rowCards.length > 1 ? trunkX : row0[0].cx,
        )
        const maxCx = row0[row0.length - 1].cx
        connectors.push({
          id: `${id}-grid-trunk-top`,
          d: `M ${pCenterX} ${pBottomY} V ${row0BusY}`,
        })
        connectors.push({
          id: `${id}-grid-row0-bar`,
          d: `M ${minCx} ${row0BusY} H ${maxCx}`,
        })
        for (const item of row0) {
          connectors.push({
            id: `${id}-to-${item.id}`,
            d: `M ${item.cx} ${row0BusY} V ${item.y}`,
          })
        }
      }

      // Subsequent rows (1..rows-1) routed via left gutter trunkX
      if (rowCards.length > 1) {
        const lastRow = rowCards[rowCards.length - 1]
        const lastRowBusY = lastRow[0].y - Math.round(GRID_ROW_GAP / 2)
        connectors.push({
          id: `${id}-grid-side-trunk`,
          d: `M ${trunkX} ${row0BusY} V ${lastRowBusY}`,
        })

        for (let r = 1; r < rowCards.length; r++) {
          const rItems = rowCards[r]
          if (!rItems || rItems.length === 0) continue
          const busY = rItems[0].y - Math.round(GRID_ROW_GAP / 2)
          const rMaxCx = rItems[rItems.length - 1].cx
          connectors.push({
            id: `${id}-grid-row${r}-bar`,
            d: `M ${trunkX} ${busY} H ${rMaxCx}`,
          })
          for (const item of rItems) {
            connectors.push({
              id: `${id}-to-${item.id}`,
              d: `M ${item.cx} ${busY} V ${item.y}`,
            })
          }
        }
      }

      return
    }

    // Standard horizontal tree layout
    let childLeftX = leftX + (meta.subtreeWidth - meta.childrenTotalWidth) / 2
    const childTopY = pBottomY + V_GAP
    const midY = pBottomY + Math.round(V_GAP / 2)

    for (const cid of meta.childrenIds) {
      const cMeta = metaMap.get(cid)
      if (!cMeta) continue
      const cCenterX = childLeftX + cMeta.subtreeWidth / 2
      connectors.push({
        id: `${id}-to-${cid}`,
        d: `M ${pCenterX} ${pBottomY} V ${midY} H ${cCenterX} V ${childTopY}`,
      })
      placeSubtree(cid, childLeftX, childTopY)
      childLeftX += cMeta.subtreeWidth + H_GAP
    }
  }

  let cursorX = 0
  for (let i = 0; i < effectiveRoots.length; i++) {
    const rId = effectiveRoots[i]
    const rMeta = metaMap.get(rId)
    if (!rMeta) continue
    placeSubtree(rId, cursorX, 0)
    cursorX += rMeta.subtreeWidth + ROOT_GAP
  }

  // Compute main tree bounding box before adding unassigned section
  let minX = positionedNodes.length > 0 ? Infinity : 0
  let minY = positionedNodes.length > 0 ? Infinity : 0
  let maxX = positionedNodes.length > 0 ? -Infinity : ORG_CARD_WIDTH
  let maxY = positionedNodes.length > 0 ? -Infinity : 0

  for (const pn of positionedNodes) {
    minX = Math.min(minX, pn.x)
    minY = Math.min(minY, pn.y)
    maxX = Math.max(maxX, pn.x + pn.width)
    maxY = Math.max(maxY, pn.y + pn.height)
  }

  // Unassigned group (only when not focused on a specific branch)
  let unassignedSection: UnassignedSectionLayout | null = null
  const filteredUnassignedIds = focusRootId
    ? []
    : unassignedIds.filter((id) => {
        const n = nodeMap.get(id)
        return n && (!hasActiveFilter || matchesFilter(n))
      })

  if (filteredUnassignedIds.length > 0) {
    const cols = Math.min(GRID_MAX_COLS, filteredUnassignedIds.length)
    const rows = Math.ceil(filteredUnassignedIds.length / GRID_MAX_COLS)
    const gridWidth = cols * ORG_CARD_WIDTH + Math.max(0, cols - 1) * H_GAP
    const sectionWidth = Math.max(360, gridWidth + UNASSIGNED_PADDING * 2)
    const treeCenterX =
      positionedNodes.length > 0 ? (minX + maxX) / 2 : sectionWidth / 2
    const sectionX = treeCenterX - sectionWidth / 2
    const sectionY = positionedNodes.length > 0 ? maxY + 64 : 0

    const uNodes: PositionedOrgNode[] = []
    if (unassignedExpanded) {
      const cardsStartX = sectionX + (sectionWidth - gridWidth) / 2
      const cardsStartY = sectionY + UNASSIGNED_HEADER_HEIGHT + 12

      for (let i = 0; i < filteredUnassignedIds.length; i++) {
        const uid = filteredUnassignedIds[i]
        const uNode = nodeMap.get(uid)
        if (!uNode) continue
        visibleNodeIds.add(uid)
        const r = Math.floor(i / GRID_MAX_COLS)
        const c = i % GRID_MAX_COLS
        uNodes.push({
          node: uNode,
          x: cardsStartX + c * (ORG_CARD_WIDTH + H_GAP),
          y: cardsStartY + r * (cardHeight + 20),
          width: ORG_CARD_WIDTH,
          height: cardHeight,
          isBridge: false,
          hasVisibleChildren: false,
          isExpanded: false,
        })
      }
    }

    const sectionHeight = unassignedExpanded
      ? UNASSIGNED_HEADER_HEIGHT +
        12 +
        rows * cardHeight +
        Math.max(0, rows - 1) * 20 +
        UNASSIGNED_PADDING
      : UNASSIGNED_HEADER_HEIGHT

    unassignedSection = {
      x: sectionX,
      y: sectionY,
      width: sectionWidth,
      height: sectionHeight,
      headerHeight: UNASSIGNED_HEADER_HEIGHT,
      totalCount: filteredUnassignedIds.length,
      expanded: unassignedExpanded,
      nodes: uNodes,
    }

    minX = Math.min(minX, sectionX)
    minY = Math.min(minY, sectionY)
    maxX = Math.max(maxX, sectionX + sectionWidth)
    maxY = Math.max(maxY, sectionY + sectionHeight)
  }

  if (positionedNodes.length === 0 && !unassignedSection) {
    return {
      nodes: [],
      connectors: [],
      unassignedSection: null,
      bounds: { minX: 0, minY: 0, maxX: 0, maxY: 0, width: 0, height: 0 },
      visibleNodeIds,
    }
  }

  return {
    nodes: positionedNodes,
    connectors,
    unassignedSection,
    bounds: {
      minX,
      minY,
      maxX,
      maxY,
      width: Math.max(1, maxX - minX),
      height: Math.max(1, maxY - minY),
    },
    visibleNodeIds,
  }
}

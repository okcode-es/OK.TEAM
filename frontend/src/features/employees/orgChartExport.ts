import type {
  OrgChartLayoutResult,
  OrgChartVisibleFields,
  PositionedOrgNode,
} from '@/features/employees/orgChartLayout'

interface ExportOrgChartParams {
  layout: OrgChartLayoutResult
  tenantTradeName: string
  exportDateLabel: string
  visibleFields: OrgChartVisibleFields
  unassignedTitle: string
  totalEmployeesLabel: string
}

function escapeXml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;')
}

function truncateText(value: string, maxChars: number): string {
  const trimmed = value.trim()
  if (trimmed.length <= maxChars) return trimmed
  return `${trimmed.slice(0, Math.max(1, maxChars - 1))}…`
}

function getInitials(name: string): string {
  const cleaned = name.trim()
  if (!cleaned) return '?'
  const parts = cleaned.split(/\s+/).filter(Boolean)
  if (parts.length >= 2) {
    return `${parts[0][0] ?? ''}${parts[parts.length - 1][0] ?? ''}`.toUpperCase()
  }
  return cleaned.slice(0, 2).toUpperCase()
}

function renderCardSvg(
  item: PositionedOrgNode,
  offsetX: number,
  offsetY: number,
  visibleFields: OrgChartVisibleFields,
): string {
  const x = item.x + offsetX
  const y = item.y + offsetY
  const { width, height, node, isBridge } = item
  const opacityAttr = isBridge ? ' opacity="0.6"' : ''

  const avatarCx = x + 32
  const avatarCy = y + 32
  const initials = escapeXml(getInitials(node.full_name))
  const fullName = escapeXml(truncateText(node.full_name, 22))

  const lines: string[] = []
  let lineY = y + 66

  if (visibleFields.jobTitle) {
    const label = escapeXml(truncateText(node.job_title || '—', 28))
    lines.push(
      `<text x="${x + 16}" y="${lineY}" font-size="11.5" fill="#475569">${label}</text>`,
    )
    lineY += 20
  }
  if (visibleFields.department) {
    const label = escapeXml(truncateText(node.department_name || '—', 28))
    lines.push(
      `<text x="${x + 16}" y="${lineY}" font-size="11.5" fill="#475569">${label}</text>`,
    )
    lineY += 20
  }
  if (visibleFields.store) {
    const label = escapeXml(truncateText(node.store_name || '—', 28))
    lines.push(
      `<text x="${x + 16}" y="${lineY}" font-size="11.5" fill="#475569">${label}</text>`,
    )
    lineY += 20
  }
  if (visibleFields.workEmail) {
    const label = escapeXml(truncateText(node.work_email || '—', 28))
    lines.push(
      `<text x="${x + 16}" y="${lineY}" font-size="11" fill="#475569">${label}</text>`,
    )
  }

  const pillWidth = 72
  const pillHeight = 20
  const pillX = x + (width - pillWidth) / 2
  const pillY = y + height - 26
  const countText = `${node.direct_reports_count} (${node.total_reports_count})`

  return `
    <g${opacityAttr}>
      <rect x="${x}" y="${y}" width="${width}" height="${height}" rx="12" fill="#FFFFFF" stroke="#E2E8F0" stroke-width="1" />
      <circle cx="${avatarCx}" cy="${avatarCy}" r="16" fill="#EFF6FF" stroke="#BFDBFE" stroke-width="1" />
      <text x="${avatarCx}" y="${avatarCy + 4}" text-anchor="middle" font-size="11" font-weight="600" fill="#1D4ED8">${initials}</text>
      <text x="${x + 56}" y="${y + 36}" font-size="13" font-weight="600" fill="#0F172A">${fullName}</text>
      ${lines.join('\n      ')}
      <rect x="${pillX}" y="${pillY}" width="${pillWidth}" height="${pillHeight}" rx="10" fill="#F1F5F9" stroke="#CBD5E1" stroke-width="1" />
      <text x="${pillX + pillWidth / 2}" y="${pillY + 13.5}" text-anchor="middle" font-size="11" font-weight="500" fill="#334155">${countText}</text>
    </g>
  `
}

export function buildOrgChartSvgDocument({
  layout,
  tenantTradeName,
  exportDateLabel,
  visibleFields,
  unassignedTitle,
  totalEmployeesLabel,
}: ExportOrgChartParams): { svg: string; width: number; height: number } {
  const padding = 40
  const headerHeight = 64
  const contentWidth = Math.max(640, Math.ceil(layout.bounds.width))
  const contentHeight = Math.max(320, Math.ceil(layout.bounds.height))
  const svgWidth = contentWidth + padding * 2
  const svgHeight = contentHeight + headerHeight + padding * 2

  const offsetX = padding - layout.bounds.minX
  const offsetY = padding + headerHeight - layout.bounds.minY

  const connectorPaths = layout.connectors
    .map(
      (c) =>
        `<path d="${c.d}" transform="translate(${offsetX}, ${offsetY})" stroke="#CBD5E1" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round" />`,
    )
    .join('\n  ')

  const mainCards = layout.nodes
    .map((n) => renderCardSvg(n, offsetX, offsetY, visibleFields))
    .join('\n  ')

  let unassignedSvg = ''
  if (layout.unassignedSection) {
    const us = layout.unassignedSection
    const ux = us.x + offsetX
    const uy = us.y + offsetY
    const uCards = us.nodes
      .map((n) => renderCardSvg(n, offsetX, offsetY, visibleFields))
      .join('\n  ')
    unassignedSvg = `
      <g>
        <rect x="${ux}" y="${uy}" width="${us.width}" height="${us.height}" rx="14" fill="#FFFFFF" fill-opacity="0.75" stroke="#CBD5E1" stroke-width="1" stroke-dasharray="4 3" />
        <text x="${ux + 20}" y="${uy + 29}" font-size="13" font-weight="600" fill="#334155">${escapeXml(unassignedTitle)} (${us.totalCount})</text>
        ${uCards}
      </g>
    `
  }

  const svg = `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="${svgWidth}" height="${svgHeight}" viewBox="0 0 ${svgWidth} ${svgHeight}" style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
  <rect width="${svgWidth}" height="${svgHeight}" fill="#F3F5F9" />
  <text x="${padding}" y="${padding + 22}" font-size="18" font-weight="700" fill="#0F172A">${escapeXml(tenantTradeName)}</text>
  <text x="${padding}" y="${padding + 42}" font-size="12" fill="#475569">${escapeXml(totalEmployeesLabel)} · ${escapeXml(exportDateLabel)}</text>
  <line x1="${padding}" y1="${padding + 54}" x2="${svgWidth - padding}" y2="${padding + 54}" stroke="#E2E8F0" stroke-width="1" />
  ${connectorPaths}
  ${mainCards}
  ${unassignedSvg}
</svg>`

  return { svg, width: svgWidth, height: svgHeight }
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function exportOrgChartSvg(params: ExportOrgChartParams): void {
  const { svg } = buildOrgChartSvgDocument(params)
  const dateStamp = new Date().toISOString().slice(0, 10)
  const blob = new Blob([svg], { type: 'image/svg+xml;charset=utf-8' })
  triggerDownload(blob, `organigrama-${dateStamp}.svg`)
}

export async function exportOrgChartPng(
  params: ExportOrgChartParams,
): Promise<void> {
  const { svg, width, height } = buildOrgChartSvgDocument(params)
  const dateStamp = new Date().toISOString().slice(0, 10)

  const svgBlob = new Blob([svg], { type: 'image/svg+xml;charset=utf-8' })
  const svgUrl = URL.createObjectURL(svgBlob)

  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const image = new Image()
      image.onload = () => resolve(image)
      image.onerror = () => reject(new Error('Failed to render SVG image'))
      image.src = svgUrl
    })

    const scale = 2
    const canvas = document.createElement('canvas')
    canvas.width = width * scale
    canvas.height = height * scale
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new Error('Canvas 2D context unavailable')

    ctx.fillStyle = '#F3F5F9'
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.scale(scale, scale)
    ctx.drawImage(img, 0, 0, width, height)

    const pngBlob = await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob((blob) => {
        if (blob) resolve(blob)
        else reject(new Error('Failed to encode PNG'))
      }, 'image/png')
    })
    triggerDownload(pngBlob, `organigrama-${dateStamp}.png`)
  } finally {
    URL.revokeObjectURL(svgUrl)
  }
}

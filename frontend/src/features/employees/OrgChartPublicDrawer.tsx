import { useState } from 'react'
import {
  Briefcase,
  Building2,
  Check,
  Copy,
  Crosshair,
  ExternalLink,
  Mail,
  Store as StoreIcon,
  UserCheck,
  Users,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { OrgChartNodePublic } from '@/api/types'
import { UserAvatar } from '@/components/common/UserAvatar'
import { Button } from '@/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { useAuth } from '@/features/auth/AuthContext'
import { normalizeDepartment } from '@/features/employees/department'

interface OrgChartPublicDrawerProps {
  node: OrgChartNodePublic | null
  allNodes: OrgChartNodePublic[]
  open: boolean
  onOpenChange: (open: boolean) => void
  onSelectNode: (nodeId: string) => void
  onFocusBranch?: (nodeId: string) => void
  onOpenFullProfile?: (employeeId: string) => void
}

export function OrgChartPublicDrawer({
  node,
  allNodes,
  open,
  onOpenChange,
  onSelectNode,
  onFocusBranch,
  onOpenFullProfile,
}: OrgChartPublicDrawerProps) {
  const { t } = useTranslation()
  const { activeTenant } = useAuth()
  const [copiedEmail, setCopiedEmail] = useState(false)

  if (!node) {
    return (
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent side="right" className="w-full bg-white sm:max-w-md" />
      </Sheet>
    )
  }

  const managerNode = node.manager_id
    ? (allNodes.find((n) => n.id === node.manager_id) ?? null)
    : null
  const directReports = allNodes.filter((n) => n.manager_id === node.id)

  const role = activeTenant?.role
  const canOpenFullProfile = (() => {
    if (!onOpenFullProfile || !activeTenant) return false
    if (role === 'owner' || role === 'hr') return true
    if (role === 'store_manager') {
      const storeAllowed = activeTenant.store_ids.includes(node.store_id)
      const deptScope = activeTenant.department_scope?.trim()
      const deptAllowed =
        !deptScope ||
        normalizeDepartment(node.department_name) ===
          normalizeDepartment(deptScope)
      return storeAllowed && deptAllowed
    }
    return false
  })()

  const handleCopyEmail = async () => {
    if (!node.work_email) return
    try {
      await navigator.clipboard.writeText(node.work_email)
      setCopiedEmail(true)
      window.setTimeout(() => setCopiedEmail(false), 1800)
    } catch {
      // Clipboard unavailable in insecure context
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="flex w-full flex-col overscroll-contain bg-white sm:max-w-md"
      >
        <SheetHeader className="border-b border-slate-200 px-6 py-4">
          <div className="flex items-center gap-3.5 pr-6">
            <UserAvatar
              avatarUrl={node.avatar_url}
              name={node.full_name}
              size="lg"
            />
            <div className="min-w-0">
              <SheetTitle className="truncate text-base font-semibold text-slate-900">
                {node.full_name}
              </SheetTitle>
              <SheetDescription className="truncate text-xs text-slate-600">
                {node.job_title || t('orgChart.drawer.noJobTitle')}
              </SheetDescription>
            </div>
          </div>
        </SheetHeader>

        <div className="flex-1 space-y-5 overflow-y-auto px-6 py-5">
          {/* Quick action: Focus branch */}
          {onFocusBranch && (
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => {
                  onFocusBranch(node.id)
                  onOpenChange(false)
                }}
                className="h-8 gap-1.5 border-slate-200 bg-white text-xs text-slate-700 hover:bg-slate-50"
              >
                <Crosshair className="h-3.5 w-3.5 text-[#2563EB]" aria-hidden="true" />
                <span>{t('orgChart.drawer.focusBranch')}</span>
              </Button>
            </div>
          )}

          {/* Public Details */}
          <section className="space-y-3 rounded-xl border border-slate-200/80 bg-slate-50/50 p-4">
            <div className="flex items-start gap-2.5 text-xs">
              <Briefcase
                className="mt-0.5 h-4 w-4 shrink-0 text-slate-500"
                aria-hidden="true"
              />
              <div className="min-w-0">
                <div className="text-slate-600">
                  {t('orgChart.fields.jobTitle')}
                </div>
                <div className="font-medium text-slate-900">
                  {node.job_title || '—'}
                </div>
              </div>
            </div>

            <div className="flex items-start gap-2.5 text-xs">
              <Building2
                className="mt-0.5 h-4 w-4 shrink-0 text-slate-500"
                aria-hidden="true"
              />
              <div className="min-w-0">
                <div className="text-slate-600">
                  {t('orgChart.fields.department')}
                </div>
                <div className="font-medium text-slate-900">
                  {node.department_name || '—'}
                </div>
              </div>
            </div>

            <div className="flex items-start gap-2.5 text-xs">
              <StoreIcon
                className="mt-0.5 h-4 w-4 shrink-0 text-slate-500"
                aria-hidden="true"
              />
              <div className="min-w-0">
                <div className="text-slate-600">
                  {t('orgChart.fields.store')}
                </div>
                <div className="font-medium text-slate-900">
                  {node.store_name || '—'}
                </div>
              </div>
            </div>

            <div className="flex items-start justify-between gap-2 text-xs">
              <div className="flex min-w-0 items-start gap-2.5">
                <Mail
                  className="mt-0.5 h-4 w-4 shrink-0 text-slate-500"
                  aria-hidden="true"
                />
                <div className="min-w-0">
                  <div className="text-slate-600">
                    {t('orgChart.fields.workEmail')}
                  </div>
                  {node.work_email ? (
                    <a
                      href={`mailto:${node.work_email}`}
                      className="font-medium break-all text-[#2563EB] hover:underline"
                    >
                      {node.work_email}
                    </a>
                  ) : (
                    <div className="font-medium text-slate-900">—</div>
                  )}
                </div>
              </div>
              {node.work_email && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => void handleCopyEmail()}
                  aria-label={t('orgChart.drawer.copyEmail')}
                  title={t('orgChart.drawer.copyEmail')}
                  className="h-7 w-7 shrink-0 p-0 text-slate-600 hover:text-slate-900"
                >
                  {copiedEmail ? (
                    <Check className="h-3.5 w-3.5 text-emerald-600" aria-hidden="true" />
                  ) : (
                    <Copy className="h-3.5 w-3.5" aria-hidden="true" />
                  )}
                </Button>
              )}
            </div>
          </section>

          {/* Direct Manager */}
          <section className="space-y-2">
            <h3 className="flex items-center gap-1.5 text-xs font-semibold tracking-wide text-slate-600 uppercase">
              <UserCheck className="h-3.5 w-3.5 text-slate-500" aria-hidden="true" />
              <span>{t('orgChart.drawer.directManager')}</span>
            </h3>
            {managerNode ? (
              <button
                type="button"
                onClick={() => onSelectNode(managerNode.id)}
                className="flex w-full items-center gap-3 rounded-xl border border-slate-200/80 bg-white p-3 text-left transition-colors hover:border-blue-300 hover:bg-blue-50/40 focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none"
              >
                <UserAvatar
                  avatarUrl={managerNode.avatar_url}
                  name={managerNode.full_name}
                  size="sm"
                />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-xs font-semibold text-slate-900">
                    {managerNode.full_name}
                  </div>
                  <div className="truncate text-[11px] text-slate-600">
                    {managerNode.job_title || managerNode.department_name || '—'}
                  </div>
                </div>
              </button>
            ) : (
              <p className="rounded-lg border border-dashed border-slate-200 px-3 py-2.5 text-xs text-slate-600">
                {t('orgChart.drawer.noManager')}
              </p>
            )}
          </section>

          {/* Direct Reports */}
          <section className="space-y-2">
            <h3 className="flex items-center justify-between text-xs font-semibold tracking-wide text-slate-600 uppercase">
              <span className="inline-flex items-center gap-1.5">
                <Users className="h-3.5 w-3.5 text-slate-500" aria-hidden="true" />
                <span>{t('orgChart.drawer.directReports')}</span>
              </span>
              <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-semibold text-slate-700 tabular-nums">
                {directReports.length}
              </span>
            </h3>
            {directReports.length === 0 ? (
              <p className="rounded-lg border border-dashed border-slate-200 px-3 py-2.5 text-xs text-slate-600">
                {t('orgChart.drawer.noReports')}
              </p>
            ) : (
              <div className="space-y-2">
                {directReports.map((rep) => (
                  <button
                    key={rep.id}
                    type="button"
                    onClick={() => onSelectNode(rep.id)}
                    className="flex w-full items-center gap-3 rounded-xl border border-slate-200/80 bg-white p-2.5 text-left transition-colors hover:border-blue-300 hover:bg-blue-50/40 focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none"
                  >
                    <UserAvatar
                      avatarUrl={rep.avatar_url}
                      name={rep.full_name}
                      size="sm"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-xs font-semibold text-slate-900">
                        {rep.full_name}
                      </div>
                      <div className="truncate text-[11px] text-slate-600">
                        {rep.job_title || rep.store_name || '—'}
                      </div>
                    </div>
                    {rep.total_reports_count > 0 && (
                      <span className="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-700 tabular-nums">
                        {rep.direct_reports_count} ({rep.total_reports_count})
                      </span>
                    )}
                  </button>
                ))}
              </div>
            )}
          </section>
        </div>

        {canOpenFullProfile && (
          <SheetFooter className="border-t border-slate-200 px-6 py-4">
            <Button
              type="button"
              onClick={() => {
                onOpenChange(false)
                onOpenFullProfile?.(node.id)
              }}
              className="w-full gap-2 bg-[#2563EB] text-white hover:bg-blue-700"
            >
              <span>{t('orgChart.drawer.openFullProfile')}</span>
              <ExternalLink className="h-4 w-4" aria-hidden="true" />
            </Button>
          </SheetFooter>
        )}
      </SheetContent>
    </Sheet>
  )
}

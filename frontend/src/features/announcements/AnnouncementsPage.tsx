import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import {
  CheckCircle2,
  Megaphone,
  Paperclip,
  Pin,
  Plus,
  ShieldCheck,
  Users,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  apiClient,
  extractApiError,
  extractFetchError,
  getApiBaseUrl,
  getAuthHeaders,
} from '@/api/client'
import type {
  AnnouncementAcknowledgementsReportResponse,
  AnnouncementCategory,
  AnnouncementResponse,
  StoreResponse,
} from '@/api/types'
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
import { Separator } from '@/components/ui/separator'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { useAuth } from '@/features/auth/AuthContext'
import { formatUtcDateTime, isImeComposing } from '@/lib/ime'
import { cn } from '@/lib/utils'

export function AnnouncementsPage() {
  const { t, i18n } = useTranslation()
  const { activeTenant } = useAuth()
  const locale = i18n.language || 'zh-CN'
  const role = activeTenant?.role
  const canPublish =
    role === 'owner' || role === 'hr' || role === 'store_manager'

  const [categoryFilter, setCategoryFilter] = useState<'all' | AnnouncementCategory>('all')
  const [items, setItems] = useState<AnnouncementResponse[]>([])
  const [stores, setStores] = useState<StoreResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [errorBanner, setErrorBanner] = useState<string | null>(null)
  const [noticeBanner, setNoticeBanner] = useState<string | null>(null)

  // Publish Sheet
  const [publishOpen, setPublishOpen] = useState(false)
  const [pubCategory, setPubCategory] = useState<AnnouncementCategory>('notice')
  const [pubTitle, setPubTitle] = useState('')
  const [pubBody, setPubBody] = useState('')
  const [pubStoreId, setPubStoreId] = useState<string>('all')
  const [pubDeptScope, setPubDeptScope] = useState('')
  const [pubRequiresAck, setPubRequiresAck] = useState(true)
  const [pubPinned, setPubPinned] = useState(false)
  const [pubFile, setPubFile] = useState<File | null>(null)
  const [publishing, setPublishing] = useState(false)
  const [publishError, setPublishError] = useState<string | null>(null)

  // Detail Sheet
  const [selected, setSelected] = useState<AnnouncementResponse | null>(null)
  const [ackReport, setAckReport] =
    useState<AnnouncementAcknowledgementsReportResponse | null>(null)
  const [ackBusy, setAckBusy] = useState(false)
  const [attachBusy, setAttachBusy] = useState(false)

  const loadData = useCallback(async () => {
    setLoading(true)
    setErrorBanner(null)
    try {
      const [annRes, storeRes] = await Promise.all([
        apiClient.GET('/api/v1/announcements', {
          params: {
            query: {
              category: categoryFilter !== 'all' ? categoryFilter : undefined,
            },
          },
        }),
        apiClient.GET('/api/v1/stores'),
      ])
      if (annRes.error || !annRes.data) {
        setErrorBanner(extractApiError(annRes.error, t('common.error')))
        return
      }
      setItems(annRes.data.items)
      if (storeRes.data) setStores(storeRes.data.items)
    } finally {
      setLoading(false)
    }
  }, [categoryFilter, t])

  useEffect(() => {
    setSelected(null)
    setAckReport(null)
    void loadData()
  }, [loadData, activeTenant?.tenant_id])

  const storeMap = useMemo(
    () => new Map(stores.map((s) => [s.id, s])),
    [stores],
  )

  const openDetail = async (item: AnnouncementResponse) => {
    setSelected(item)
    setAckReport(null)
    if (canPublish && item.requires_acknowledgement) {
      const res = await apiClient.GET(
        '/api/v1/announcements/{announcement_id}/acknowledgements',
        {
          params: { path: { announcement_id: item.id } },
        },
      )
      if (res.data) setAckReport(res.data)
    }
  }

  const handlePublish = async (e: FormEvent) => {
    e.preventDefault()
    if (!canPublish) return
    if (!pubTitle.trim() || !pubBody.trim()) {
      setPublishError(t('announcements.publishDrawer.requiredFields'))
      return
    }
    setPublishing(true)
    setPublishError(null)
    try {
      const formData = new FormData()
      formData.append('category', pubCategory)
      formData.append('title', pubTitle.trim())
      formData.append('body', pubBody.trim())
      if (pubStoreId !== 'all') formData.append('store_id', pubStoreId)
      if (pubDeptScope.trim()) {
        formData.append('department_scope', pubDeptScope.trim())
      }
      formData.append(
        'requires_acknowledgement',
        pubRequiresAck ? 'true' : 'false',
      )
      formData.append('is_pinned', pubPinned ? 'true' : 'false')
      if (pubFile) formData.append('file', pubFile)

      const headers = await getAuthHeaders()
      const res = await fetch(`${getApiBaseUrl()}/api/v1/announcements`, {
        method: 'POST',
        headers,
        body: formData,
      })
      if (!res.ok) {
        setPublishError(await extractFetchError(res, t('errors.GENERIC')))
        return
      }
      setPublishOpen(false)
      setPubTitle('')
      setPubBody('')
      setPubDeptScope('')
      setPubFile(null)
      setNoticeBanner(t('announcements.publishSuccess'))
      await loadData()
    } catch (err) {
      setPublishError(extractApiError(err, t('errors.GENERIC')))
    } finally {
      setPublishing(false)
    }
  }

  const handleAcknowledge = async (annId: string) => {
    setAckBusy(true)
    setNoticeBanner(null)
    try {
      const res = await apiClient.POST(
        '/api/v1/announcements/{announcement_id}/acknowledge',
        {
          params: { path: { announcement_id: annId } },
        },
      )
      if (res.error) {
        setErrorBanner(extractApiError(res.error, t('common.error')))
        return
      }
      setNoticeBanner(t('announcements.acknowledgedSuccess'))
      await loadData()
      const detailRes = await apiClient.GET(
        '/api/v1/announcements/{announcement_id}',
        {
          params: { path: { announcement_id: annId } },
        },
      )
      if (detailRes.data) {
        await openDetail(detailRes.data)
      }
    } finally {
      setAckBusy(false)
    }
  }

  const handleAttachmentDownload = async (annId: string) => {
    setAttachBusy(true)
    try {
      const res = await apiClient.POST(
        '/api/v1/announcements/{announcement_id}/attachment/signed-url',
        {
          params: { path: { announcement_id: annId } },
        },
      )
      if (res.data) {
        window.open(res.data.url, '_blank', 'noopener,noreferrer')
      }
    } finally {
      setAttachBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-lg font-semibold text-slate-900">
            {t('announcements.title')}
          </h1>
          <p className="text-sm text-slate-600">{t('announcements.subtitle')}</p>
        </div>

        {canPublish && (
          <Button
            type="button"
            onClick={() => {
              setPublishError(null)
              setPublishOpen(true)
            }}
            className="bg-slate-900 text-white hover:bg-slate-800"
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
            {t('announcements.publishButton')}
          </Button>
        )}
      </div>

      {noticeBanner && (
        <div
          role="status"
          className="rounded-md border border-emerald-200 bg-emerald-50 px-3.5 py-2 text-xs text-emerald-700"
        >
          {noticeBanner}
        </div>
      )}

      {errorBanner && (
        <div
          role="alert"
          className="rounded-md border border-rose-200 bg-rose-50 px-3.5 py-2 text-xs text-rose-700"
        >
          {errorBanner}
        </div>
      )}

      {/* Category Tabs */}
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-slate-200 bg-white p-2.5">
        <div className="flex flex-wrap gap-1">
          {(['all', 'notice', 'policy'] as const).map((cat) => (
            <button
              key={cat}
              type="button"
              onClick={() => setCategoryFilter(cat)}
              className={cn(
                'rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
                categoryFilter === cat
                  ? 'bg-slate-900 text-white'
                  : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900',
              )}
            >
              {t(`announcements.categories.${cat}`)}
            </button>
          ))}
        </div>
        <span className="px-2 text-xs text-slate-500 tabular-nums">
          {t('announcements.count', { count: items.length })}
        </span>
      </div>

      {/* Announcements List */}
      <div className="space-y-2.5">
        {loading ? (
          <div className="space-y-2">
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : items.length === 0 ? (
          <div className="rounded-md border border-slate-200 bg-white p-8 text-center text-sm text-slate-500">
            {t('announcements.empty')}
          </div>
        ) : (
          items.map((item) => {
            const store = item.store_id ? storeMap.get(item.store_id) : null
            return (
              <div
                key={item.id}
                onClick={() => void openDetail(item)}
                className="cursor-pointer rounded-md border border-slate-200 bg-white p-4 transition-colors hover:bg-slate-50"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="flex min-w-0 items-center gap-2">
                    {item.is_pinned && (
                      <Pin
                        className="h-4 w-4 shrink-0 text-[#2563EB]"
                        aria-hidden="true"
                      />
                    )}
                    <Megaphone
                      className="h-4 w-4 shrink-0 text-slate-400"
                      aria-hidden="true"
                    />
                    <h2 className="truncate text-sm font-semibold text-slate-900">
                      {item.title}
                    </h2>
                  </div>

                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge
                      variant="outline"
                      className="border-slate-200 bg-slate-50 text-slate-700"
                    >
                      {t(`announcements.categories.${item.category}`)}
                    </Badge>
                    {item.attachment_document_id && (
                      <Badge
                        variant="outline"
                        className="border-slate-200 bg-white text-slate-600"
                      >
                        <Paperclip className="mr-1 h-3 w-3" aria-hidden="true" />
                        PDF
                      </Badge>
                    )}
                    {item.requires_acknowledgement && (
                      <Badge
                        variant="outline"
                        className={cn(
                          'tabular-nums',
                          item.acknowledged_by_self
                            ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                            : 'border-amber-200 bg-amber-50 text-amber-800',
                        )}
                      >
                        {item.acknowledged_by_self
                          ? t('announcements.acknowledgedBadge')
                          : t('announcements.pendingAckBadge')}
                        {canPublish &&
                          ` (${item.acknowledged_count}/${item.target_count})`}
                      </Badge>
                    )}
                  </div>
                </div>

                <p className="mt-2 line-clamp-2 text-xs text-slate-600">
                  {item.body}
                </p>

                <div className="mt-3 flex flex-wrap items-center gap-3 text-[11px] text-slate-500 tabular-nums">
                  <span>{formatUtcDateTime(item.published_at_utc, locale)}</span>
                  <span>
                    {store
                      ? `${store.code} · ${store.name}`
                      : t('announcements.allStoresScope')}
                  </span>
                  {item.department_scope && (
                    <span>{item.department_scope}</span>
                  )}
                </div>
              </div>
            )
          })
        )}
      </div>

      {/* Publish Sheet */}
      <Sheet open={publishOpen} onOpenChange={setPublishOpen}>
        <SheetContent side="right" className="w-full overflow-y-auto bg-white sm:max-w-lg">
          <SheetHeader>
            <SheetTitle>{t('announcements.publishDrawer.title')}</SheetTitle>
            <SheetDescription>
              {t('announcements.publishDrawer.description')}
            </SheetDescription>
          </SheetHeader>

          <form
            onSubmit={(e) => void handlePublish(e)}
            className="mt-4 space-y-4 px-4"
          >
            {publishError && (
              <div
                role="alert"
                className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
              >
                {publishError}
              </div>
            )}

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label>{t('announcements.publishDrawer.category')}</Label>
                <Select
                  value={pubCategory}
                  onValueChange={(v) =>
                    setPubCategory(v as AnnouncementCategory)
                  }
                >
                  <SelectTrigger className="w-full bg-white">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="notice">
                      {t('announcements.categories.notice')}
                    </SelectItem>
                    <SelectItem value="policy">
                      {t('announcements.categories.policy')}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-1.5">
                <Label>{t('announcements.publishDrawer.storeScope')}</Label>
                <Select value={pubStoreId} onValueChange={setPubStoreId}>
                  <SelectTrigger className="w-full bg-white">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">
                      {t('announcements.allStoresScope')}
                    </SelectItem>
                    {stores.map((s) => (
                      <SelectItem key={s.id} value={s.id}>
                        {s.code} · {s.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="ann-dept">
                {t('announcements.publishDrawer.departmentScope')}
              </Label>
              <Input
                id="ann-dept"
                value={pubDeptScope}
                onChange={(e) => setPubDeptScope(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && isImeComposing(e)) e.preventDefault()
                }}
                placeholder={t('announcements.publishDrawer.departmentPlaceholder')}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="ann-title">
                {t('announcements.publishDrawer.docTitle')}
              </Label>
              <Input
                id="ann-title"
                required
                value={pubTitle}
                onChange={(e) => setPubTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && isImeComposing(e)) e.preventDefault()
                }}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="ann-body">
                {t('announcements.publishDrawer.body')}
              </Label>
              <textarea
                id="ann-body"
                rows={5}
                required
                value={pubBody}
                onChange={(e) => setPubBody(e.target.value)}
                className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none"
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="ann-file">
                {t('announcements.publishDrawer.attachment')}
              </Label>
              <Input
                id="ann-file"
                type="file"
                accept=".pdf,.jpg,.jpeg,.png,.webp,application/pdf,image/jpeg,image/png,image/webp"
                onChange={(e) => setPubFile(e.target.files?.[0] ?? null)}
              />
            </div>

            <div className="space-y-2 rounded-md border border-slate-200 bg-slate-50 p-3">
              <label className="flex items-center gap-2 text-xs text-slate-700">
                <Checkbox
                  checked={pubRequiresAck}
                  onCheckedChange={(v) => setPubRequiresAck(Boolean(v))}
                />
                <span>{t('announcements.publishDrawer.requiresAck')}</span>
              </label>
              <label className="flex items-center gap-2 text-xs text-slate-700">
                <Checkbox
                  checked={pubPinned}
                  onCheckedChange={(v) => setPubPinned(Boolean(v))}
                />
                <span>{t('announcements.publishDrawer.isPinned')}</span>
              </label>
            </div>

            <SheetFooter className="flex gap-2 px-0">
              <Button
                type="button"
                variant="outline"
                onClick={() => setPublishOpen(false)}
              >
                {t('announcements.publishDrawer.cancel')}
              </Button>
              <Button type="submit" disabled={publishing}>
                {publishing
                  ? t('announcements.publishDrawer.publishing')
                  : t('announcements.publishDrawer.submit')}
              </Button>
            </SheetFooter>
          </form>
        </SheetContent>
      </Sheet>

      {/* Detail & Acknowledgements Sheet */}
      <Sheet
        open={Boolean(selected)}
        onOpenChange={(open) => {
          if (!open) setSelected(null)
        }}
      >
        <SheetContent side="right" className="w-full overflow-y-auto bg-white sm:max-w-lg">
          <SheetHeader>
            <SheetTitle>{selected?.title}</SheetTitle>
            <SheetDescription>
              {selected && formatUtcDateTime(selected.published_at_utc, locale)}
            </SheetDescription>
          </SheetHeader>

          {selected && (
            <div className="mt-4 space-y-4 px-4">
              <div className="flex flex-wrap gap-2">
                <Badge variant="outline" className="border-slate-200 bg-slate-50">
                  {t(`announcements.categories.${selected.category}`)}
                </Badge>
                <Badge
                  variant="outline"
                  className="border-slate-200 bg-white font-mono text-[10px] text-slate-600 tabular-nums"
                >
                  SHA-256: {selected.content_sha256_hex.slice(0, 12)}…
                </Badge>
              </div>

              <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-sm whitespace-pre-wrap text-slate-800">
                {selected.body}
              </div>

              {selected.attachment_document_id && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={attachBusy}
                  onClick={() => void handleAttachmentDownload(selected.id)}
                >
                  <Paperclip className="h-4 w-4" aria-hidden="true" />
                  {t('announcements.downloadAttachment')}
                </Button>
              )}

              {selected.requires_acknowledgement && (
                <div className="space-y-2">
                  {selected.acknowledged_by_self ? (
                    <div className="flex items-center gap-2 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700">
                      <CheckCircle2 className="h-4 w-4 shrink-0" aria-hidden="true" />
                      <span>{t('announcements.alreadyAcknowledged')}</span>
                    </div>
                  ) : (
                    <Button
                      type="button"
                      disabled={ackBusy}
                      onClick={() => void handleAcknowledge(selected.id)}
                      className="w-full bg-slate-900 text-white hover:bg-slate-800"
                    >
                      <ShieldCheck className="h-4 w-4" aria-hidden="true" />
                      {t('announcements.acknowledgeBtn')}
                    </Button>
                  )}
                </div>
              )}

              {ackReport && (
                <>
                  <Separator />
                  <div className="space-y-3">
                    <div className="flex items-center justify-between text-xs font-semibold text-slate-900">
                      <span className="flex items-center gap-1.5">
                        <Users className="h-4 w-4 text-slate-500" aria-hidden="true" />
                        {t('announcements.ackReportTitle')}
                      </span>
                      <span className="tabular-nums text-[#2563EB]">
                        {ackReport.acknowledged_count}/
                        {ackReport.total_target_count}
                      </span>
                    </div>

                    {ackReport.pending.length > 0 && (
                      <div className="space-y-1">
                        <div className="text-[11px] font-medium text-amber-800">
                          {t('announcements.pendingListTitle', {
                            count: ackReport.pending_count,
                          })}
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                          {ackReport.pending.map((emp) => (
                            <Badge
                              key={emp.employee_id}
                              variant="outline"
                              className="border-amber-200 bg-amber-50 text-xs text-amber-800"
                            >
                              {emp.employee_code} · {emp.full_name}
                            </Badge>
                          ))}
                        </div>
                      </div>
                    )}

                    {ackReport.acknowledged.length > 0 && (
                      <div className="space-y-1">
                        <div className="text-[11px] font-medium text-emerald-700">
                          {t('announcements.acknowledgedListTitle', {
                            count: ackReport.acknowledged_count,
                          })}
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                          {ackReport.acknowledged.map((emp) => (
                            <Badge
                              key={emp.employee_id}
                              variant="outline"
                              className="border-emerald-200 bg-emerald-50 text-xs text-emerald-700"
                            >
                              {emp.employee_code} · {emp.full_name}
                            </Badge>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>
          )}
        </SheetContent>
      </Sheet>
    </div>
  )
}

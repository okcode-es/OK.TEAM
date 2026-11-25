import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from 'react'
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from '@tanstack/react-table'
import { CheckCircle2, Download, FileCheck2, Upload } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  apiClient,
  extractApiError,
  getApiBaseUrl,
  getAuthHeaders,
} from '@/api/client'
import type {
  DocumentCategory,
  DocumentDetailResponse,
  DocumentListItemResponse,
  EmployeeResponse,
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useAuth } from '@/features/auth/AuthContext'
import { formatUtcDateTime, isImeComposing } from '@/lib/ime'

const ALL_CATEGORIES: DocumentCategory[] = [
  'contract',
  'payslip',
  'attendance_report',
  'medical_leave',
  'id_document',
  'other',
]

const MANAGER_UPLOAD_CATEGORIES: DocumentCategory[] = [
  'attendance_report',
  'medical_leave',
  'other',
]

const ALLOWED_MIME_TYPES = new Set([
  'application/pdf',
  'image/jpeg',
  'image/png',
  'image/webp',
])
const MAX_FILE_BYTES = 10 * 1024 * 1024

function formatFileSize(bytes: number, locale: string): string {
  if (bytes < 1024) {
    return `${new Intl.NumberFormat(locale).format(bytes)} B`
  }
  if (bytes < 1024 * 1024) {
    return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(bytes / 1024)} KB`
  }
  return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 2 }).format(bytes / (1024 * 1024))} MB`
}

export function DocumentsPage() {
  const { t, i18n } = useTranslation()
  const { activeTenant, profile } = useAuth()
  const locale = i18n.language || 'zh-CN'

  const role = activeTenant?.role
  const canUpload = role !== 'accountant'
  const isEmployeeRole = role === 'employee'
  const isManagerRole = role === 'store_manager'

  const uploadCategories = useMemo<DocumentCategory[]>(() => {
    if (isEmployeeRole) return ['medical_leave']
    if (isManagerRole) return MANAGER_UPLOAD_CATEGORIES
    return ALL_CATEGORIES
  }, [isEmployeeRole, isManagerRole])

  const [categoryFilter, setCategoryFilter] = useState<string>('all')
  const [signatureFilter, setSignatureFilter] = useState<string>('all')

  const [items, setItems] = useState<DocumentListItemResponse[]>([])
  const [employees, setEmployees] = useState<EmployeeResponse[]>([])
  const [stores, setStores] = useState<StoreResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [errorBanner, setErrorBanner] = useState<string | null>(null)

  // Upload Sheet state
  const [uploadOpen, setUploadOpen] = useState(false)
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [uploadTitle, setUploadTitle] = useState('')
  const [uploadCategory, setUploadCategory] =
    useState<DocumentCategory>('other')
  const [uploadEmployeeId, setUploadEmployeeId] = useState<string>('none')
  const [uploadStoreId, setUploadStoreId] = useState<string>('none')
  const [uploadPeriodMonth, setUploadPeriodMonth] = useState('')
  const [uploadRequiresSignature, setUploadRequiresSignature] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)

  // Detail Sheet state
  const [detailOpen, setDetailOpen] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detail, setDetail] = useState<DocumentDetailResponse | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [generatingUrl, setGeneratingUrl] = useState(false)
  const [signedUrlCountdown, setSignedUrlCountdown] = useState<number | null>(
    null,
  )
  const [signingReceipt, setSigningReceipt] = useState(false)
  const [signedNotice, setSignedNotice] = useState<string | null>(null)

  const loadMetadata = useCallback(async () => {
    const [empRes, storeRes] = await Promise.all([
      apiClient.GET('/api/v1/employees'),
      apiClient.GET('/api/v1/stores'),
    ])
    if (empRes.data) setEmployees(empRes.data.items)
    if (storeRes.data) setStores(storeRes.data.items)
  }, [])

  const loadDocuments = useCallback(async () => {
    setLoading(true)
    setErrorBanner(null)
    try {
      const res = await apiClient.GET('/api/v1/documents', {
        params: {
          query: {
            category:
              categoryFilter !== 'all'
                ? (categoryFilter as DocumentCategory)
                : undefined,
            requires_signature:
              signatureFilter === 'required'
                ? true
                : signatureFilter === 'not_required'
                  ? false
                  : undefined,
          },
        },
      })
      if (res.error || !res.data) {
        setErrorBanner(extractApiError(res.error, t('common.error')))
        return
      }
      setItems(res.data.items)
    } finally {
      setLoading(false)
    }
  }, [categoryFilter, signatureFilter, t])

  useEffect(() => {
    setCategoryFilter('all')
    setSignatureFilter('all')
    setDetailOpen(false)
    setDetail(null)
    void loadMetadata()
  }, [loadMetadata, activeTenant?.tenant_id])

  useEffect(() => {
    void loadDocuments()
  }, [loadDocuments, activeTenant?.tenant_id])

  useEffect(() => {
    if (signedUrlCountdown === null || signedUrlCountdown <= 0) return
    const timer = window.setInterval(() => {
      setSignedUrlCountdown((prev) =>
        prev !== null && prev > 1 ? prev - 1 : 0,
      )
    }, 1000)
    return () => window.clearInterval(timer)
  }, [signedUrlCountdown])

  const employeeMap = useMemo(() => {
    const map = new Map<string, EmployeeResponse>()
    for (const emp of employees) map.set(emp.id, emp)
    return map
  }, [employees])

  const storeMap = useMemo(() => {
    const map = new Map<string, StoreResponse>()
    for (const st of stores) map.set(st.id, st)
    return map
  }, [stores])

  const openDetail = useCallback(
    async (documentId: string) => {
      setDetailOpen(true)
      setDetailLoading(true)
      setDetailError(null)
      setSignedUrlCountdown(null)
      setSignedNotice(null)
      try {
        const res = await apiClient.GET('/api/v1/documents/{document_id}', {
          params: { path: { document_id: documentId } },
        })
        if (res.error || !res.data) {
          setDetailError(extractApiError(res.error, t('common.error')))
          setDetail(null)
          return
        }
        setDetail(res.data)
      } finally {
        setDetailLoading(false)
      }
    },
    [t],
  )

  const handleOpenUpload = () => {
    setUploadFile(null)
    setUploadTitle('')
    setUploadCategory(uploadCategories[0] ?? 'other')
    setUploadEmployeeId('none')
    setUploadStoreId('none')
    setUploadPeriodMonth('')
    setUploadRequiresSignature(false)
    setUploadError(null)
    setUploadOpen(true)
  }

  const handleUploadSubmit = async (e?: FormEvent) => {
    if (e) e.preventDefault()
    if (!canUpload) return

    if (!uploadFile) {
      setUploadError(t('documents.uploadDrawer.fileRequired'))
      return
    }
    if (uploadFile.size > MAX_FILE_BYTES) {
      setUploadError(t('documents.uploadDrawer.fileTooLarge'))
      return
    }
    if (uploadFile.type && !ALLOWED_MIME_TYPES.has(uploadFile.type)) {
      setUploadError(t('documents.uploadDrawer.fileInvalidType'))
      return
    }
    if (!uploadTitle.trim()) {
      setUploadError(t('documents.uploadDrawer.titleRequired'))
      return
    }

    setUploading(true)
    setUploadError(null)
    try {
      const formData = new FormData()
      formData.append('file', uploadFile)
      formData.append('category', uploadCategory)
      formData.append('title', uploadTitle.trim())
      if (!isEmployeeRole && uploadEmployeeId !== 'none') {
        formData.append('employee_id', uploadEmployeeId)
      }
      if (!isEmployeeRole && uploadStoreId !== 'none') {
        formData.append('store_id', uploadStoreId)
      }
      if (uploadPeriodMonth.trim()) {
        formData.append('period_month', uploadPeriodMonth.trim())
      }
      formData.append(
        'requires_signature',
        uploadRequiresSignature ? 'true' : 'false',
      )

      const headers = await getAuthHeaders()
      const response = await fetch(`${getApiBaseUrl()}/api/v1/documents`, {
        method: 'POST',
        headers,
        body: formData,
      })

      if (!response.ok) {
        let message = t('common.error')
        try {
          const payload = (await response.json()) as { detail?: unknown }
          if (typeof payload.detail === 'string' && payload.detail.trim()) {
            message = payload.detail
          }
        } catch {
          // ignore non-json error body
        }
        setUploadError(message)
        return
      }

      setUploadOpen(false)
      await loadDocuments()
    } finally {
      setUploading(false)
    }
  }

  const handleGetSignedUrl = async () => {
    if (!detail) return
    setGeneratingUrl(true)
    setDetailError(null)
    try {
      const res = await apiClient.POST(
        '/api/v1/documents/{document_id}/signed-url',
        {
          params: { path: { document_id: detail.document.id } },
          body: { ttl_seconds: 60 },
        },
      )
      if (res.error || !res.data) {
        setDetailError(extractApiError(res.error, t('common.error')))
        return
      }
      setSignedUrlCountdown(res.data.ttl_seconds)
      window.open(res.data.url, '_blank', 'noopener,noreferrer')
    } finally {
      setGeneratingUrl(false)
    }
  }

  const canSignSelected = useMemo(() => {
    if (!detail || !detail.document.requires_signature) return false
    if (!detail.document.employee_id || !profile?.user_id) return false
    const ownerEmp = employeeMap.get(detail.document.employee_id)
    if (!ownerEmp || ownerEmp.user_id !== profile.user_id) return false
    const alreadySigned = detail.receipts.some(
      (r) => r.employee_id === ownerEmp.id,
    )
    return !alreadySigned
  }, [detail, employeeMap, profile?.user_id])

  const handleSignReceipt = async () => {
    if (!detail || !canSignSelected) return
    setSigningReceipt(true)
    setDetailError(null)
    setSignedNotice(null)
    try {
      const res = await apiClient.POST(
        '/api/v1/documents/{document_id}/receipts',
        {
          params: { path: { document_id: detail.document.id } },
        },
      )
      if (res.error || !res.data) {
        setDetailError(extractApiError(res.error, t('common.error')))
        return
      }
      setSignedNotice(t('documents.detailDrawer.signedSuccess'))
      await openDetail(detail.document.id)
      await loadDocuments()
    } finally {
      setSigningReceipt(false)
    }
  }

  const handleUploadKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !isImeComposing(e)) {
      e.preventDefault()
      void handleUploadSubmit()
    }
  }

  const columns = useMemo<ColumnDef<DocumentListItemResponse>[]>(
    () => [
      {
        id: 'title',
        header: t('documents.columns.title'),
        cell: ({ row }) => (
          <div>
            <div className="font-medium text-slate-900">
              {row.original.document.title}
            </div>
            <div className="text-xs text-slate-500">
              {row.original.document.original_filename}
            </div>
          </div>
        ),
      },
      {
        id: 'category',
        header: t('documents.columns.category'),
        cell: ({ row }) => (
          <Badge
            variant="outline"
            className="border-slate-200 bg-slate-50 text-slate-700"
          >
            {t(`documents.categories.${row.original.document.category}`)}
          </Badge>
        ),
      },
      {
        id: 'owner',
        header: t('documents.columns.owner'),
        cell: ({ row }) => {
          const doc = row.original.document
          const emp = doc.employee_id ? employeeMap.get(doc.employee_id) : null
          const store = doc.store_id ? storeMap.get(doc.store_id) : null
          return (
            <div className="text-xs text-slate-700">
              <div>{emp ? emp.full_name : t('documents.tenantWide')}</div>
              {store && <div className="text-slate-500">{store.name}</div>}
            </div>
          )
        },
      },
      {
        id: 'sha256',
        header: t('documents.columns.sha256'),
        cell: ({ row }) => (
          <span className="font-mono text-xs text-slate-600 tabular-nums">
            {row.original.document.sha256_hex.slice(0, 12)}…
          </span>
        ),
      },
      {
        id: 'size',
        header: t('documents.columns.size'),
        cell: ({ row }) => (
          <span className="text-xs text-slate-600 tabular-nums">
            {formatFileSize(row.original.document.size_bytes, locale)}
          </span>
        ),
      },
      {
        id: 'signature',
        header: t('documents.columns.signature'),
        cell: ({ row }) => {
          const doc = row.original.document
          if (!doc.requires_signature) {
            return (
              <Badge
                variant="outline"
                className="border-slate-200 bg-slate-100 text-slate-600"
              >
                {t('documents.signatureStatuses.notRequired')}
              </Badge>
            )
          }
          const isSigned =
            row.original.signed_by_self || row.original.receipt_count > 0
          return isSigned ? (
            <Badge
              variant="outline"
              className="border-emerald-200 bg-emerald-50 text-emerald-700"
            >
              {t('documents.signatureStatuses.signed')}
            </Badge>
          ) : (
            <Badge
              variant="outline"
              className="border-amber-200 bg-amber-50 text-amber-800"
            >
              {t('documents.signatureStatuses.pending')}
            </Badge>
          )
        },
      },
      {
        id: 'createdAt',
        header: t('documents.columns.createdAt'),
        cell: ({ row }) => (
          <span className="text-xs text-slate-600 tabular-nums">
            {formatUtcDateTime(row.original.document.created_at, locale)}
          </span>
        ),
      },
    ],
    [employeeMap, storeMap, locale, t],
  )

  const table = useReactTable({
    data: items,
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="space-y-1">
        <h1 className="text-lg font-semibold tracking-tight text-slate-900">
          {t('documents.title')}
        </h1>
        <p className="text-sm text-slate-600">{t('documents.subtitle')}</p>
      </div>

      {/* Compact Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-slate-200 bg-white p-3">
        <div className="flex flex-wrap items-center gap-2">
          <Select value={categoryFilter} onValueChange={setCategoryFilter}>
            <SelectTrigger className="min-w-44 bg-white">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">
                {t('documents.allCategories')}
              </SelectItem>
              {ALL_CATEGORIES.map((cat) => (
                <SelectItem key={cat} value={cat}>
                  {t(`documents.categories.${cat}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select value={signatureFilter} onValueChange={setSignatureFilter}>
            <SelectTrigger className="min-w-44 bg-white">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">
                {t('documents.signatureFilterAll')}
              </SelectItem>
              <SelectItem value="required">
                {t('documents.signatureFilterRequired')}
              </SelectItem>
              <SelectItem value="not_required">
                {t('documents.signatureFilterNotRequired')}
              </SelectItem>
            </SelectContent>
          </Select>

          <span className="px-1 text-xs text-slate-500 tabular-nums">
            {t('documents.count', { count: items.length })}
          </span>
        </div>

        {canUpload && (
          <Button type="button" size="sm" onClick={handleOpenUpload}>
            <Upload className="h-4 w-4" aria-hidden="true" />
            {t('documents.uploadButton')}
          </Button>
        )}
      </div>

      {errorBanner && (
        <div
          role="alert"
          className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700"
        >
          {errorBanner}
        </div>
      )}

      {/* High-Density TanStack Table */}
      <div className="overflow-x-auto rounded-md border border-slate-200 bg-white">
        {loading ? (
          <div className="space-y-2 p-4">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : items.length === 0 ? (
          <div className="p-8 text-center text-sm text-slate-500">
            {t('documents.empty')}
          </div>
        ) : (
          <Table>
            <TableHeader>
              {table.getHeaderGroups().map((hg) => (
                <TableRow key={hg.id}>
                  {hg.headers.map((header) => (
                    <TableHead key={header.id}>
                      {flexRender(
                        header.column.columnDef.header,
                        header.getContext(),
                      )}
                    </TableHead>
                  ))}
                </TableRow>
              ))}
            </TableHeader>
            <TableBody>
              {table.getRowModel().rows.map((row) => (
                <TableRow
                  key={row.id}
                  className="cursor-pointer hover:bg-slate-50"
                  onClick={() => void openDetail(row.original.document.id)}
                >
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id}>
                      {flexRender(
                        cell.column.columnDef.cell,
                        cell.getContext(),
                      )}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>

      {/* Upload Document Sheet */}
      <Sheet open={uploadOpen} onOpenChange={setUploadOpen}>
        <SheetContent side="right" className="w-full overflow-y-auto bg-white sm:max-w-md">
          <SheetHeader>
            <SheetTitle>{t('documents.uploadDrawer.title')}</SheetTitle>
            <SheetDescription>
              {t('documents.uploadDrawer.description')}
            </SheetDescription>
          </SheetHeader>

          <form
            onSubmit={(e) => void handleUploadSubmit(e)}
            className="mt-4 space-y-4 px-4"
          >
            {uploadError && (
              <div
                role="alert"
                className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
              >
                {uploadError}
              </div>
            )}

            <div className="space-y-1.5">
              <Label htmlFor="doc-file">{t('documents.uploadDrawer.file')}</Label>
              <Input
                id="doc-file"
                type="file"
                accept=".pdf,.jpg,.jpeg,.png,.webp,application/pdf,image/jpeg,image/png,image/webp"
                onChange={(e) => {
                  const f = e.target.files?.[0] ?? null
                  setUploadFile(f)
                  if (f && !uploadTitle.trim()) {
                    setUploadTitle(f.name.replace(/\.[^.]+$/, ''))
                  }
                }}
                required
              />
              <p className="text-xs text-slate-500">
                {t('documents.uploadDrawer.fileHint')}
              </p>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="doc-title">
                {t('documents.uploadDrawer.docTitle')}
              </Label>
              <Input
                id="doc-title"
                value={uploadTitle}
                placeholder={t('documents.uploadDrawer.docTitlePlaceholder')}
                onChange={(e) => setUploadTitle(e.target.value)}
                onKeyDown={handleUploadKeyDown}
                required
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="doc-category">
                {t('documents.uploadDrawer.category')}
              </Label>
              <Select
                value={uploadCategory}
                onValueChange={(v) => setUploadCategory(v as DocumentCategory)}
              >
                <SelectTrigger id="doc-category" className="w-full bg-white">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {uploadCategories.map((cat) => (
                    <SelectItem key={cat} value={cat}>
                      {t(`documents.categories.${cat}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {!isEmployeeRole && (
              <>
                <div className="space-y-1.5">
                  <Label htmlFor="doc-employee">
                    {t('documents.uploadDrawer.employee')}
                  </Label>
                  <Select
                    value={uploadEmployeeId}
                    onValueChange={setUploadEmployeeId}
                  >
                    <SelectTrigger id="doc-employee" className="w-full bg-white">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">
                        {t('documents.uploadDrawer.noEmployee')}
                      </SelectItem>
                      {employees.map((emp) => (
                        <SelectItem key={emp.id} value={emp.id}>
                          {emp.employee_code} · {emp.full_name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="doc-store">
                    {t('documents.uploadDrawer.store')}
                  </Label>
                  <Select
                    value={uploadStoreId}
                    onValueChange={setUploadStoreId}
                  >
                    <SelectTrigger id="doc-store" className="w-full bg-white">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">
                        {t('documents.uploadDrawer.noStore')}
                      </SelectItem>
                      {stores.map((st) => (
                        <SelectItem key={st.id} value={st.id}>
                          {st.code} · {st.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </>
            )}

            <div className="space-y-1.5">
              <Label htmlFor="doc-period">
                {t('documents.uploadDrawer.periodMonth')}
              </Label>
              <Input
                id="doc-period"
                placeholder="2026-11"
                value={uploadPeriodMonth}
                onChange={(e) => setUploadPeriodMonth(e.target.value)}
                onKeyDown={handleUploadKeyDown}
              />
            </div>

            {!isEmployeeRole && (
              <div className="flex items-center gap-2">
                <Checkbox
                  id="doc-requires-sig"
                  checked={uploadRequiresSignature}
                  onCheckedChange={(checked) =>
                    setUploadRequiresSignature(checked === true)
                  }
                />
                <Label
                  htmlFor="doc-requires-sig"
                  className="cursor-pointer text-xs font-normal text-slate-700"
                >
                  {t('documents.uploadDrawer.requiresSignature')}
                </Label>
              </div>
            )}

            <Separator />

            <SheetFooter className="flex gap-2 px-0">
              <Button
                type="button"
                variant="outline"
                onClick={() => setUploadOpen(false)}
              >
                {t('documents.uploadDrawer.cancel')}
              </Button>
              <Button type="submit" disabled={uploading}>
                {uploading
                  ? t('documents.uploadDrawer.submitting')
                  : t('documents.uploadDrawer.submit')}
              </Button>
            </SheetFooter>
          </form>
        </SheetContent>
      </Sheet>

      {/* Detail & SHA-256 Receipt Sheet */}
      <Sheet open={detailOpen} onOpenChange={setDetailOpen}>
        <SheetContent side="right" className="w-full overflow-y-auto bg-white sm:max-w-lg">
          <SheetHeader>
            <SheetTitle>{t('documents.detailDrawer.title')}</SheetTitle>
            <SheetDescription>
              {t('documents.detailDrawer.description')}
            </SheetDescription>
          </SheetHeader>

          <div className="mt-4 space-y-4 px-4">
            {detailError && (
              <div
                role="alert"
                className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
              >
                {detailError}
              </div>
            )}
            {signedNotice && (
              <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700">
                {signedNotice}
              </div>
            )}

            {detailLoading ? (
              <div className="space-y-2">
                <Skeleton className="h-20 w-full" />
                <Skeleton className="h-16 w-full" />
              </div>
            ) : (
              detail && (
                <>
                  <div className="space-y-1">
                    <h2 className="text-sm font-semibold text-slate-900">
                      {detail.document.title}
                    </h2>
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge
                        variant="outline"
                        className="border-slate-200 bg-slate-50 text-slate-700"
                      >
                        {t(`documents.categories.${detail.document.category}`)}
                      </Badge>
                      {detail.document.period_month && (
                        <Badge
                          variant="outline"
                          className="border-slate-200 bg-white text-slate-600 tabular-nums"
                        >
                          {detail.document.period_month}
                        </Badge>
                      )}
                    </div>
                  </div>

                  <dl className="space-y-2 rounded-md border border-slate-200 bg-slate-50 p-3 text-xs">
                    <div className="flex justify-between gap-2">
                      <dt className="text-slate-500">
                        {t('documents.detailDrawer.filename')}
                      </dt>
                      <dd className="font-medium text-slate-900">
                        {detail.document.original_filename}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt className="text-slate-500">
                        {t('documents.detailDrawer.mimeSize')}
                      </dt>
                      <dd className="text-slate-700 tabular-nums">
                        {detail.document.mime_type} ·{' '}
                        {formatFileSize(detail.document.size_bytes, locale)}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt className="text-slate-500">
                        {t('documents.detailDrawer.retentionUntil')}
                      </dt>
                      <dd className="text-slate-700 tabular-nums">
                        {formatUtcDateTime(
                          detail.document.retention_until,
                          locale,
                        )}
                      </dd>
                    </div>
                    <div className="space-y-1 pt-1">
                      <dt className="text-slate-500">
                        {t('documents.detailDrawer.sha256Full')}
                      </dt>
                      <dd className="rounded border border-slate-200 bg-white p-2 font-mono text-[11px] break-all text-slate-800 tabular-nums">
                        {detail.document.sha256_hex}
                      </dd>
                    </div>
                  </dl>

                  {/* On-demand 60s Signed URL */}
                  <div className="space-y-2">
                    <Button
                      type="button"
                      className="w-full"
                      disabled={generatingUrl}
                      onClick={() => void handleGetSignedUrl()}
                    >
                      <Download className="h-4 w-4" aria-hidden="true" />
                      {generatingUrl
                        ? t('documents.detailDrawer.generatingUrl')
                        : t('documents.detailDrawer.downloadButton')}
                    </Button>
                    {signedUrlCountdown !== null && signedUrlCountdown > 0 && (
                      <p className="text-xs text-teal-700 tabular-nums">
                        {t('documents.detailDrawer.signedUrlActive', {
                          seconds: signedUrlCountdown,
                        })}
                      </p>
                    )}
                    {signedUrlCountdown === 0 && (
                      <p className="text-xs text-slate-500">
                        {t('documents.detailDrawer.signedUrlExpired')}
                      </p>
                    )}
                  </div>

                  <Separator />

                  {/* Electronic Receipt Section */}
                  <div className="space-y-3">
                    <div className="flex items-center gap-2 text-xs font-semibold tracking-wide text-slate-700 uppercase">
                      <FileCheck2 className="h-4 w-4 text-teal-700" aria-hidden="true" />
                      <span>{t('documents.detailDrawer.receiptSectionTitle')}</span>
                    </div>

                    {canSignSelected && (
                      <div className="space-y-3 rounded-md border border-teal-200 bg-teal-50/40 p-3">
                        <p className="text-xs leading-relaxed text-slate-700">
                          {t('documents.detailDrawer.legalStatement')}
                        </p>
                        <Button
                          type="button"
                          size="sm"
                          disabled={signingReceipt}
                          onClick={() => void handleSignReceipt()}
                        >
                          <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                          {signingReceipt
                            ? t('documents.detailDrawer.signing')
                            : t('documents.detailDrawer.signButton')}
                        </Button>
                      </div>
                    )}

                    {detail.receipts.length === 0 ? (
                      <p className="text-xs text-slate-500">
                        {t('documents.detailDrawer.noReceipts')}
                      </p>
                    ) : (
                      <div className="space-y-2">
                        {detail.receipts.map((receipt) => (
                          <div
                            key={receipt.id}
                            className="space-y-1.5 rounded-md border border-slate-200 bg-white p-3 text-xs"
                          >
                            <div className="flex justify-between gap-2">
                              <span className="text-slate-500">
                                {t('documents.detailDrawer.receiptSignedAt')}
                              </span>
                              <span className="font-medium text-slate-900 tabular-nums">
                                {formatUtcDateTime(
                                  receipt.signed_at_utc,
                                  locale,
                                  'UTC',
                                )}{' '}
                                UTC
                              </span>
                            </div>
                            <div className="flex justify-between gap-2">
                              <span className="text-slate-500">
                                {t('documents.detailDrawer.receiptIp')}
                              </span>
                              <span className="font-mono text-slate-700 tabular-nums">
                                {receipt.ip_address}
                              </span>
                            </div>
                            <div className="space-y-0.5">
                              <span className="text-slate-500">
                                {t('documents.detailDrawer.receiptUserAgent')}
                              </span>
                              <p className="truncate font-mono text-[11px] text-slate-600">
                                {receipt.user_agent}
                              </p>
                            </div>
                            <div className="space-y-0.5">
                              <span className="text-slate-500">
                                {t('documents.detailDrawer.receiptHash')}
                              </span>
                              <p className="font-mono text-[11px] break-all text-slate-700 tabular-nums">
                                {receipt.document_sha256_hex}
                              </p>
                            </div>
                            <div className="space-y-0.5 pt-1">
                              <span className="text-slate-500">
                                {t('documents.detailDrawer.receiptStatement')}
                              </span>
                              <p className="text-slate-700">
                                {receipt.receipt_statement}
                              </p>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  <SheetFooter className="px-0">
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => setDetailOpen(false)}
                    >
                      {t('documents.detailDrawer.close')}
                    </Button>
                  </SheetFooter>
                </>
              )
            )}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  )
}

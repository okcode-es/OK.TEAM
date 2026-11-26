import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  CheckCircle2,
  Clock,
  Download,
  FileText,
  Plus,
  ShieldCheck,
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
  DocumentCategory,
  DocumentListItemResponse,
  EmployeeResponse,
} from '@/api/types'
import { EmptyState } from '@/components/common/EmptyState'
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
import { cn } from '@/lib/utils'

interface ProfileDocumentsTabProps {
  employee: EmployeeResponse
}

const CATEGORY_BADGE: Record<DocumentCategory, string> = {
  payslip: 'border-sky-200 bg-sky-50 text-sky-700',
  contract: 'border-blue-200 bg-blue-50 text-[#2563EB]',
  medical_leave: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  attendance_report: 'border-amber-200 bg-amber-50 text-amber-800',
  id_document: 'border-slate-200 bg-slate-100 text-slate-700',
  other: 'border-slate-200 bg-slate-100 text-slate-600',
}

export function ProfileDocumentsTab({ employee }: ProfileDocumentsTabProps) {
  const { t, i18n } = useTranslation()
  const { activeTenant } = useAuth()
  const locale = i18n.language || 'zh-CN'
  const isEs = locale.startsWith('es')
  const canUpload =
    activeTenant?.role === 'owner' ||
    activeTenant?.role === 'hr' ||
    activeTenant?.role === 'store_manager'

  const [category, setCategory] = useState<'all' | DocumentCategory>('all')
  const [items, setItems] = useState<DocumentListItemResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  const [uploadOpen, setUploadOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [uploadCategory, setUploadCategory] =
    useState<DocumentCategory>('contract')
  const [requiresSignature, setRequiresSignature] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)

  const loadDocs = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await apiClient.GET('/api/v1/documents', {
        params: {
          query: {
            employee_id: employee.id,
            category: category === 'all' ? undefined : category,
          },
        },
      })
      if (res.error || !res.data) {
        setError(extractApiError(res.error, t('common.error')))
        return
      }
      setItems(res.data.items)
    } finally {
      setLoading(false)
    }
  }, [employee.id, category, t])

  useEffect(() => {
    void loadDocs()
  }, [loadDocs])

  const handleDownload = async (docId: string) => {
    setBusyId(docId)
    try {
      const res = await apiClient.POST(
        '/api/v1/documents/{document_id}/signed-url',
        {
          params: { path: { document_id: docId } },
          body: { ttl_seconds: 60 },
        },
      )
      if (res.data) {
        window.open(res.data.url, '_blank', 'noopener,noreferrer')
      }
    } finally {
      setBusyId(null)
    }
  }

  const handleSign = async (docId: string) => {
    setBusyId(docId)
    try {
      const res = await apiClient.POST(
        '/api/v1/documents/{document_id}/receipts',
        {
          params: { path: { document_id: docId } },
        },
      )
      if (res.error) {
        setError(extractApiError(res.error, t('common.error')))
        return
      }
      await loadDocs()
    } finally {
      setBusyId(null)
    }
  }

  const handleUpload = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!file || !activeTenant) return
    setUploading(true)
    setUploadError(null)
    try {
      const formData = new FormData()
      formData.append('title', title.trim() || file.name)
      formData.append('category', uploadCategory)
      formData.append('employee_id', employee.id)
      formData.append('requires_signature', String(requiresSignature))
      formData.append('file', file)

      const headers = await getAuthHeaders()
      const resp = await fetch(`${getApiBaseUrl()}/api/v1/documents`, {
        method: 'POST',
        headers,
        body: formData,
      })
      if (!resp.ok) {
        setUploadError(await extractFetchError(resp, t('common.error')))
        return
      }
      setUploadOpen(false)
      setTitle('')
      setFile(null)
      await loadDocs()
    } finally {
      setUploading(false)
    }
  }

  const categories: Array<{ id: 'all' | DocumentCategory; label: string }> = [
    { id: 'all', label: isEs ? 'Todos' : '全部' },
    { id: 'payslip', label: t('documents.categories.payslip') },
    { id: 'contract', label: t('documents.categories.contract') },
    { id: 'medical_leave', label: t('documents.categories.medical_leave') },
    {
      id: 'attendance_report',
      label: t('documents.categories.attendance_report'),
    },
    { id: 'id_document', label: t('documents.categories.id_document') },
  ]

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-1.5">
          {categories.map((cat) => (
            <button
              key={cat.id}
              type="button"
              onClick={() => setCategory(cat.id)}
              className={cn(
                'rounded-lg px-3 py-1.5 text-xs font-medium transition-colors',
                category === cat.id
                  ? 'bg-[#2563EB] text-white'
                  : 'border border-slate-200 bg-white text-slate-600 hover:bg-slate-50',
              )}
            >
              {cat.label}
            </button>
          ))}
        </div>

        {canUpload && (
          <Button
            type="button"
            size="sm"
            onClick={() => {
              setUploadError(null)
              setUploadOpen(true)
            }}
            className="bg-[#2563EB] text-white hover:bg-blue-700"
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
            {isEs ? 'Subir documento' : '上传文档'}
          </Button>
        )}
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-2.5 text-xs text-rose-700"
        >
          {error}
        </div>
      )}

      {loading ? (
        <Skeleton className="h-48 w-full rounded-xl" />
      ) : items.length === 0 ? (
        <EmptyState
          icon={FileText}
          title={isEs ? 'No hay documentos' : '暂无员工文档'}
          description={
            isEs
              ? 'Los contratos, nóminas, justificantes y documentos aparecerán aquí.'
              : '该员工的劳动合同、工资单、病假条与合规文档将显示在此处。'
          }
          actionLabel={
            canUpload ? (isEs ? 'Subir documento' : '上传文档') : undefined
          }
          onAction={canUpload ? () => setUploadOpen(true) : undefined}
        />
      ) : (
        <div className="overflow-hidden rounded-xl border border-slate-200/75 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader className="bg-slate-50/80">
                <TableRow className="border-slate-200">
                  <TableHead className="px-4 text-xs font-semibold text-slate-600">
                    {isEs ? 'Documento' : '文件名'}
                  </TableHead>
                  <TableHead className="px-4 text-xs font-semibold text-slate-600">
                    {isEs ? 'Fecha' : '日期'}
                  </TableHead>
                  <TableHead className="px-4 text-xs font-semibold text-slate-600">
                    {isEs ? 'Categoría' : '分类'}
                  </TableHead>
                  <TableHead className="px-4 text-xs font-semibold text-slate-600">
                    {isEs ? 'Firma electrónica' : '电子签收状态'}
                  </TableHead>
                  <TableHead className="px-4 text-right text-xs font-semibold text-slate-600">
                    {isEs ? 'Acciones' : '操作'}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((item) => (
                  <TableRow key={item.document.id} className="border-slate-200">
                    <TableCell className="px-4 py-3">
                      <div className="font-medium text-slate-900">
                        {item.document.title}
                      </div>
                      <div className="text-xs text-slate-500">
                        {item.document.original_filename}
                      </div>
                    </TableCell>
                    <TableCell className="px-4 py-3 text-xs text-slate-600 tabular-nums">
                      {formatUtcDateTime(item.document.created_at, locale)}
                    </TableCell>
                    <TableCell className="px-4 py-3">
                      <Badge
                        variant="outline"
                        className={cn(
                          'text-[11px]',
                          CATEGORY_BADGE[item.document.category],
                        )}
                      >
                        {t(`documents.categories.${item.document.category}`)}
                      </Badge>
                    </TableCell>
                    <TableCell className="px-4 py-3">
                      {!item.document.requires_signature ? (
                        <span className="text-xs text-slate-400">—</span>
                      ) : item.signed_by_self ? (
                        <Badge
                          variant="outline"
                          className="gap-1 border-emerald-200 bg-emerald-50 text-[11px] text-emerald-700"
                        >
                          <CheckCircle2
                            className="h-3 w-3"
                            aria-hidden="true"
                          />
                          {t('documents.signatureStatuses.signed')}
                        </Badge>
                      ) : (
                        <Badge
                          variant="outline"
                          className="gap-1 border-amber-200 bg-amber-50 text-[11px] text-amber-800"
                        >
                          <Clock className="h-3 w-3" aria-hidden="true" />
                          {t('documents.signatureStatuses.pending')}
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="px-4 py-3 text-right">
                      <div className="inline-flex items-center gap-1.5">
                        {item.document.requires_signature &&
                          !item.signed_by_self && (
                            <Button
                              type="button"
                              size="xs"
                              disabled={busyId === item.document.id}
                              onClick={() => void handleSign(item.document.id)}
                              className="bg-[#2563EB] text-white hover:bg-blue-700"
                            >
                              <ShieldCheck
                                className="h-3 w-3"
                                aria-hidden="true"
                              />
                              {t('dashboard.myDocuments.sign')}
                            </Button>
                          )}
                        <Button
                          type="button"
                          size="xs"
                          variant="outline"
                          disabled={busyId === item.document.id}
                          onClick={() => void handleDownload(item.document.id)}
                        >
                          <Download className="h-3 w-3" aria-hidden="true" />
                          {isEs ? 'Descargar' : '下载'}
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </div>
      )}

      {/* Upload Sheet */}
      <Sheet open={uploadOpen} onOpenChange={setUploadOpen}>
        <SheetContent
          side="right"
          className="flex w-full flex-col bg-white sm:max-w-md"
        >
          <SheetHeader className="border-b border-slate-200 px-6 py-4">
            <SheetTitle>{isEs ? 'Subir documento' : '上传员工文档'}</SheetTitle>
            <SheetDescription>
              {employee.full_name} ({employee.employee_code})
            </SheetDescription>
          </SheetHeader>
          <form
            id="profile-doc-upload"
            onSubmit={handleUpload}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && isImeComposing(e)) e.preventDefault()
            }}
            className="flex-1 space-y-4 overflow-y-auto px-6 py-4"
          >
            {uploadError && (
              <div
                role="alert"
                className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
              >
                {uploadError}
              </div>
            )}

            <div className="space-y-1.5">
              <Label htmlFor="pdoc-title">{isEs ? 'Título' : '文档标题'}</Label>
              <Input
                id="pdoc-title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder={isEs ? 'Contrato indefinido 2026' : '2026 劳动合同'}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="pdoc-cat">{isEs ? 'Categoría' : '分类'}</Label>
              <Select
                value={uploadCategory}
                onValueChange={(v) => setUploadCategory(v as DocumentCategory)}
              >
                <SelectTrigger id="pdoc-cat" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(
                    [
                      'contract',
                      'payslip',
                      'medical_leave',
                      'attendance_report',
                      'id_document',
                      'other',
                    ] as DocumentCategory[]
                  ).map((cat) => (
                    <SelectItem key={cat} value={cat}>
                      {t(`documents.categories.${cat}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="pdoc-file">{isEs ? 'Archivo' : '选择文件'}</Label>
              <Input
                id="pdoc-file"
                type="file"
                required
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </div>

            <label className="flex cursor-pointer items-center gap-2 text-xs text-slate-700">
              <Checkbox
                checked={requiresSignature}
                onCheckedChange={(v) => setRequiresSignature(Boolean(v))}
              />
              <span>
                {isEs ? 'Requiere firma electrónica' : '需要员工电子签收'}
              </span>
            </label>
          </form>
          <SheetFooter className="flex-row justify-end gap-2 border-t border-slate-200 px-6 py-4">
            <Button
              type="button"
              variant="outline"
              onClick={() => setUploadOpen(false)}
            >
              {t('common.cancel')}
            </Button>
            <Button
              type="submit"
              form="profile-doc-upload"
              disabled={uploading || !file}
              className="bg-[#2563EB] text-white hover:bg-blue-700"
            >
              {uploading
                ? isEs
                  ? 'Subiendo...'
                  : '上传中...'
                : isEs
                  ? 'Subir'
                  : '确认上传'}
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>
    </div>
  )
}

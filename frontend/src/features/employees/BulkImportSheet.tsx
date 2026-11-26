import { useState } from 'react'
import { CheckCircle2, Download, Upload } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  extractApiError,
  extractFetchError,
  getApiBaseUrl,
  getAuthHeaders,
} from '@/api/client'
import type { BulkImportResponse } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

interface BulkImportSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onImported: () => Promise<void> | void
}

export function BulkImportSheet({
  open,
  onOpenChange,
  onImported,
}: BulkImportSheetProps) {
  const { t } = useTranslation()
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [result, setResult] = useState<BulkImportResponse | null>(null)

  const handleDownloadTemplate = async (format: 'csv' | 'xlsx') => {
    setErrorMsg(null)
    try {
      const headers = await getAuthHeaders()
      const res = await fetch(
        `${getApiBaseUrl()}/api/v1/employees/import/template?format=${format}`,
        { headers },
      )
      if (!res.ok) {
        setErrorMsg(await extractFetchError(res, t('errors.GENERIC')))
        return
      }
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `employees-import-template.${format}`
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setErrorMsg(extractApiError(err, t('errors.GENERIC')))
    }
  }

  const runImport = async (dryRun: boolean) => {
    if (!file) return
    setBusy(true)
    setErrorMsg(null)
    try {
      const formData = new FormData()
      formData.append('file', file)
      const headers = await getAuthHeaders()
      const res = await fetch(
        `${getApiBaseUrl()}/api/v1/employees/import?dry_run=${dryRun ? 'true' : 'false'}`,
        {
          method: 'POST',
          headers,
          body: formData,
        },
      )
      if (!res.ok) {
        setErrorMsg(await extractFetchError(res, t('errors.GENERIC')))
        return
      }
      const data = (await res.json()) as BulkImportResponse
      setResult(data)
      if (!dryRun && data.committed) {
        await onImported()
      }
    } catch (err) {
      setErrorMsg(extractApiError(err, t('errors.GENERIC')))
    } finally {
      setBusy(false)
    }
  }

  const formatRowField = (field: string): string => {
    const key = `employees.bulkImport.fields.${field}`
    return t(key, { defaultValue: field })
  }

  const formatRowError = (code: string): string => {
    const key = `employees.bulkImport.errorCodes.${code}`
    return t(key, { defaultValue: t('employees.bulkImport.errorCodes.GENERIC') })
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full overflow-y-auto bg-white sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>{t('employees.bulkImport.title')}</SheetTitle>
          <SheetDescription>
            {t('employees.bulkImport.description')}
          </SheetDescription>
        </SheetHeader>

        <div className="mt-4 space-y-4 px-4">
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void handleDownloadTemplate('csv')}
            >
              <Download className="h-4 w-4" aria-hidden="true" />
              {t('employees.bulkImport.downloadCsv')}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void handleDownloadTemplate('xlsx')}
            >
              <Download className="h-4 w-4" aria-hidden="true" />
              {t('employees.bulkImport.downloadXlsx')}
            </Button>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="bulk-import-file">
              {t('employees.bulkImport.selectFile')}
            </Label>
            <Input
              id="bulk-import-file"
              type="file"
              accept=".csv,.xlsx"
              onChange={(e) => {
                setFile(e.target.files?.[0] ?? null)
                setResult(null)
                setErrorMsg(null)
              }}
            />
          </div>

          {errorMsg && (
            <div
              role="alert"
              className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
            >
              {errorMsg}
            </div>
          )}

          {result && (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2 rounded-md border border-slate-200 bg-slate-50 p-3 text-xs tabular-nums">
                <Badge variant="outline" className="bg-white">
                  {t('employees.bulkImport.totalRows', {
                    count: result.total_rows,
                  })}
                </Badge>
                <Badge
                  variant="outline"
                  className="border-emerald-200 bg-emerald-50 text-emerald-700"
                >
                  {t('employees.bulkImport.validRows', {
                    count: result.valid_rows,
                  })}
                </Badge>
                <Badge
                  variant="outline"
                  className={
                    result.error_rows > 0
                      ? 'border-rose-200 bg-rose-50 text-rose-700'
                      : 'bg-white'
                  }
                >
                  {t('employees.bulkImport.errorRows', {
                    count: result.error_rows,
                  })}
                </Badge>
                {result.committed && (
                  <span className="inline-flex items-center gap-1 font-medium text-emerald-700">
                    <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                    {t('employees.bulkImport.committed', {
                      count: result.created_count,
                    })}
                  </span>
                )}
              </div>

              {result.errors.length > 0 && (
                <div className="max-h-64 overflow-y-auto rounded-md border border-slate-200">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="text-xs">
                          {t('employees.bulkImport.colRow')}
                        </TableHead>
                        <TableHead className="text-xs">
                          {t('employees.bulkImport.colField')}
                        </TableHead>
                        <TableHead className="text-xs">
                          {t('employees.bulkImport.colMessage')}
                        </TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {result.errors.map((err, idx) => (
                        <TableRow key={`${err.row_number}-${err.field}-${idx}`}>
                          <TableCell className="text-xs tabular-nums">
                            {err.row_number}
                          </TableCell>
                          <TableCell className="text-xs font-medium text-slate-700">
                            {formatRowField(err.field)}
                          </TableCell>
                          <TableCell className="text-xs text-rose-700">
                            {formatRowError(err.code)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </div>
          )}

          <SheetFooter className="flex flex-wrap gap-2 px-0">
            <Button
              type="button"
              variant="outline"
              disabled={!file || busy}
              onClick={() => void runImport(true)}
            >
              {t('employees.bulkImport.dryRun')}
            </Button>
            <Button
              type="button"
              disabled={
                !file ||
                busy ||
                (result !== null && result.error_rows > 0) ||
                (result !== null && result.committed)
              }
              onClick={() => void runImport(false)}
              className="bg-slate-900 text-white hover:bg-slate-800"
            >
              <Upload className="h-4 w-4" aria-hidden="true" />
              {t('employees.bulkImport.commit')}
            </Button>
          </SheetFooter>
        </div>
      </SheetContent>
    </Sheet>
  )
}

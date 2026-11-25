import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from '@tanstack/react-table'
import { Plus, Search, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { apiClient, extractApiError } from '@/api/client'
import type { StoreResponse } from '@/api/types'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
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
import {
  formatUtcDateTime,
  isImeComposing,
  SPAIN_TIMEZONES,
} from '@/lib/ime'

interface StoreFormState {
  name: string
  code: string
  timezone: string
  address: string
  city: string
  postal_code: string
}

const EMPTY_FORM: StoreFormState = {
  name: '',
  code: '',
  timezone: 'Europe/Madrid',
  address: '',
  city: '',
  postal_code: '',
}

export function StoresPage() {
  const { t, i18n } = useTranslation()
  const { activeTenant } = useAuth()
  const canManage =
    activeTenant?.role === 'owner' || activeTenant?.role === 'hr'

  const [stores, setStores] = useState<StoreResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [errorBanner, setErrorBanner] = useState<string | null>(null)

  const [drawerOpen, setDrawerOpen] = useState(false)
  const [editingStore, setEditingStore] = useState<StoreResponse | null>(null)
  const [form, setForm] = useState<StoreFormState>(EMPTY_FORM)
  const [saving, setSaving] = useState(false)
  const [drawerError, setDrawerError] = useState<string | null>(null)
  const [archiveTarget, setArchiveTarget] = useState<StoreResponse | null>(null)

  const loadStores = useCallback(async () => {
    setLoading(true)
    setErrorBanner(null)
    try {
      const { data, error } = await apiClient.GET('/api/v1/stores')
      if (error || !data) {
        setErrorBanner(extractApiError(error, t('common.error')))
        return
      }
      setStores(data.items)
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    void loadStores()
  }, [loadStores, activeTenant?.tenant_id])

  const filteredStores = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    if (!q) return stores
    return stores.filter(
      (item) =>
        item.name.toLowerCase().includes(q) ||
        item.code.toLowerCase().includes(q) ||
        item.city.toLowerCase().includes(q),
    )
  }, [stores, searchQuery])

  const openCreateDrawer = () => {
    setEditingStore(null)
    setForm({
      ...EMPTY_FORM,
      timezone: activeTenant?.default_timezone ?? 'Europe/Madrid',
    })
    setDrawerError(null)
    setDrawerOpen(true)
  }

  const openEditDrawer = (store: StoreResponse) => {
    setEditingStore(store)
    setForm({
      name: store.name,
      code: store.code,
      timezone: store.timezone,
      address: store.address,
      city: store.city,
      postal_code: store.postal_code,
    })
    setDrawerError(null)
    setDrawerOpen(true)
  }

  const handleSave = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!canManage) return
    setSaving(true)
    setDrawerError(null)
    try {
      if (editingStore) {
        const { error } = await apiClient.PATCH('/api/v1/stores/{store_id}', {
          params: { path: { store_id: editingStore.id } },
          body: { ...form, is_active: editingStore.is_active },
        })
        if (error) {
          setDrawerError(extractApiError(error, t('common.error')))
          return
        }
      } else {
        const { error } = await apiClient.POST('/api/v1/stores', {
          body: form,
        })
        if (error) {
          setDrawerError(extractApiError(error, t('common.error')))
          return
        }
      }
      setDrawerOpen(false)
      await loadStores()
    } finally {
      setSaving(false)
    }
  }

  const handleArchiveConfirm = async () => {
    if (!archiveTarget || !canManage) return
    const { error } = await apiClient.DELETE('/api/v1/stores/{store_id}', {
      params: { path: { store_id: archiveTarget.id } },
    })
    if (error) {
      setDrawerError(extractApiError(error, t('common.error')))
      setArchiveTarget(null)
      return
    }
    setArchiveTarget(null)
    setDrawerOpen(false)
    await loadStores()
  }

  const columns = useMemo<ColumnDef<StoreResponse>[]>(
    () => [
      {
        accessorKey: 'code',
        header: t('stores.columns.code'),
        cell: ({ row }) => (
          <span className="font-mono text-xs font-medium text-slate-700 tabular-nums">
            {row.original.code}
          </span>
        ),
      },
      {
        accessorKey: 'name',
        header: t('stores.columns.name'),
        cell: ({ row }) => (
          <span className="font-medium text-slate-900">{row.original.name}</span>
        ),
      },
      {
        accessorKey: 'timezone',
        header: t('stores.columns.timezone'),
        cell: ({ row }) => (
          <span className="text-xs text-slate-600 tabular-nums">
            {row.original.timezone}
          </span>
        ),
      },
      {
        accessorKey: 'city',
        header: t('stores.columns.city'),
        cell: ({ row }) => (
          <span className="text-slate-600">
            {[row.original.city, row.original.address]
              .filter(Boolean)
              .join(' · ') || '—'}
          </span>
        ),
      },
      {
        accessorKey: 'is_active',
        header: t('stores.columns.status'),
        cell: ({ row }) => (
          <Badge
            variant="outline"
            className={
              row.original.is_active
                ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                : 'border-slate-200 bg-slate-100 text-slate-600'
            }
          >
            {row.original.is_active
              ? t('statuses.active')
              : t('statuses.inactive')}
          </Badge>
        ),
      },
      {
        accessorKey: 'updated_at',
        header: t('stores.columns.updatedAt'),
        cell: ({ row }) => (
          <span className="text-xs text-slate-500 tabular-nums">
            {formatUtcDateTime(
              row.original.updated_at,
              i18n.language,
              row.original.timezone,
            )}
          </span>
        ),
      },
    ],
    [t, i18n.language],
  )

  const table = useReactTable({
    data: filteredStores,
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h1 id="stores-table-heading" className="text-lg font-semibold text-slate-900">
            {t('stores.title')}
          </h1>
          <p className="text-sm text-slate-600">{t('stores.subtitle')}</p>
        </div>

        {canManage && (
          <Button type="button" onClick={openCreateDrawer}>
            <Plus className="h-4 w-4" aria-hidden="true" />
            {t('stores.addButton')}
          </Button>
        )}
      </div>

      {!canManage && (
        <div className="rounded-md border border-slate-200 bg-white px-3.5 py-2.5 text-xs text-slate-600">
          {t('stores.readOnlyHint')}
        </div>
      )}

      {errorBanner && (
        <div
          role="alert"
          className="rounded-md border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-xs text-rose-700"
        >
          {errorBanner}
        </div>
      )}

      {/* Template 1: High-Density Edge-to-Edge Card Table */}
      <section className="overflow-hidden rounded-md border border-slate-200 bg-white">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 py-3">
          <div className="relative min-w-56 flex-1 sm:max-w-sm">
            <Search
              className="pointer-events-none absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-slate-400"
              aria-hidden="true"
            />
            <Input
              type="search"
              aria-label={t('stores.searchPlaceholder')}
              enterKeyHint="search"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && isImeComposing(e)) {
                  e.preventDefault()
                }
              }}
              placeholder={t('stores.searchPlaceholder')}
              className="h-8 pl-8 text-sm"
            />
          </div>
          <span className="text-xs text-slate-500 tabular-nums">
            {t('stores.count', { count: filteredStores.length })}
          </span>
        </div>

        <div
          tabIndex={0}
          role="region"
          aria-labelledby="stores-table-heading"
          className="overflow-x-auto focus-visible:ring-2 focus-visible:ring-teal-700 focus-visible:outline-none"
        >
          <Table>
            <TableHeader className="sticky top-0 bg-slate-50">
              {table.getHeaderGroups().map((headerGroup) => (
                <TableRow key={headerGroup.id} className="border-slate-200">
                  {headerGroup.headers.map((header) => (
                    <TableHead
                      key={header.id}
                      className="px-4 py-2.5 text-xs font-medium whitespace-nowrap text-slate-600"
                    >
                      {header.isPlaceholder
                        ? null
                        : flexRender(
                            header.column.columnDef.header,
                            header.getContext(),
                          )}
                    </TableHead>
                  ))}
                </TableRow>
              ))}
            </TableHeader>
            <TableBody>
              {loading ? (
                Array.from({ length: 3 }).map((_, idx) => (
                  <TableRow key={idx}>
                    <TableCell colSpan={columns.length} className="px-4 py-3">
                      <Skeleton className="h-5 w-full" />
                    </TableCell>
                  </TableRow>
                ))
              ) : table.getRowModel().rows.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={columns.length}
                    className="px-4 py-10 text-center text-sm text-slate-500"
                  >
                    {t('stores.empty')}
                  </TableCell>
                </TableRow>
              ) : (
                table.getRowModel().rows.map((row) => (
                  <TableRow
                    key={row.id}
                    onClick={() => openEditDrawer(row.original)}
                    className="cursor-pointer border-slate-200 hover:bg-slate-50"
                  >
                    {row.getVisibleCells().map((cell) => (
                      <TableCell
                        key={cell.id}
                        className="px-4 py-2.5 text-sm whitespace-nowrap"
                      >
                        {flexRender(
                          cell.column.columnDef.cell,
                          cell.getContext(),
                        )}
                      </TableCell>
                    ))}
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </section>

      {/* Right Slide-Over Sheet Drawer */}
      <Sheet open={drawerOpen} onOpenChange={setDrawerOpen}>
        <SheetContent
          side="right"
          className="flex w-full flex-col overscroll-contain bg-white sm:max-w-md"
        >
          <SheetHeader className="border-b border-slate-200 px-6 py-4">
            <SheetTitle>
              {editingStore
                ? t('stores.drawer.editTitle')
                : t('stores.drawer.createTitle')}
            </SheetTitle>
            <SheetDescription>
              {t('stores.drawer.description')}
            </SheetDescription>
          </SheetHeader>

          <form
            id="store-drawer-form"
            onSubmit={handleSave}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && isImeComposing(e)) {
                e.preventDefault()
              }
            }}
            className="flex-1 space-y-4 overflow-y-auto px-6 py-4"
          >
            {drawerError && (
              <div
                role="alert"
                className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
              >
                {drawerError}
              </div>
            )}

            <div className="space-y-1.5">
              <Label htmlFor="store-code">{t('stores.drawer.code')}</Label>
              <Input
                id="store-code"
                required
                disabled={!canManage}
                spellCheck={false}
                className="uppercase tabular-nums"
                value={form.code}
                onChange={(e) => setForm({ ...form, code: e.target.value })}
                placeholder="MAD-01"
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="store-name">{t('stores.drawer.name')}</Label>
              <Input
                id="store-name"
                required
                disabled={!canManage}
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="Madrid Sol"
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="store-tz">{t('stores.drawer.timezone')}</Label>
              <Select
                disabled={!canManage}
                value={form.timezone}
                onValueChange={(value) => setForm({ ...form, timezone: value })}
              >
                <SelectTrigger id="store-tz" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SPAIN_TIMEZONES.map((tz) => (
                    <SelectItem key={tz} value={tz}>
                      {tz}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="store-address">
                {t('stores.drawer.address')}
              </Label>
              <Input
                id="store-address"
                disabled={!canManage}
                value={form.address}
                onChange={(e) => setForm({ ...form, address: e.target.value })}
                placeholder="Calle Mayor 12"
              />
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="store-city">{t('stores.drawer.city')}</Label>
                <Input
                  id="store-city"
                  disabled={!canManage}
                  value={form.city}
                  onChange={(e) => setForm({ ...form, city: e.target.value })}
                  placeholder="Madrid"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="store-postal">
                  {t('stores.drawer.postalCode')}
                </Label>
                <Input
                  id="store-postal"
                  disabled={!canManage}
                  className="tabular-nums"
                  value={form.postal_code}
                  onChange={(e) =>
                    setForm({ ...form, postal_code: e.target.value })
                  }
                  placeholder="28013"
                />
              </div>
            </div>
          </form>

          <SheetFooter className="flex-row flex-wrap items-center justify-between gap-2 border-t border-slate-200 px-6 py-4">
            {editingStore && canManage ? (
              <Button
                type="button"
                variant="outline"
                className="border-rose-200 text-rose-700 hover:bg-rose-50 hover:text-rose-800"
                onClick={() => setArchiveTarget(editingStore)}
              >
                <Trash2 className="h-4 w-4" aria-hidden="true" />
                {t('stores.drawer.archive')}
              </Button>
            ) : (
              <span />
            )}

            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => setDrawerOpen(false)}
              >
                {t('stores.drawer.cancel')}
              </Button>
              {canManage && (
                <Button
                  type="submit"
                  form="store-drawer-form"
                  disabled={saving}
                >
                  {saving ? t('stores.drawer.saving') : t('stores.drawer.save')}
                </Button>
              )}
            </div>
          </SheetFooter>
        </SheetContent>
      </Sheet>

      <AlertDialog
        open={archiveTarget !== null}
        onOpenChange={(open) => {
          if (!open) setArchiveTarget(null)
        }}
      >
        <AlertDialogContent className="bg-white">
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t('stores.archiveDialog.title')}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t('stores.archiveDialog.description')}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>
              {t('stores.archiveDialog.cancel')}
            </AlertDialogCancel>
            <AlertDialogAction
              className="bg-rose-700 text-white hover:bg-rose-800"
              onClick={() => void handleArchiveConfirm()}
            >
              {t('stores.archiveDialog.confirm')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

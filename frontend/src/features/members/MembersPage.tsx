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
import type {
  MembershipResponse,
  MembershipStatus,
  StoreResponse,
  UserRole,
} from '@/api/types'
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

const ALL_ROLES: UserRole[] = [
  'owner',
  'hr',
  'store_manager',
  'accountant',
  'employee',
]

const ROLE_BADGE_CLASS: Record<UserRole, string> = {
  owner: 'border-slate-300 bg-slate-900 text-white',
  hr: 'border-teal-200 bg-teal-50 text-teal-800',
  store_manager: 'border-amber-200 bg-amber-50 text-amber-800',
  accountant: 'border-slate-200 bg-slate-100 text-slate-700',
  employee: 'border-slate-200 bg-white text-slate-600',
}

interface MemberFormState {
  user_id: string
  email: string
  display_name: string
  role: UserRole
  status: MembershipStatus
  store_ids: string[]
}

const EMPTY_FORM: MemberFormState = {
  user_id: '',
  email: '',
  display_name: '',
  role: 'store_manager',
  status: 'active',
  store_ids: [],
}

export function MembersPage() {
  const { t, i18n } = useTranslation()
  const { activeTenant } = useAuth()
  const isOwner = activeTenant?.role === 'owner'
  const canManage = isOwner || activeTenant?.role === 'hr'

  const [members, setMembers] = useState<MembershipResponse[]>([])
  const [stores, setStores] = useState<StoreResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [errorBanner, setErrorBanner] = useState<string | null>(null)

  const [drawerOpen, setDrawerOpen] = useState(false)
  const [editingMember, setEditingMember] = useState<MembershipResponse | null>(
    null,
  )
  const [form, setForm] = useState<MemberFormState>(EMPTY_FORM)
  const [saving, setSaving] = useState(false)
  const [drawerError, setDrawerError] = useState<string | null>(null)
  const [archiveTarget, setArchiveTarget] = useState<MembershipResponse | null>(
    null,
  )

  const loadData = useCallback(async () => {
    setLoading(true)
    setErrorBanner(null)
    try {
      const [membersRes, storesRes] = await Promise.all([
        apiClient.GET('/api/v1/memberships'),
        apiClient.GET('/api/v1/stores'),
      ])
      if (membersRes.error || !membersRes.data) {
        setErrorBanner(extractApiError(membersRes.error, t('common.error')))
        return
      }
      setMembers(membersRes.data.items)
      if (storesRes.data) {
        setStores(storesRes.data.items)
      }
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    void loadData()
  }, [loadData, activeTenant?.tenant_id])

  const storeMap = useMemo(
    () => new Map(stores.map((store) => [store.id, store])),
    [stores],
  )

  const filteredMembers = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    if (!q) return members
    return members.filter(
      (item) =>
        item.display_name.toLowerCase().includes(q) ||
        item.email.toLowerCase().includes(q) ||
        item.role.toLowerCase().includes(q),
    )
  }, [members, searchQuery])

  const assignableRoles = useMemo(
    () => (isOwner ? ALL_ROLES : ALL_ROLES.filter((role) => role !== 'owner')),
    [isOwner],
  )

  const canEditTarget =
    canManage && (isOwner || editingMember?.role !== 'owner')

  const openCreateDrawer = () => {
    setEditingMember(null)
    setForm({ ...EMPTY_FORM, user_id: crypto.randomUUID() })
    setDrawerError(null)
    setDrawerOpen(true)
  }

  const openEditDrawer = (member: MembershipResponse) => {
    setEditingMember(member)
    setForm({
      user_id: member.user_id,
      email: member.email,
      display_name: member.display_name,
      role: member.role,
      status: member.status,
      store_ids: [...member.store_ids],
    })
    setDrawerError(null)
    setDrawerOpen(true)
  }

  const toggleStoreId = (storeId: string, checked: boolean) => {
    setForm((prev) => {
      const next = new Set(prev.store_ids)
      if (checked) {
        next.add(storeId)
      } else {
        next.delete(storeId)
      }
      return { ...prev, store_ids: Array.from(next) }
    })
  }

  const handleSave = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!canEditTarget) return
    setSaving(true)
    setDrawerError(null)
    try {
      if (editingMember) {
        const { error } = await apiClient.PATCH(
          '/api/v1/memberships/{membership_id}',
          {
            params: { path: { membership_id: editingMember.id } },
            body: {
              display_name: form.display_name,
              role: form.role,
              status: form.status,
              store_ids: form.store_ids,
            },
          },
        )
        if (error) {
          setDrawerError(extractApiError(error, t('common.error')))
          return
        }
      } else {
        const { error } = await apiClient.POST('/api/v1/memberships', {
          body: form,
        })
        if (error) {
          setDrawerError(extractApiError(error, t('common.error')))
          return
        }
      }
      setDrawerOpen(false)
      await loadData()
    } finally {
      setSaving(false)
    }
  }

  const handleArchiveConfirm = async () => {
    if (!archiveTarget || !canEditTarget) return
    const { error } = await apiClient.DELETE(
      '/api/v1/memberships/{membership_id}',
      {
        params: { path: { membership_id: archiveTarget.id } },
      },
    )
    if (error) {
      setDrawerError(extractApiError(error, t('common.error')))
      setArchiveTarget(null)
      return
    }
    setArchiveTarget(null)
    setDrawerOpen(false)
    await loadData()
  }

  const columns = useMemo<ColumnDef<MembershipResponse>[]>(
    () => [
      {
        accessorKey: 'display_name',
        header: t('members.columns.member'),
        cell: ({ row }) => (
          <div className="flex flex-col">
            <span className="font-medium text-slate-900">
              {row.original.display_name}
            </span>
            <span className="text-xs text-slate-500">{row.original.email}</span>
          </div>
        ),
      },
      {
        accessorKey: 'role',
        header: t('members.columns.role'),
        cell: ({ row }) => (
          <Badge variant="outline" className={ROLE_BADGE_CLASS[row.original.role]}>
            {t(`roles.${row.original.role}`)}
          </Badge>
        ),
      },
      {
        accessorKey: 'store_ids',
        header: t('members.columns.stores'),
        cell: ({ row }) => {
          const { role, store_ids } = row.original
          if (role === 'owner' || role === 'hr' || role === 'accountant') {
            return (
              <span className="text-xs text-slate-500">
                {t('members.allStoresScope')}
              </span>
            )
          }
          if (store_ids.length === 0) {
            return (
              <span className="text-xs text-amber-700">
                {t('members.noStoresScope')}
              </span>
            )
          }
          return (
            <div className="flex flex-wrap gap-1">
              {store_ids.map((id) => {
                const store = storeMap.get(id)
                return (
                  <Badge
                    key={id}
                    variant="outline"
                    className="border-slate-200 bg-slate-50 text-slate-700 tabular-nums"
                  >
                    {store ? `${store.code} ${store.name}` : id.slice(0, 8)}
                  </Badge>
                )
              })}
            </div>
          )
        },
      },
      {
        accessorKey: 'status',
        header: t('members.columns.status'),
        cell: ({ row }) => {
          const st = row.original.status
          const badgeClass =
            st === 'active'
              ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
              : st === 'invited'
                ? 'border-amber-200 bg-amber-50 text-amber-700'
                : 'border-slate-200 bg-slate-100 text-slate-600'
          return (
            <Badge variant="outline" className={badgeClass}>
              {t(`statuses.${st}`)}
            </Badge>
          )
        },
      },
      {
        accessorKey: 'updated_at',
        header: t('members.columns.updatedAt'),
        cell: ({ row }) => (
          <span className="text-xs text-slate-500 tabular-nums">
            {formatUtcDateTime(
              row.original.updated_at,
              i18n.language,
              activeTenant?.default_timezone ?? 'Europe/Madrid',
            )}
          </span>
        ),
      },
    ],
    [t, i18n.language, storeMap, activeTenant?.default_timezone],
  )

  const table = useReactTable({
    data: filteredMembers,
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h1
            id="members-table-heading"
            className="text-lg font-semibold text-slate-900"
          >
            {t('members.title')}
          </h1>
          <p className="text-sm text-slate-600">{t('members.subtitle')}</p>
        </div>

        {canManage && (
          <Button type="button" onClick={openCreateDrawer}>
            <Plus className="h-4 w-4" aria-hidden="true" />
            {t('members.addButton')}
          </Button>
        )}
      </div>

      {!canManage && (
        <div className="rounded-md border border-slate-200 bg-white px-3.5 py-2.5 text-xs text-slate-600">
          {t('members.readOnlyHint')}
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

      <section className="overflow-hidden rounded-md border border-slate-200 bg-white">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 py-3">
          <div className="relative min-w-56 flex-1 sm:max-w-sm">
            <Search
              className="pointer-events-none absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-slate-400"
              aria-hidden="true"
            />
            <Input
              type="search"
              aria-label={t('members.searchPlaceholder')}
              enterKeyHint="search"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && isImeComposing(e)) {
                  e.preventDefault()
                }
              }}
              placeholder={t('members.searchPlaceholder')}
              className="h-8 pl-8 text-sm"
            />
          </div>
          <span className="text-xs text-slate-500 tabular-nums">
            {t('members.count', { count: filteredMembers.length })}
          </span>
        </div>

        <div
          tabIndex={0}
          role="region"
          aria-labelledby="members-table-heading"
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
                    {t('members.empty')}
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

      <Sheet open={drawerOpen} onOpenChange={setDrawerOpen}>
        <SheetContent
          side="right"
          className="flex w-full flex-col overscroll-contain bg-white sm:max-w-md"
        >
          <SheetHeader className="border-b border-slate-200 px-6 py-4">
            <SheetTitle>
              {editingMember
                ? t('members.drawer.editTitle')
                : t('members.drawer.createTitle')}
            </SheetTitle>
            <SheetDescription>
              {t('members.drawer.description')}
            </SheetDescription>
          </SheetHeader>

          <form
            id="member-drawer-form"
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

            {!editingMember && (
              <>
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between gap-2">
                    <Label htmlFor="member-uid">
                      {t('members.drawer.userId')}
                    </Label>
                    <Button
                      type="button"
                      size="xs"
                      variant="ghost"
                      onClick={() =>
                        setForm((prev) => ({
                          ...prev,
                          user_id: crypto.randomUUID(),
                        }))
                      }
                    >
                      {t('members.drawer.generateUuid')}
                    </Button>
                  </div>
                  <Input
                    id="member-uid"
                    required
                    spellCheck={false}
                    className="font-mono text-xs tabular-nums"
                    value={form.user_id}
                    onChange={(e) =>
                      setForm({ ...form, user_id: e.target.value })
                    }
                  />
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="member-email">
                    {t('members.drawer.email')}
                  </Label>
                  <Input
                    id="member-email"
                    type="email"
                    required
                    spellCheck={false}
                    value={form.email}
                    onChange={(e) =>
                      setForm({ ...form, email: e.target.value })
                    }
                    placeholder="manager@empresa.es"
                  />
                </div>
              </>
            )}

            <div className="space-y-1.5">
              <Label htmlFor="member-name">
                {t('members.drawer.displayName')}
              </Label>
              <Input
                id="member-name"
                required
                disabled={!canEditTarget}
                value={form.display_name}
                onChange={(e) =>
                  setForm({ ...form, display_name: e.target.value })
                }
              />
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="member-role">{t('members.drawer.role')}</Label>
                <Select
                  disabled={!canEditTarget}
                  value={form.role}
                  onValueChange={(value) =>
                    setForm({ ...form, role: value as UserRole })
                  }
                >
                  <SelectTrigger id="member-role" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {(editingMember && !canEditTarget
                      ? ALL_ROLES
                      : assignableRoles
                    ).map((role) => (
                      <SelectItem key={role} value={role}>
                        {t(`roles.${role}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="member-status">
                  {t('members.drawer.status')}
                </Label>
                <Select
                  disabled={!canEditTarget}
                  value={form.status}
                  onValueChange={(value) =>
                    setForm({ ...form, status: value as MembershipStatus })
                  }
                >
                  <SelectTrigger id="member-status" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="active">
                      {t('statuses.active')}
                    </SelectItem>
                    <SelectItem value="invited">
                      {t('statuses.invited')}
                    </SelectItem>
                    <SelectItem value="disabled">
                      {t('statuses.disabled')}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="space-y-2 pt-1">
              <Label>{t('members.drawer.assignedStores')}</Label>
              <div className="space-y-2 rounded-md border border-slate-200 bg-slate-50 p-3">
                {stores.length === 0 ? (
                  <p className="text-xs text-slate-500">{t('stores.empty')}</p>
                ) : (
                  stores.map((store) => {
                    const checked = form.store_ids.includes(store.id)
                    const checkboxId = `assign-store-${store.id}`
                    return (
                      <div
                        key={store.id}
                        className="flex items-center gap-2.5 text-sm"
                      >
                        <Checkbox
                          id={checkboxId}
                          disabled={!canEditTarget}
                          checked={checked}
                          onCheckedChange={(val) =>
                            toggleStoreId(store.id, val === true)
                          }
                        />
                        <Label
                          htmlFor={checkboxId}
                          className="cursor-pointer font-normal text-slate-700"
                        >
                          <span className="mr-1.5 font-mono text-xs text-slate-500 tabular-nums">
                            {store.code}
                          </span>
                          {store.name}
                        </Label>
                      </div>
                    )
                  })
                )}
              </div>
            </div>
          </form>

          <SheetFooter className="flex-row flex-wrap items-center justify-between gap-2 border-t border-slate-200 px-6 py-4">
            {editingMember && canEditTarget ? (
              <Button
                type="button"
                variant="outline"
                className="border-rose-200 text-rose-700 hover:bg-rose-50 hover:text-rose-800"
                onClick={() => setArchiveTarget(editingMember)}
              >
                <Trash2 className="h-4 w-4" aria-hidden="true" />
                {t('members.drawer.archive')}
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
                {t('members.drawer.cancel')}
              </Button>
              {canEditTarget && (
                <Button
                  type="submit"
                  form="member-drawer-form"
                  disabled={saving}
                >
                  {saving
                    ? t('members.drawer.saving')
                    : t('members.drawer.save')}
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
              {t('members.archiveDialog.title')}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t('members.archiveDialog.description')}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>
              {t('members.archiveDialog.cancel')}
            </AlertDialogCancel>
            <AlertDialogAction
              className="bg-rose-700 text-white hover:bg-rose-800"
              onClick={() => void handleArchiveConfirm()}
            >
              {t('members.archiveDialog.confirm')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

import { useCallback, useEffect, useRef, useState } from 'react'
import { Coffee, Plus, RotateCcw, Save, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { apiClient, extractApiError } from '@/api/client'
import type {
  BreakMode,
  BreakPolicyResponse,
  BreakScopeType,
  BreakTrackingMode,
  BreakWindowSchema,
  EffectiveBreakPolicyResponse,
} from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { useAuth } from '@/features/auth/AuthContext'
import { cn } from '@/lib/utils'

interface BreakPolicyFormProps {
  scopeType: BreakScopeType
  storeId?: string
  employeeId?: string
  readOnly?: boolean
  onSaved?: () => void | Promise<void>
}

const DURATION_PRESETS = [15, 30, 45, 60, 90]

export function BreakPolicyForm(props: BreakPolicyFormProps) {
  const { activeTenant } = useAuth()
  const { scopeType, storeId, employeeId } = props
  const scopeId = scopeType === 'store' ? storeId : employeeId

  return (
    <ScopedBreakPolicyForm
      key={JSON.stringify([activeTenant?.tenant_id, scopeType, scopeId])}
      {...props}
    />
  )
}

function ScopedBreakPolicyForm({
  scopeType,
  storeId,
  employeeId,
  readOnly = false,
  onSaved,
}: BreakPolicyFormProps) {
  const { t, i18n } = useTranslation()
  const isEs = (i18n.language || 'zh-CN').startsWith('es')

  const [loadStatus, setLoadStatus] = useState<'loading' | 'ready' | 'failed'>(
    'loading',
  )
  const loadRequest = useRef<AbortController | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const [existingPolicy, setExistingPolicy] =
    useState<BreakPolicyResponse | null>(null)
  const [effectivePolicy, setEffectivePolicy] =
    useState<EffectiveBreakPolicyResponse | null>(null)

  const [breakMode, setBreakMode] = useState<BreakMode>('fixed_duration')
  const [durationMinutes, setDurationMinutes] = useState<number>(30)
  const [windows, setWindows] = useState<BreakWindowSchema[]>([
    { start: '14:00', end: '16:00' },
  ])
  const [isPaid, setIsPaid] = useState<boolean>(false)
  const [trackingMode, setTrackingMode] =
    useState<BreakTrackingMode>('manual_punch')
  const [minShiftMinutes, setMinShiftMinutes] = useState<number>(360)
  const [autoEndManualBreak, setAutoEndManualBreak] = useState<boolean>(true)

  const applyPolicyValues = useCallback(
    (
      src: {
        break_mode: BreakMode
        duration_minutes?: number | null
        windows?: BreakWindowSchema[]
        is_paid: boolean
        tracking_mode: BreakTrackingMode
        min_shift_minutes_for_auto_deduct: number
        auto_end_manual_break: boolean
      } | null,
    ) => {
      if (!src) {
        setBreakMode('fixed_duration')
        setDurationMinutes(30)
        setWindows([{ start: '14:00', end: '16:00' }])
        setIsPaid(false)
        setTrackingMode('manual_punch')
        setMinShiftMinutes(360)
        setAutoEndManualBreak(true)
        return
      }
      setBreakMode(src.break_mode)
      setDurationMinutes(src.duration_minutes ?? 30)
      setWindows(
        src.windows && src.windows.length > 0
          ? src.windows
          : [{ start: '14:00', end: '16:00' }],
      )
      setIsPaid(src.is_paid)
      setTrackingMode(src.tracking_mode)
      setMinShiftMinutes(src.min_shift_minutes_for_auto_deduct ?? 360)
      setAutoEndManualBreak(src.auto_end_manual_break ?? true)
    },
    [],
  )

  const loadPolicy = useCallback(async () => {
    loadRequest.current?.abort()
    const request = new AbortController()
    loadRequest.current = request
    const { signal } = request
    setLoadStatus('loading')
    setError(null)

    try {
      if (
        (scopeType === 'employee' && !employeeId) ||
        (scopeType === 'store' && !storeId)
      ) {
        setLoadStatus('failed')
        return
      }

      const listRes = await apiClient.GET('/api/v1/break-policies', { signal })
      if (signal.aborted) return
      if (!listRes.response.ok || !listRes.data) {
        setLoadStatus('failed')
        return
      }

      const matched =
        listRes.data.items.find((p) => {
          if (p.scope_type !== scopeType) return false
          if (scopeType === 'store') return p.store_id === storeId
          if (scopeType === 'employee') return p.employee_id === employeeId
          return true
        }) ?? null

      let effective: EffectiveBreakPolicyResponse | null = null
      if (scopeType === 'employee' && employeeId) {
        const effRes = await apiClient.GET(
          '/api/v1/break-policies/effective/{employee_id}',
          { params: { path: { employee_id: employeeId } }, signal },
        )
        if (signal.aborted) return
        if (!effRes.response.ok || !effRes.data) {
          setLoadStatus('failed')
          return
        }
        effective = effRes.data
      }

      setExistingPolicy(matched)
      setEffectivePolicy(effective)
      applyPolicyValues(matched ?? effective)
      setLoadStatus('ready')
    } catch {
      if (!signal.aborted) setLoadStatus('failed')
    }
  }, [scopeType, storeId, employeeId, applyPolicyValues])

  useEffect(() => {
    void loadPolicy()
    return () => loadRequest.current?.abort()
  }, [loadPolicy])

  const canWrite = loadStatus === 'ready' && !readOnly && !saving

  const handleSave = async () => {
    const signal = loadRequest.current?.signal
    if (!canWrite || !signal || signal.aborted) return
    setError(null)
    setNotice(null)

    if (breakMode === 'fixed_duration') {
      if (!Number.isFinite(durationMinutes) || durationMinutes <= 0) {
        setError(
          isEs
            ? 'Indique una duración válida en minutos.'
            : '请填写有效的休息时长（分钟）。',
        )
        return
      }
    } else {
      if (windows.length === 0) {
        setError(
          isEs
            ? 'Añada al menos una franja horaria.'
            : '请至少添加一条休息时段。',
        )
        return
      }
      for (const w of windows) {
        if (!w.start || !w.end || w.end <= w.start) {
          setError(
            isEs
              ? 'Cada franja debe tener hora de fin posterior a la de inicio.'
              : '每个休息时段的结束时间必须晚于开始时间。',
          )
          return
        }
      }
    }

    setSaving(true)
    try {
      const body = {
        break_mode: breakMode,
        duration_minutes:
          breakMode === 'fixed_duration' ? durationMinutes : null,
        windows: breakMode === 'fixed_window' ? windows : [],
        is_paid: isPaid,
        tracking_mode: trackingMode,
        min_shift_minutes_for_auto_deduct: minShiftMinutes,
        auto_end_manual_break: autoEndManualBreak,
        is_active: true,
      }

      if (scopeType === 'tenant') {
        const res = await apiClient.PUT('/api/v1/break-policies/tenant', {
          body,
          signal,
        })
        if (res.error) {
          setError(extractApiError(res.error, t('errors.GENERIC')))
          return
        }
      } else if (scopeType === 'store' && storeId) {
        const res = await apiClient.PUT(
          '/api/v1/break-policies/store/{store_id}',
          {
            params: { path: { store_id: storeId } },
            body,
            signal,
          },
        )
        if (res.error) {
          setError(extractApiError(res.error, t('errors.GENERIC')))
          return
        }
      } else if (scopeType === 'employee' && employeeId) {
        const res = await apiClient.PUT(
          '/api/v1/break-policies/employee/{employee_id}',
          {
            params: { path: { employee_id: employeeId } },
            body,
            signal,
          },
        )
        if (res.error) {
          setError(extractApiError(res.error, t('errors.GENERIC')))
          return
        }
      }

      if (signal.aborted) return
      setNotice(
        isEs ? 'Regla de descanso guardada.' : '休息规则已保存并生效。',
      )
      await loadPolicy()
      await onSaved?.()
    } catch (err) {
      setError(extractApiError(err, t('errors.GENERIC')))
    } finally {
      setSaving(false)
    }
  }

  const handleResetToInherited = async () => {
    const signal = loadRequest.current?.signal
    if (!canWrite || !existingPolicy || !signal || signal.aborted) return
    setSaving(true)
    setError(null)
    setNotice(null)
    try {
      const res = await apiClient.DELETE('/api/v1/break-policies/{policy_id}', {
        params: { path: { policy_id: existingPolicy.id } },
        signal,
      })
      if (res.error) {
        setError(extractApiError(res.error, t('errors.GENERIC')))
        return
      }
      if (signal.aborted) return
      setNotice(
        isEs
          ? 'Restablecido a la regla heredada.'
          : '已恢复为上级默认规则。',
      )
      await loadPolicy()
      await onSaved?.()
    } catch (err) {
      setError(extractApiError(err, t('errors.GENERIC')))
    } finally {
      setSaving(false)
    }
  }

  if (loadStatus === 'loading') {
    return <Skeleton className="h-44 w-full rounded-xl" />
  }

  const noticeAlert = notice && (
    <div
      role="status"
      className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700"
    >
      {notice}
    </div>
  )

  if (loadStatus === 'failed') {
    return (
      <div className="space-y-2">
        {noticeAlert}
        <p
          role="alert"
          className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
        >
          {isEs ? 'No se pudo cargar la regla de descanso.' : '无法加载休息规则。'}
        </p>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={saving}
          onClick={() => void loadPolicy()}
        >
          <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
          {isEs ? 'Reintentar' : '重试'}
        </Button>
      </div>
    )
  }

  const inheritanceBadgeLabel = (() => {
    if (scopeType !== 'employee' || !effectivePolicy) return null
    switch (effectivePolicy.inherited_from) {
      case 'employee':
        return isEs ? 'Personalizado' : '个人定制'
      case 'store':
        return isEs ? 'Heredado de tienda' : '继承门店默认'
      case 'tenant':
        return isEs ? 'Heredado de empresa' : '继承企业默认'
      default:
        return isEs ? 'Predeterminado' : '系统默认'
    }
  })()

  return (
    <div className="space-y-4">
      {inheritanceBadgeLabel && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-xs text-slate-600">
            <Coffee className="h-3.5 w-3.5 text-[#2563EB]" aria-hidden="true" />
            <span>{isEs ? 'Origen de la regla:' : '当前生效来源：'}</span>
            <Badge
              variant="outline"
              className={cn(
                'text-[11px]',
                effectivePolicy?.inherited_from === 'employee'
                  ? 'border-blue-200 bg-blue-50 text-[#2563EB]'
                  : 'border-slate-200 bg-slate-50 text-slate-700',
              )}
            >
              {inheritanceBadgeLabel}
            </Badge>
          </div>
        </div>
      )}

      {error && (
        <div
          role="alert"
          className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
        >
          {error}
        </div>
      )}

      {noticeAlert}

      {/* Break Mode Selector */}
      <div className="space-y-1.5">
        <Label className="text-xs">
          {isEs ? 'Modalidad de descanso' : '休息模式'}
        </Label>
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            disabled={readOnly}
            onClick={() => setBreakMode('fixed_duration')}
            className={cn(
              'rounded-lg border px-3 py-2 text-left text-xs font-medium transition-colors',
              breakMode === 'fixed_duration'
                ? 'border-[#2563EB] bg-blue-50/70 text-[#2563EB]'
                : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50',
            )}
          >
            {isEs ? 'Duración fija' : '固定时长'}
          </button>
          <button
            type="button"
            disabled={readOnly}
            onClick={() => setBreakMode('fixed_window')}
            className={cn(
              'rounded-lg border px-3 py-2 text-left text-xs font-medium transition-colors',
              breakMode === 'fixed_window'
                ? 'border-[#2563EB] bg-blue-50/70 text-[#2563EB]'
                : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50',
            )}
          >
            {isEs ? 'Franja fija / Jornada partida' : '固定时段 / 两段班午休'}
          </button>
        </div>
      </div>

      {breakMode === 'fixed_duration' ? (
        <div className="space-y-2">
          <Label htmlFor="bp-duration" className="text-xs">
            {isEs ? 'Duración (minutos)' : '每日预设休息时长（分钟）'}
          </Label>
          <div className="flex flex-wrap items-center gap-1.5">
            {DURATION_PRESETS.map((mins) => (
              <button
                key={mins}
                type="button"
                disabled={readOnly}
                onClick={() => setDurationMinutes(mins)}
                className={cn(
                  'rounded-md border px-2.5 py-1 text-xs font-medium tabular-nums transition-colors',
                  durationMinutes === mins
                    ? 'border-[#2563EB] bg-[#2563EB] text-white'
                    : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50',
                )}
              >
                {mins} min
              </button>
            ))}
            <Input
              id="bp-duration"
              type="number"
              min={5}
              max={360}
              disabled={readOnly}
              value={durationMinutes}
              onChange={(e) => setDurationMinutes(Number(e.target.value) || 0)}
              className="h-8 w-24 text-xs tabular-nums"
            />
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label className="text-xs">
              {isEs ? 'Franjas horarias (máx. 4)' : '固定休息时段（最多 4 段）'}
            </Label>
            {!readOnly && windows.length < 4 && (
              <Button
                type="button"
                size="xs"
                variant="outline"
                onClick={() =>
                  setWindows((prev) => [
                    ...prev,
                    { start: '14:00', end: '16:00' },
                  ])
                }
              >
                <Plus className="h-3 w-3" aria-hidden="true" />
                {isEs ? 'Añadir franja' : '添加时段'}
              </Button>
            )}
          </div>
          <div className="space-y-2">
            {windows.map((w, idx) => (
              <div key={idx} className="flex items-center gap-2">
                <Input
                  type="time"
                  disabled={readOnly}
                  value={w.start}
                  onChange={(e) =>
                    setWindows((prev) =>
                      prev.map((item, i) =>
                        i === idx ? { ...item, start: e.target.value } : item,
                      ),
                    )
                  }
                  className="h-8 text-xs tabular-nums"
                />
                <span className="text-xs text-slate-400">–</span>
                <Input
                  type="time"
                  disabled={readOnly}
                  value={w.end}
                  onChange={(e) =>
                    setWindows((prev) =>
                      prev.map((item, i) =>
                        i === idx ? { ...item, end: e.target.value } : item,
                      ),
                    )
                  }
                  className="h-8 text-xs tabular-nums"
                />
                {!readOnly && windows.length > 1 && (
                  <Button
                    type="button"
                    size="icon-sm"
                    variant="ghost"
                    onClick={() =>
                      setWindows((prev) => prev.filter((_, i) => i !== idx))
                    }
                    aria-label={isEs ? 'Eliminar franja' : '删除时段'}
                    className="text-slate-400 hover:text-rose-600"
                  >
                    <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                  </Button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Paid vs Unpaid */}
      <div className="space-y-1.5">
        <Label className="text-xs">
          {isEs ? 'Cómputo salarial' : '是否带薪计入工时'}
        </Label>
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            disabled={readOnly}
            onClick={() => setIsPaid(false)}
            className={cn(
              'rounded-lg border px-3 py-2 text-left text-xs font-medium transition-colors',
              !isPaid
                ? 'border-[#2563EB] bg-blue-50/70 text-[#2563EB]'
                : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50',
            )}
          >
            {isEs ? 'No retribuido (se descuenta)' : '不计薪（从工时扣除）'}
          </button>
          <button
            type="button"
            disabled={readOnly}
            onClick={() => setIsPaid(true)}
            className={cn(
              'rounded-lg border px-3 py-2 text-left text-xs font-medium transition-colors',
              isPaid
                ? 'border-[#2563EB] bg-blue-50/70 text-[#2563EB]'
                : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50',
            )}
          >
            {isEs ? 'Retribuido (cuenta como trabajo)' : '带薪休息（计入工时）'}
          </button>
        </div>
      </div>

      {/* Tracking Mode */}
      <div className="space-y-2">
        <Label className="text-xs">
          {isEs ? 'Modo de registro' : '打卡追踪方式'}
        </Label>
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            disabled={readOnly}
            onClick={() => setTrackingMode('manual_punch')}
            className={cn(
              'rounded-lg border px-3 py-2 text-left text-xs font-medium transition-colors',
              trackingMode === 'manual_punch'
                ? 'border-[#2563EB] bg-blue-50/70 text-[#2563EB]'
                : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50',
            )}
          >
            {isEs ? 'Fichaje manual (pausa/reanudar)' : '手动暂停 / 恢复'}
          </button>
          <button
            type="button"
            disabled={readOnly}
            onClick={() => setTrackingMode('auto_deduct')}
            className={cn(
              'rounded-lg border px-3 py-2 text-left text-xs font-medium transition-colors',
              trackingMode === 'auto_deduct'
                ? 'border-[#2563EB] bg-blue-50/70 text-[#2563EB]'
                : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50',
            )}
          >
            {isEs ? 'Descuento automático' : '满阈值自动扣减'}
          </button>
        </div>

        {trackingMode === 'manual_punch' ? (
          <label className="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2 text-xs text-slate-700">
            <input
              type="checkbox"
              disabled={readOnly}
              checked={autoEndManualBreak}
              onChange={(e) => setAutoEndManualBreak(e.target.checked)}
              className="h-4 w-4 rounded border-slate-300 text-[#2563EB]"
            />
            <span>
              {isEs
                ? 'Finalizar pausa automáticamente al cumplir la duración'
                : '到时自动结束休息并恢复工作计时'}
            </span>
          </label>
        ) : (
          <div className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2">
            <Label htmlFor="bp-min-shift" className="text-xs text-slate-700">
              {isEs
                ? 'Umbral mínimo de jornada (min)'
                : '触发自动扣减的最短班次（分钟）'}
            </Label>
            <Input
              id="bp-min-shift"
              type="number"
              min={60}
              max={720}
              disabled={readOnly}
              value={minShiftMinutes}
              onChange={(e) => setMinShiftMinutes(Number(e.target.value) || 360)}
              className="h-8 w-24 text-xs tabular-nums"
            />
          </div>
        )}
      </div>

      {!readOnly && (
        <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
          {existingPolicy && scopeType !== 'tenant' ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={!canWrite}
              onClick={() => void handleResetToInherited()}
              className="text-xs text-slate-600"
            >
              <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
              {isEs ? 'Restablecer heredado' : '恢复继承默认'}
            </Button>
          ) : (
            <span />
          )}
          <Button
            type="button"
            size="sm"
            disabled={!canWrite}
            onClick={() => void handleSave()}
            className="bg-[#2563EB] text-xs text-white hover:bg-blue-700"
          >
            <Save className="h-3.5 w-3.5" aria-hidden="true" />
            {saving
              ? isEs
                ? 'Guardando...'
                : '正在保存...'
              : isEs
                ? 'Guardar regla'
                : '保存休息规则'}
          </Button>
        </div>
      )}
    </div>
  )
}

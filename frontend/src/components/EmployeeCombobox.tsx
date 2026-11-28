import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react'
import { Check, ChevronsUpDown, Search, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { EmployeeResponse } from '@/api/types'
import { isImeComposing } from '@/lib/ime'
import { cn } from '@/lib/utils'

interface EmployeeComboboxProps {
  employees: EmployeeResponse[]
  value: string
  onValueChange: (value: string) => void
  includeAllOption?: boolean
  allValue?: string
  allLabel?: string
  allowClear?: boolean
  disabled?: boolean
  placeholder?: string
  searchPlaceholder?: string
  emptyLabel?: string
  id?: string
  ariaLabel?: string
  ariaInvalid?: boolean
  className?: string
  triggerClassName?: string
}

interface ComboboxOption {
  value: string
  label: string
  subLabel?: string
  searchText: string
}

export function EmployeeCombobox({
  employees,
  value,
  onValueChange,
  includeAllOption = false,
  allValue = 'all',
  allLabel,
  allowClear = false,
  disabled = false,
  placeholder,
  searchPlaceholder,
  emptyLabel,
  id,
  ariaLabel,
  ariaInvalid = false,
  className,
  triggerClassName,
}: EmployeeComboboxProps) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [highlightedIndex, setHighlightedIndex] = useState(0)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const searchInputRef = useRef<HTMLInputElement | null>(null)

  const resolvedAllLabel = allLabel ?? t('common.allEmployees')
  const resolvedPlaceholder = placeholder ?? t('common.selectEmployee')
  const resolvedSearchPlaceholder =
    searchPlaceholder ?? t('common.searchEmployeePlaceholder')
  const resolvedEmptyLabel = emptyLabel ?? t('common.noMatchingEmployees')

  const options = useMemo<ComboboxOption[]>(() => {
    const list: ComboboxOption[] = []
    if (includeAllOption) {
      list.push({
        value: allValue,
        label: resolvedAllLabel,
        searchText: resolvedAllLabel.toLowerCase(),
      })
    }
    for (const emp of employees) {
      const preferred =
        emp.preferred_name &&
        emp.preferred_name.trim() !== '' &&
        emp.preferred_name.trim().toLowerCase() !==
          emp.full_name.trim().toLowerCase()
          ? emp.preferred_name.trim()
          : undefined
      list.push({
        value: emp.id,
        label: `${emp.employee_code} · ${emp.full_name}`,
        subLabel: preferred,
        searchText: [emp.full_name, emp.preferred_name, emp.employee_code]
          .filter(Boolean)
          .join(' ')
          .toLowerCase(),
      })
    }
    return list
  }, [employees, includeAllOption, allValue, resolvedAllLabel])

  const filteredOptions = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return options
    return options.filter((opt) => opt.searchText.includes(q))
  }, [options, query])

  const selectedOption = useMemo(
    () => options.find((opt) => opt.value === value) ?? null,
    [options, value],
  )

  useEffect(() => {
    if (!open) return
    setHighlightedIndex(0)
    const rafId = window.requestAnimationFrame(() => {
      searchInputRef.current?.focus()
    })
    return () => window.cancelAnimationFrame(rafId)
  }, [open])

  useEffect(() => {
    if (!open) return
    const handlePointerDown = (event: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handlePointerDown)
    return () => document.removeEventListener('mousedown', handlePointerDown)
  }, [open])

  const handleSelect = (nextValue: string) => {
    onValueChange(nextValue)
    setOpen(false)
    setQuery('')
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      setOpen(false)
      return
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setHighlightedIndex((prev) =>
        filteredOptions.length === 0
          ? 0
          : (prev + 1) % filteredOptions.length,
      )
      return
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      setHighlightedIndex((prev) =>
        filteredOptions.length === 0
          ? 0
          : (prev - 1 + filteredOptions.length) % filteredOptions.length,
      )
      return
    }
    if (event.key === 'Enter' && !isImeComposing(event)) {
      event.preventDefault()
      const target = filteredOptions[highlightedIndex]
      if (target) {
        handleSelect(target.value)
      }
    }
  }

  const showClear =
    allowClear &&
    !disabled &&
    Boolean(value) &&
    (!includeAllOption || value !== allValue)

  return (
    <div ref={containerRef} className={cn('relative', className)}>
      <div className="relative flex items-center">
        <button
          id={id}
          type="button"
          role="combobox"
          aria-expanded={open}
          aria-haspopup="listbox"
          aria-label={ariaLabel}
          aria-invalid={ariaInvalid}
          disabled={disabled}
          onClick={() => {
            if (!disabled) {
              setOpen((prev) => !prev)
            }
          }}
          className={cn(
            'flex h-9 w-full items-center justify-between gap-2 rounded-md border border-slate-200 bg-white px-3 py-1.5 text-left text-xs text-slate-900 shadow-xs transition-colors hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50',
            ariaInvalid && 'border-rose-400 focus-visible:ring-rose-500',
            showClear && 'pr-12',
            triggerClassName,
          )}
        >
          <span
            className={cn(
              'min-w-0 flex-1 truncate tabular-nums',
              !selectedOption && 'text-slate-500',
            )}
          >
            {selectedOption ? selectedOption.label : resolvedPlaceholder}
          </span>
          <ChevronsUpDown
            className="h-3.5 w-3.5 shrink-0 text-slate-500"
            aria-hidden="true"
          />
        </button>

        {showClear && (
          <button
            type="button"
            aria-label={t('common.clearSelection')}
            onClick={(e) => {
              e.stopPropagation()
              onValueChange(includeAllOption ? allValue : '')
              setQuery('')
            }}
            className="absolute right-7 inline-flex h-5 w-5 items-center justify-center rounded-sm text-slate-500 hover:bg-slate-100 hover:text-slate-700 focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none"
          >
            <X className="h-3 w-3" aria-hidden="true" />
          </button>
        )}
      </div>

      {open && (
        <div className="absolute right-0 left-0 z-50 mt-1 overflow-hidden rounded-md border border-slate-200 bg-white shadow-md">
          <div className="flex items-center gap-2 border-b border-slate-100 px-2.5 py-2">
            <Search
              className="h-3.5 w-3.5 shrink-0 text-slate-500"
              aria-hidden="true"
            />
            <input
              ref={searchInputRef}
              type="text"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value)
                setHighlightedIndex(0)
              }}
              onKeyDown={handleKeyDown}
              placeholder={resolvedSearchPlaceholder}
              className="w-full bg-transparent text-xs text-slate-900 placeholder:text-slate-500 focus:outline-none"
            />
          </div>

          <ul
            role="listbox"
            className="max-h-60 overflow-y-auto p-1"
          >
            {filteredOptions.length === 0 ? (
              <li className="px-2.5 py-2 text-xs text-slate-600">
                {resolvedEmptyLabel}
              </li>
            ) : (
              filteredOptions.map((opt, idx) => {
                const isSelected = opt.value === value
                const isHighlighted = idx === highlightedIndex
                return (
                  <li
                    key={opt.value}
                    role="option"
                    aria-selected={isSelected}
                    onMouseEnter={() => setHighlightedIndex(idx)}
                    onMouseDown={(e) => {
                      e.preventDefault()
                      handleSelect(opt.value)
                    }}
                    className={cn(
                      'flex cursor-pointer items-center justify-between gap-2 rounded-sm px-2.5 py-1.5 text-xs text-slate-900',
                      isHighlighted && 'bg-slate-100',
                      isSelected && 'font-medium text-[#2563EB]',
                    )}
                  >
                    <div className="min-w-0 flex-1 truncate tabular-nums">
                      <span>{opt.label}</span>
                      {opt.subLabel && (
                        <span className="ml-1.5 text-slate-600">
                          ({opt.subLabel})
                        </span>
                      )}
                    </div>
                    {isSelected && (
                      <Check
                        className="h-3.5 w-3.5 shrink-0 text-[#2563EB]"
                        aria-hidden="true"
                      />
                    )}
                  </li>
                )
              })
            )}
          </ul>
        </div>
      )}
    </div>
  )
}

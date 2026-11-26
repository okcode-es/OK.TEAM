import type { LucideIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface EmptyStateProps {
  icon: LucideIcon
  title: string
  description?: string
  actionLabel?: string
  onAction?: () => void
  className?: string
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  actionLabel,
  onAction,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center rounded-xl border border-slate-200/75 bg-white px-6 py-10 text-center shadow-[0_1px_2px_rgba(15,23,42,0.04)]',
        className,
      )}
    >
      <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-full border border-slate-200 bg-slate-50 text-slate-500">
        <Icon className="h-5 w-5" aria-hidden="true" />
      </div>
      <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
      {description && (
        <p className="mt-1 max-w-sm text-xs leading-relaxed text-slate-500">
          {description}
        </p>
      )}
      {actionLabel && onAction && (
        <Button
          type="button"
          size="sm"
          onClick={onAction}
          className="mt-4 bg-[#2563EB] text-white hover:bg-blue-700"
        >
          {actionLabel}
        </Button>
      )}
    </div>
  )
}

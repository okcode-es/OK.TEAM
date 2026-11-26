import { useEffect, useMemo, useState } from 'react'
import { getApiBaseUrl } from '@/api/client'
import { cn } from '@/lib/utils'

interface UserAvatarProps {
  avatarUrl?: string | null
  name: string
  size?: 'xs' | 'sm' | 'md' | 'lg'
  className?: string
}

const SIZE_CLASSES: Record<NonNullable<UserAvatarProps['size']>, string> = {
  xs: 'h-6 w-6 text-[10px]',
  sm: 'h-8 w-8 text-xs',
  md: 'h-10 w-10 text-sm',
  lg: 'h-14 w-14 text-base',
}

function resolveAvatarUrl(raw?: string | null): string | null {
  if (!raw || !raw.trim()) return null
  const trimmed = raw.trim()
  if (trimmed.startsWith('http://') || trimmed.startsWith('https://')) {
    return trimmed
  }
  if (trimmed.startsWith('/')) {
    return `${getApiBaseUrl()}${trimmed}`
  }
  return trimmed
}

function extractInitials(name: string): string {
  const cleaned = name.trim()
  if (!cleaned) return '?'
  const parts = cleaned.split(/\s+/).filter(Boolean)
  if (parts.length >= 2) {
    return `${parts[0][0] ?? ''}${parts[parts.length - 1][0] ?? ''}`.toUpperCase()
  }
  return cleaned.slice(0, 2).toUpperCase()
}

export function UserAvatar({
  avatarUrl,
  name,
  size = 'sm',
  className,
}: UserAvatarProps) {
  const resolvedUrl = useMemo(() => resolveAvatarUrl(avatarUrl), [avatarUrl])
  const [hasError, setHasError] = useState(false)

  useEffect(() => {
    setHasError(false)
  }, [resolvedUrl])

  const initials = useMemo(() => extractInitials(name), [name])

  if (resolvedUrl && !hasError) {
    return (
      <img
        src={resolvedUrl}
        alt={name}
        loading="lazy"
        onError={() => setHasError(true)}
        className={cn(
          'shrink-0 rounded-full border border-slate-200 bg-slate-100 object-cover',
          SIZE_CLASSES[size],
          className,
        )}
      />
    )
  }

  return (
    <span
      role="img"
      aria-label={name}
      className={cn(
        'inline-flex shrink-0 items-center justify-center rounded-full border border-slate-200 bg-slate-100 font-medium text-slate-700 select-none',
        SIZE_CLASSES[size],
        className,
      )}
    >
      {initials}
    </span>
  )
}

import type { ReactNode } from 'react'
import { cn } from '../utils/cn'

// ─── Default icon ─────────────────────────────────────────────────────────────

function DefaultIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.25}
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-9 w-9 text-slate-700"
    >
      <path d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" />
    </svg>
  )
}

// ─── Component ────────────────────────────────────────────────────────────────

interface EmptyStateProps {
  title:        string
  description?: string
  /** Custom icon node. Defaults to inbox icon. */
  icon?:        ReactNode
  /** CTA button or link rendered below description. */
  action?:      ReactNode
  /**
   * `compact` — smaller padding, smaller text (fits inside a short panel).
   * Default `false` = generous vertical centering.
   */
  compact?:     boolean
  className?:   string
}

export function EmptyState({
  title,
  description,
  icon,
  action,
  compact   = false,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center text-center',
        compact ? 'gap-1.5 py-6' : 'gap-2 py-12',
        className,
      )}
    >
      <div className={cn(compact ? 'mb-0' : 'mb-1')}>
        {icon ?? <DefaultIcon />}
      </div>

      <p className={cn('font-medium text-slate-400', compact ? 'text-xs' : 'text-sm')}>
        {title}
      </p>

      {description && (
        <p className={cn('text-slate-600', compact ? 'max-w-[220px] text-[11px]' : 'max-w-xs text-xs')}>
          {description}
        </p>
      )}

      {action && (
        <div className={cn(compact ? 'mt-2' : 'mt-4')}>
          {action}
        </div>
      )}
    </div>
  )
}

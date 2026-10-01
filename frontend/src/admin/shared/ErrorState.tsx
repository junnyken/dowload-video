import { cn } from '../utils/cn'

// ─── Inline error icon ────────────────────────────────────────────────────────

function ErrorIcon({ compact }: { compact?: boolean }) {
  if (compact) {
    return (
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.75}
        strokeLinecap="round"
        className="h-4 w-4 flex-shrink-0 text-danger"
      >
        <path d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
      </svg>
    )
  }
  return (
    <div className="flex h-10 w-10 items-center justify-center rounded-full border border-danger/60 bg-danger-soft">
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.75}
        strokeLinecap="round"
        className="h-5 w-5 text-danger"
      >
        <path d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
      </svg>
    </div>
  )
}

// ─── Component ────────────────────────────────────────────────────────────────

interface ErrorStateProps {
  /** Bold heading line. Default "Something went wrong". */
  title?:     string
  /** Supporting detail / technical reason. */
  message?:   string
  onRetry?:   () => void
  /**
   * `compact` — horizontal inline layout (good for inside table cells or
   * small panels). Default `false` = centered vertical layout.
   */
  compact?:   boolean
  className?: string
}

export function ErrorState({
  title     = 'Something went wrong',
  message   = 'Failed to load data.',
  onRetry,
  compact   = false,
  className,
}: ErrorStateProps) {
  if (compact) {
    return (
      <div
        role="alert"
        className={cn(
          'flex items-center gap-2 rounded-lg border border-danger/50 bg-danger-soft px-3 py-2',
          className,
        )}
      >
        <ErrorIcon compact />
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium text-danger">{title}</p>
          {message && message !== title && (
            <p className="truncate text-[11px] text-danger">{message}</p>
          )}
        </div>
        {onRetry && (
          <button
            onClick={onRetry}
            className="flex-shrink-0 rounded border border-danger/60 px-2.5 py-1 text-[11px] font-medium text-danger transition-colors hover:border-danger/30 hover:bg-danger-soft"
          >
            Retry
          </button>
        )}
      </div>
    )
  }

  return (
    <div
      role="alert"
      className={cn(
        'flex flex-col items-center justify-center gap-3 py-10 text-center',
        className,
      )}
    >
      <ErrorIcon />
      <div>
        <p className="text-sm font-semibold text-danger">{title}</p>
        {message && message !== title && (
          <p className="mt-0.5 max-w-xs text-xs text-danger">{message}</p>
        )}
      </div>
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-1 rounded-lg border border-line px-4 py-1.5 text-xs font-medium text-fg-muted transition-colors hover:border-line-strong hover:text-fg-2"
        >
          Try again
        </button>
      )}
    </div>
  )
}

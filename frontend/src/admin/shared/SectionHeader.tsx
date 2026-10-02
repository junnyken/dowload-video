import type { ReactNode } from 'react'
import { cn } from '../utils/cn'

interface SectionHeaderProps {
  title:      string
  subtitle?:  string
  /** Right-side slot: action buttons, badges, etc. */
  right?:     ReactNode
  /** Stick to top of nearest scroll container (card or page). */
  sticky?:    boolean
  className?: string
}

export function SectionHeader({
  title,
  subtitle,
  right,
  sticky    = false,
  className,
}: SectionHeaderProps) {
  return (
    <div
      className={cn(
        'flex items-center justify-between gap-3 border-b border-line px-4 py-2.5',
        sticky && 'sticky top-0 z-10 bg-surface',
        className,
      )}
    >
      <div className="min-w-0">
        <h2 className="font-mono text-[10px] font-medium uppercase tracking-widest text-fg-muted truncate">
          {title}
        </h2>
        {subtitle && (
          <p className="mt-0.5 truncate text-[11px] leading-tight text-fg-muted">
            {subtitle}
          </p>
        )}
      </div>
      {right && (
        <div className="flex flex-shrink-0 items-center gap-2">
          {right}
        </div>
      )}
    </div>
  )
}

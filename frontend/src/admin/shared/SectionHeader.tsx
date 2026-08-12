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
        'flex items-center justify-between gap-3 border-b border-slate-800 px-4 py-2.5',
        sticky && 'sticky top-0 z-10 bg-slate-900/95 backdrop-blur-sm',
        className,
      )}
    >
      <div className="min-w-0">
        <h2 className="text-[10px] font-semibold uppercase tracking-widest text-slate-500 truncate">
          {title}
        </h2>
        {subtitle && (
          <p className="mt-0.5 truncate text-[11px] leading-tight text-slate-700">
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

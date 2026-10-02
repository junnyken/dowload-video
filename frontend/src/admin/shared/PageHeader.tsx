import type { ReactNode } from 'react'
import { cn } from '../utils/cn'

interface PageHeaderProps {
  title:        string
  description?: ReactNode
  /** Small inline content next to the title: counts, status pills. */
  badge?:       ReactNode
  /** Right-aligned controls: refresh, range pickers, primary action. */
  actions?:     ReactNode
  className?:   string
}

/**
 * Single page heading used by every admin page: same size, weight and
 * spacing, with a mono description line and an action slot that wraps under
 * the title on narrow screens.
 */
export function PageHeader({ title, description, badge, actions, className }: PageHeaderProps) {
  return (
    <div className={cn('flex flex-wrap items-start justify-between gap-x-4 gap-y-3', className)}>
      <div className="min-w-0">
        <div className="flex items-center gap-2.5">
          <h1 className="text-lg font-semibold tracking-tight text-fg">{title}</h1>
          {badge}
        </div>
        {description && <p className="mt-1 text-xs text-fg-2">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

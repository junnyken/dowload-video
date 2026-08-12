import type { ReactNode } from 'react'
import { cn } from '../utils/cn'

// ─── Component ────────────────────────────────────────────────────────────────
//
// Sits directly below an <h1> page title. Provides a secondary description
// line and an optional right-side meta slot (status pills, counts, badges).
//
// Usage:
//   <h1 className="text-base font-semibold text-slate-100">Platform Health</h1>
//   <PageSubheader
//     description="Real-time status across all 12 download platforms"
//     meta={<StatusPill status="healthy" dot />}
//   />

interface PageSubheaderProps {
  description: string
  /** Optional right-side content: status pills, breadcrumb, counts, etc. */
  meta?:       ReactNode
  className?:  string
}

export function PageSubheader({ description, meta, className }: PageSubheaderProps) {
  return (
    <div className={cn('flex items-center justify-between gap-3', className)}>
      <p className="text-xs text-slate-500">{description}</p>
      {meta && (
        <div className="flex flex-shrink-0 items-center gap-2">
          {meta}
        </div>
      )}
    </div>
  )
}

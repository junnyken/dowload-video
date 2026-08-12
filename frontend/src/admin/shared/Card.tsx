import type { ReactNode } from 'react'
import { cn } from '../utils/cn'

// ─── Exported types (used by panels) ──────────────────────────────────────────

export type CardPadding = 'none' | 'xs' | 'sm' | 'md' | 'lg'
export type CardAccent  = 'none' | 'green' | 'yellow' | 'red' | 'blue' | 'purple'

const PAD: Record<CardPadding, string> = {
  none: '',
  xs:   'p-2',
  sm:   'p-3',
  md:   'p-4',
  lg:   'p-5 lg:p-6',
}

const ACCENT: Record<CardAccent, string> = {
  none:   '',
  green:  'border-l-2 border-l-emerald-600',
  yellow: 'border-l-2 border-l-amber-500',
  red:    'border-l-2 border-l-red-600',
  blue:   'border-l-2 border-l-blue-600',
  purple: 'border-l-2 border-l-purple-600',
}

interface CardProps {
  children:   ReactNode
  className?: string
  /**
   * Inner padding. Default `'none'` — sectioned panels manage their own
   * horizontal spacing. Use `'sm'`/`'md'` for stat cards and content cards.
   */
  padding?:   CardPadding
  accent?:    CardAccent
  onClick?:   () => void
  as?:        'div' | 'section' | 'article'
  tabIndex?:  number
}

export function Card({
  children,
  className,
  padding  = 'none',
  accent   = 'none',
  onClick,
  as: As   = 'div',
  tabIndex,
}: CardProps) {
  return (
    <As
      className={cn(
        'rounded-2xl border border-slate-800 bg-slate-900/70',
        PAD[padding],
        ACCENT[accent],
        onClick && [
          'cursor-pointer transition-colors',
          'hover:border-slate-700 hover:bg-slate-800/60',
          'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-blue-700',
        ],
        className,
      )}
      onClick={onClick}
      tabIndex={tabIndex ?? (onClick ? 0 : undefined)}
      role={onClick ? 'button' : undefined}
    >
      {children}
    </As>
  )
}

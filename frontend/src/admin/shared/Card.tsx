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
  green:  'border-l-2 border-l-success',
  yellow: 'border-l-2 border-l-accent',
  red:    'border-l-2 border-l-danger',
  blue:   'border-l-2 border-l-line',
  purple: 'border-l-2 border-l-line',
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
        'rounded-2xl border border-line bg-surface-2',
        PAD[padding],
        ACCENT[accent],
        onClick && [
          'cursor-pointer transition-colors',
          'hover:border-line hover:bg-surface',
          'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-line-strong',
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

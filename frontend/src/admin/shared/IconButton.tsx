import type { ReactNode } from 'react'
import { cn } from '../utils/cn'

// ─── Types ─────────────────────────────────────────────────────────────────────

export type IconButtonVariant = 'default' | 'ghost' | 'danger' | 'success' | 'primary'
export type IconButtonSize    = 'xs' | 'sm' | 'md'

// ─── Style maps ───────────────────────────────────────────────────────────────

const VARIANT: Record<IconButtonVariant, string> = {
  default: [
    'border border-line bg-surface text-fg-2',
    'hover:border-line-strong hover:bg-surface-2 hover:text-fg',
  ].join(' '),
  ghost: [
    'border border-transparent bg-transparent text-fg-muted',
    'hover:bg-surface-2 hover:text-fg',
  ].join(' '),
  danger: [
    'border border-danger/30 bg-danger-soft text-danger',
    'hover:border-danger/60',
  ].join(' '),
  success: [
    'border border-success/30 bg-success-soft text-success',
    'hover:border-success/60',
  ].join(' '),
  primary: [
    'border border-accent bg-accent text-accent-fg',
    'hover:bg-accent-hover hover:border-accent-hover',
  ].join(' '),
}

const SIZE: Record<IconButtonSize, string> = {
  xs: 'h-6 w-6 rounded-md',
  sm: 'h-7 w-7 rounded-control',
  md: 'h-8 w-8 rounded-control',
}

const ICON_SIZE: Record<IconButtonSize, string> = {
  xs: 'h-3 w-3',
  sm: 'h-3.5 w-3.5',
  md: 'h-4 w-4',
}

// ─── Spinner ──────────────────────────────────────────────────────────────────

function Spinner({ className }: { className?: string }) {
  return (
    <svg className={cn('animate-spin', className)} viewBox="0 0 24 24" fill="none">
      <circle className="opacity-20" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
      <path className="opacity-80" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
    </svg>
  )
}

// ─── Component ────────────────────────────────────────────────────────────────

interface IconButtonProps {
  /** Accessible label — shown as native tooltip and used as aria-label. */
  label:       string
  icon:        ReactNode
  onClick?:    () => void
  variant?:    IconButtonVariant
  size?:       IconButtonSize
  disabled?:   boolean
  /** Shows a spinner in place of the icon. Implies disabled. */
  loading?:    boolean
  /** Applies a pressed/selected visual state. */
  active?:     boolean
  className?:  string
  type?:       'button' | 'submit' | 'reset'
}

export function IconButton({
  label,
  icon,
  onClick,
  variant   = 'default',
  size      = 'sm',
  disabled  = false,
  loading   = false,
  active    = false,
  className,
  type      = 'button',
}: IconButtonProps) {
  return (
    <button
      type={type}
      title={label}
      aria-label={label}
      aria-pressed={active || undefined}
      aria-busy={loading || undefined}
      onClick={onClick}
      disabled={disabled || loading}
      className={cn(
        'inline-flex items-center justify-center transition-colors',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
        'disabled:cursor-not-allowed disabled:opacity-40',
        SIZE[size],
        VARIANT[variant],
        active && 'ring-1 ring-inset ring-line-strong',
        className,
      )}
    >
      {loading ? (
        <Spinner className={ICON_SIZE[size]} />
      ) : (
        <span className={cn('flex items-center justify-center', ICON_SIZE[size])}>
          {icon}
        </span>
      )}
    </button>
  )
}

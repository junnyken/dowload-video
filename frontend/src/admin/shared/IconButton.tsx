import type { ReactNode } from 'react'
import { cn } from '../utils/cn'

// ─── Types ─────────────────────────────────────────────────────────────────────

export type IconButtonVariant = 'default' | 'ghost' | 'danger' | 'success' | 'primary'
export type IconButtonSize    = 'xs' | 'sm' | 'md'

// ─── Style maps ───────────────────────────────────────────────────────────────

const VARIANT: Record<IconButtonVariant, string> = {
  default: [
    'border border-slate-700 bg-slate-800/60 text-slate-400',
    'hover:border-slate-600 hover:bg-slate-700 hover:text-slate-200',
  ].join(' '),
  ghost: [
    'border border-transparent bg-transparent text-slate-500',
    'hover:border-slate-700 hover:bg-slate-800 hover:text-slate-300',
  ].join(' '),
  danger: [
    'border border-red-900/60 bg-red-950/40 text-red-500',
    'hover:border-red-800 hover:bg-red-950 hover:text-red-400',
  ].join(' '),
  success: [
    'border border-emerald-900/60 bg-emerald-950/40 text-emerald-500',
    'hover:border-emerald-800 hover:bg-emerald-950 hover:text-emerald-400',
  ].join(' '),
  primary: [
    'border border-blue-800 bg-blue-950/50 text-blue-400',
    'hover:border-blue-700 hover:bg-blue-950 hover:text-blue-300',
  ].join(' '),
}

const SIZE: Record<IconButtonSize, string> = {
  xs: 'h-6 w-6 rounded',
  sm: 'h-7 w-7 rounded-lg',
  md: 'h-8 w-8 rounded-lg',
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
        'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-blue-700',
        'disabled:cursor-not-allowed disabled:opacity-40',
        SIZE[size],
        VARIANT[variant],
        active && 'ring-1 ring-inset ring-blue-700/50',
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

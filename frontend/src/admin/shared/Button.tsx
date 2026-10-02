import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { cn } from '../utils/cn'

export type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost'
export type ButtonSize = 'sm' | 'md'

export const BUTTON_VARIANT: Record<ButtonVariant, string> = {
  primary:   'border border-accent bg-accent text-accent-fg hover:bg-accent-hover hover:border-accent-hover',
  secondary: 'border border-line bg-surface text-fg hover:border-line-strong hover:bg-surface-2',
  danger:    'border border-danger/30 bg-danger-soft text-danger hover:border-danger/60',
  ghost:     'border border-transparent bg-transparent text-fg-2 hover:bg-surface-2 hover:text-fg',
}

const SIZE: Record<ButtonSize, string> = {
  sm: 'h-8 px-3 text-xs',
  md: 'h-9 px-4 text-sm',
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?:    ButtonSize
  children: ReactNode
}

/** primary = accent, secondary = surface + line, danger = danger-soft. */
export function Button({ variant = 'secondary', size = 'sm', className, type = 'button', children, ...rest }: ButtonProps) {
  return (
    <button
      type={type}
      className={cn(
        'inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-control font-medium transition-colors',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
        'disabled:cursor-not-allowed disabled:opacity-50',
        SIZE[size],
        BUTTON_VARIANT[variant],
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  )
}

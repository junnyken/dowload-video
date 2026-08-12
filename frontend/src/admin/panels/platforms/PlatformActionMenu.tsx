import { useEffect, useRef, useState } from 'react'
import { cn } from '../../utils/cn'
import type { PlatformHealthRow } from './platform.types'

type PlatformAction =
  | 'reset_circuit'
  | 'trip_circuit'
  | 'force_disable'
  | 'force_enable'
  | 'test_connection'
  | 'view_jobs'

interface MenuItemProps {
  label: string
  description?: string
  icon: string   // SVG path d= value
  onClick: () => void
  destructive?: boolean
  disabled?: boolean
}

function MenuItem({ label, description, icon, onClick, destructive, disabled }: MenuItemProps) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'flex w-full items-start gap-3 px-3 py-2 text-left text-xs transition-colors',
        disabled
          ? 'cursor-not-allowed opacity-40'
          : destructive
            ? 'text-red-400 hover:bg-red-950/40'
            : 'text-slate-300 hover:bg-slate-800',
      )}
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.75}
        strokeLinecap="round"
        strokeLinejoin="round"
        className="mt-0.5 h-3.5 w-3.5 flex-shrink-0"
      >
        <path d={icon} />
      </svg>
      <div>
        <p className="font-medium">{label}</p>
        {description && (
          <p className={cn('text-[10px]', destructive ? 'text-red-600' : 'text-slate-600')}>
            {description}
          </p>
        )}
      </div>
    </button>
  )
}

function Divider() {
  return <div className="my-1 border-t border-slate-800" />
}

interface PlatformActionMenuProps {
  row: PlatformHealthRow
  onAction?: (platform: string, action: PlatformAction) => void
  onViewJobs?: (platform: string) => void
}

export function PlatformActionMenu({ row, onAction, onViewJobs }: PlatformActionMenuProps) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function onOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    function onEsc(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onOutside)
    document.addEventListener('keydown', onEsc)
    return () => {
      document.removeEventListener('mousedown', onOutside)
      document.removeEventListener('keydown', onEsc)
    }
  }, [open])

  function handle(action: PlatformAction) {
    setOpen(false)
    onAction?.(row.platform, action)
    if (action === 'view_jobs') onViewJobs?.(row.platform)
  }

  const isOpen       = row.circuitState === 'open'
  const isHalf       = row.circuitState === 'half'
  const isDisabled   = row.status === 'disabled'
  const isExempt     = row.circuitState === 'exempt'

  return (
    <div ref={ref} className="relative flex justify-center">
      <button
        onClick={e => { e.stopPropagation(); setOpen(o => !o) }}
        className={cn(
          'flex h-6 w-6 items-center justify-center rounded text-slate-600 transition-colors hover:bg-slate-700 hover:text-slate-300',
          open && 'bg-slate-700 text-slate-300',
        )}
        aria-label={`Actions for ${row.platform}`}
      >
        <svg viewBox="0 0 24 24" fill="currentColor" className="h-3.5 w-3.5">
          <circle cx="12" cy="5"  r="1.5" />
          <circle cx="12" cy="12" r="1.5" />
          <circle cx="12" cy="19" r="1.5" />
        </svg>
      </button>

      {open && (
        <div
          className="absolute right-0 top-7 z-50 w-52 rounded-xl border border-slate-800 bg-slate-900 py-1 shadow-2xl"
          onClick={e => e.stopPropagation()}
        >
          {/* Circuit actions */}
          {!isExempt && (
            <>
              {(isOpen || isHalf) && (
                <MenuItem
                  label="Reset Circuit Breaker"
                  description="Force-close → allow traffic"
                  icon="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                  onClick={() => handle('reset_circuit')}
                />
              )}
              {!isOpen && !isDisabled && (
                <MenuItem
                  label="Trip Circuit Breaker"
                  description="Force-open → block traffic"
                  icon="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636"
                  onClick={() => handle('trip_circuit')}
                  destructive
                />
              )}
              <Divider />
            </>
          )}

          {/* Enable/Disable */}
          {isDisabled ? (
            <MenuItem
              label="Enable Platform"
              icon="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"
              onClick={() => handle('force_enable')}
            />
          ) : (
            <MenuItem
              label="Disable Platform"
              description="Stop accepting new jobs"
              icon="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z"
              onClick={() => handle('force_disable')}
              destructive
            />
          )}

          <Divider />

          <MenuItem
            label="Test Connection"
            description="Run a probe job"
            icon="M13 10V3L4 14h7v7l9-11h-7z"
            onClick={() => handle('test_connection')}
          />
          <MenuItem
            label="View All Jobs →"
            description={`Filter jobs by ${row.platform}`}
            icon="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"
            onClick={() => handle('view_jobs')}
          />
        </div>
      )}
    </div>
  )
}

import { cn } from '../../utils/cn'
import type { PlatformFilter, PlatformStatus } from './platform.types'
import { DEFAULT_FILTER } from './platform.types'

const STATUS_OPTIONS: Array<{ value: PlatformFilter['status']; label: string }> = [
  { value: 'all',      label: 'All' },
  { value: 'healthy',  label: 'Healthy' },
  { value: 'warning',  label: 'Warning' },
  { value: 'critical', label: 'Critical' },
  { value: 'disabled', label: 'Disabled' },
]

const STATUS_ACTIVE_STYLES: Record<PlatformStatus | 'all', string> = {
  all:      'border-slate-600 bg-slate-700 text-slate-200',
  healthy:  'border-emerald-900 bg-emerald-950 text-emerald-400',
  warning:  'border-amber-900  bg-amber-950  text-amber-400',
  critical: 'border-red-900    bg-red-950    text-red-400',
  disabled: 'border-slate-700  bg-slate-800  text-slate-400',
}

function ToggleChip({
  active,
  label,
  onClick,
}: {
  active: boolean
  label: string
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'flex items-center gap-1 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors',
        active
          ? 'border-blue-900 bg-blue-950 text-blue-300'
          : 'border-slate-800 text-slate-500 hover:border-slate-700 hover:text-slate-400',
      )}
    >
      {active ? (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5} className="h-3 w-3 flex-shrink-0">
          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
        </svg>
      ) : (
        <span className="h-3 w-3 flex-shrink-0" />
      )}
      {label}
    </button>
  )
}

interface PlatformFiltersProps {
  value: PlatformFilter
  onChange: (filter: PlatformFilter) => void
  totalCount: number
  filteredCount: number
}

function hasActiveFilters(f: PlatformFilter): boolean {
  return (
    f.search !== '' ||
    f.status !== 'all' ||
    f.cookieRequired !== null ||
    f.proxyRequired !== null
  )
}

export function PlatformFilters({
  value,
  onChange,
  totalCount,
  filteredCount,
}: PlatformFiltersProps) {
  const dirty = hasActiveFilters(value)

  return (
    <div className="flex flex-col gap-3 border-b border-slate-800/60 pb-3">
      {/* Row 1: Search + result count */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={1.75}
            className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-600"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            type="text"
            value={value.search}
            onChange={e => onChange({ ...value, search: e.target.value })}
            placeholder="Search platforms…"
            className="w-full rounded-lg border border-slate-800 bg-slate-800/50 py-1.5 pl-8 pr-3 text-xs text-slate-200 placeholder-slate-600 outline-none transition-colors focus:border-slate-600 focus:bg-slate-800"
          />
          {value.search && (
            <button
              onClick={() => onChange({ ...value, search: '' })}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-600 hover:text-slate-400"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="h-3 w-3">
                <path strokeLinecap="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>

        <span className="flex-shrink-0 font-mono text-[10px] text-slate-600">
          {dirty ? `${filteredCount} / ${totalCount}` : `${totalCount} platforms`}
        </span>

        {dirty && (
          <button
            onClick={() => onChange(DEFAULT_FILTER)}
            className="flex-shrink-0 rounded-md border border-slate-800 px-2 py-1 text-[10px] text-slate-500 transition-colors hover:border-slate-700 hover:text-slate-300"
          >
            Clear
          </button>
        )}
      </div>

      {/* Row 2: Status chips + toggle chips */}
      <div className="flex flex-wrap items-center gap-2">
        {/* Status filter chips */}
        {STATUS_OPTIONS.map(opt => (
          <button
            key={opt.value}
            onClick={() => onChange({ ...value, status: opt.value })}
            className={cn(
              'rounded-md border px-2.5 py-1 font-mono text-[10px] font-semibold uppercase tracking-wide transition-colors',
              value.status === opt.value
                ? STATUS_ACTIVE_STYLES[opt.value]
                : 'border-slate-800 text-slate-600 hover:border-slate-700 hover:text-slate-400',
            )}
          >
            {opt.label}
          </button>
        ))}

        <div className="mx-1 h-4 w-px bg-slate-800" />

        <ToggleChip
          label="Cookie Required"
          active={value.cookieRequired === true}
          onClick={() =>
            onChange({ ...value, cookieRequired: value.cookieRequired === true ? null : true })
          }
        />
        <ToggleChip
          label="Proxy Required"
          active={value.proxyRequired === true}
          onClick={() =>
            onChange({ ...value, proxyRequired: value.proxyRequired === true ? null : true })
          }
        />
      </div>
    </div>
  )
}

import { useRef } from 'react'
import { cn } from '../../utils/cn'

// ─── Quick-search hint chips ───────────────────────────────────────────────────

const HINTS = [
  { label: 'Job ID',   example: 'job_yt_3f8a2b'          },
  { label: 'Batch',    example: 'batch_morning_run_01'    },
  { label: 'URL',      example: 'youtube.com/watch?v=...' },
  { label: 'Platform', example: 'youtube'                 },
  { label: 'IP',       example: '103.138.88.100'          },
]

interface JobSearchBarProps {
  value:    string
  onChange: (v: string) => void
  onSearch: (v: string) => void
  loading?: boolean
}

export function JobSearchBar({ value, onChange, onSearch, loading }: JobSearchBarProps) {
  const inputRef = useRef<HTMLInputElement>(null)

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter')  onSearch(value)
    if (e.key === 'Escape') { onChange(''); onSearch(''); inputRef.current?.blur() }
  }

  function clear() {
    onChange('')
    onSearch('')
    inputRef.current?.focus()
  }

  return (
    <div className="flex flex-col gap-2 border-b border-slate-800 px-4 py-3">
      {/* Input row */}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-600"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-4.35-4.35M16.65 10a6.65 6.65 0 11-13.3 0 6.65 6.65 0 0113.3 0z" />
          </svg>

          <input
            ref={inputRef}
            type="text"
            value={value}
            onChange={e => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search by job ID, batch ID, URL, platform, user IP…"
            spellCheck={false}
            className={cn(
              'w-full rounded-lg border border-slate-800 bg-slate-950 py-2 pl-8 text-xs text-slate-300',
              'placeholder-slate-700 outline-none transition-colors focus:border-blue-700',
              value ? 'pr-7' : 'pr-3',
            )}
          />

          {value && (
            <button
              onClick={clear}
              aria-label="Clear"
              className="absolute right-2 top-1/2 -translate-y-1/2 flex h-4 w-4 items-center justify-center rounded text-slate-600 hover:text-slate-400"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5} className="h-3 w-3">
                <path strokeLinecap="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>

        {/* Search button */}
        <button
          onClick={() => onSearch(value)}
          disabled={!value.trim() || loading}
          className={cn(
            'flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-semibold transition-colors',
            'border-blue-800 bg-blue-950/50 text-blue-400 hover:bg-blue-950 hover:text-blue-300',
            'disabled:cursor-not-allowed disabled:opacity-40',
          )}
        >
          {loading ? (
            <svg className="h-3 w-3 animate-spin" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="h-3.5 w-3.5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-4.35-4.35M16.65 10a6.65 6.65 0 11-13.3 0 6.65 6.65 0 0113.3 0z" />
            </svg>
          )}
          Inspect
        </button>
      </div>

      {/* Hint chips */}
      <div className="flex flex-wrap gap-1.5">
        {HINTS.map(h => (
          <button
            key={h.label}
            onClick={() => { onChange(h.example); onSearch(h.example) }}
            className="flex items-center gap-1 rounded border border-slate-800 bg-slate-950/50 px-2 py-0.5 transition-colors hover:border-slate-700 hover:bg-slate-900"
          >
            <span className="text-[9px] font-semibold uppercase tracking-widest text-slate-700">{h.label}</span>
            <span className="font-mono text-[10px] text-slate-600">{h.example}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

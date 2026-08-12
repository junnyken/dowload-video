import { useState, useMemo } from 'react'
import { cn } from '../../utils/cn'
import { SectionHeader } from '../../shared/SectionHeader'
import { Card } from '../../shared/Card'
import type { CookieItem, CookiePoolSummary, CookieAction, AddCookieFormData } from './cookie.types'
import { computeSummary } from './cookie.mock'
import { CookieTable } from './CookieTable'
import { AddCookieModal } from './AddCookieModal'

// ─── Summary stat card ─────────────────────────────────────────────────────────

interface SummaryCardProps {
  label: string
  value: number
  total?: number
  tone: 'neutral' | 'green' | 'amber' | 'red' | 'blue'
  iconPath: string
}

const TONE_CLASSES: Record<SummaryCardProps['tone'], { text: string; icon: string }> = {
  neutral: { text: 'text-slate-300',  icon: 'text-slate-500'  },
  green:   { text: 'text-emerald-400', icon: 'text-emerald-600' },
  amber:   { text: 'text-amber-400',   icon: 'text-amber-600'   },
  red:     { text: 'text-red-400',     icon: 'text-red-700'     },
  blue:    { text: 'text-blue-400',    icon: 'text-blue-600'    },
}

function SummaryCard({ label, value, total, tone, iconPath }: SummaryCardProps) {
  const t = TONE_CLASSES[tone]
  return (
    <div className="flex items-center gap-3 rounded-xl border border-slate-800 bg-slate-900 px-4 py-3">
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.75}
        strokeLinecap="round"
        strokeLinejoin="round"
        className={cn('h-4 w-4 flex-shrink-0', t.icon)}
      >
        <path d={iconPath} />
      </svg>
      <div className="min-w-0">
        <div className="flex items-baseline gap-1">
          <span className={cn('text-xl font-bold tabular-nums leading-none', t.text)}>{value}</span>
          {total !== undefined && (
            <span className="text-xs text-slate-600">/ {total}</span>
          )}
        </div>
        <p className="mt-0.5 text-[10px] font-medium uppercase tracking-widest text-slate-600">{label}</p>
      </div>
    </div>
  )
}

// ─── Platform filter chips ─────────────────────────────────────────────────────

const PLATFORMS_ALL = [
  'youtube', 'instagram', 'tiktok', 'facebook', 'twitter',
  'bilibili', 'douyin', 'soundcloud', 'pinterest', 'reddit', 'vimeo', 'threads',
]

interface PlatformChipsProps {
  selected: string
  onChange: (v: string) => void
  cookiesByPlatform: Record<string, number>
}

function PlatformChips({ selected, onChange, cookiesByPlatform }: PlatformChipsProps) {
  const platforms = PLATFORMS_ALL.filter(p => (cookiesByPlatform[p] ?? 0) > 0)
  return (
    <div className="flex flex-wrap gap-1.5">
      <button
        onClick={() => onChange('all')}
        className={cn(
          'rounded-full border px-3 py-0.5 text-[11px] font-medium transition-colors',
          selected === 'all'
            ? 'border-blue-700 bg-blue-950 text-blue-300'
            : 'border-slate-700 text-slate-500 hover:border-slate-600 hover:text-slate-300',
        )}
      >
        All
      </button>
      {platforms.map(p => (
        <button
          key={p}
          onClick={() => onChange(p)}
          className={cn(
            'rounded-full border px-3 py-0.5 text-[11px] font-medium capitalize transition-colors',
            selected === p
              ? 'border-blue-700 bg-blue-950 text-blue-300'
              : 'border-slate-700 text-slate-500 hover:border-slate-600 hover:text-slate-300',
          )}
        >
          {p}
          <span className="ml-1 text-[10px] text-slate-700">{cookiesByPlatform[p]}</span>
        </button>
      ))}
    </div>
  )
}

// ─── Main panel ────────────────────────────────────────────────────────────────

interface CookiePoolPanelProps {
  cookies: CookieItem[]
  loading?: boolean
  error?: string
  onRetry?: () => void
  onAction?: (id: string, action: CookieAction) => void
  onAdd?: (data: AddCookieFormData) => void
}

export function CookiePoolPanel({
  cookies,
  loading,
  error,
  onRetry,
  onAction,
  onAdd,
}: CookiePoolPanelProps) {
  const [search, setSearch]     = useState('')
  const [platform, setPlatform] = useState('all')
  const [modalOpen, setModalOpen] = useState(false)

  const summary: CookiePoolSummary = useMemo(
    () => computeSummary(cookies),
    [cookies],
  )

  const cookiesByPlatform = useMemo(
    () =>
      cookies.reduce<Record<string, number>>((acc, c) => {
        acc[c.platform] = (acc[c.platform] ?? 0) + 1
        return acc
      }, {}),
    [cookies],
  )

  const filtered = useMemo(() => {
    let list = cookies
    if (platform !== 'all') list = list.filter(c => c.platform === platform)
    if (search.trim()) {
      const q = search.toLowerCase()
      list = list.filter(
        c =>
          c.accountLabel.toLowerCase().includes(q) ||
          c.platform.toLowerCase().includes(q) ||
          c.status.toLowerCase().includes(q),
      )
    }
    return list
  }, [cookies, platform, search])

  function handleAction(id: string, action: CookieAction) {
    onAction?.(id, action)
  }

  function handleSave(data: AddCookieFormData) {
    setModalOpen(false)
    onAdd?.(data)
  }

  function handleTest(data: AddCookieFormData) {
    console.log('[CookiePool] test cookie', data) // Phase 2: probe API
  }

  return (
    <>
      <Card className="overflow-hidden">
        {/* ── Header ── */}
        <SectionHeader
          title="Cookie Pool"
          subtitle="Session cookies for platform authentication"
          right={
            <button
              onClick={() => setModalOpen(true)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-blue-500"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="h-3.5 w-3.5">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
              </svg>
              Add Cookie
            </button>
          }
        />

        {/* ── Summary stats ── */}
        <div className="grid grid-cols-2 gap-2 border-b border-slate-800 px-4 py-3 sm:grid-cols-5">
          <SummaryCard
            label="Total"
            value={summary.total}
            tone="neutral"
            iconPath="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4"
          />
          <SummaryCard
            label="Active"
            value={summary.active}
            total={summary.total}
            tone="green"
            iconPath="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"
          />
          <SummaryCard
            label="Cooldown"
            value={summary.cooldown}
            tone={summary.cooldown > 0 ? 'amber' : 'neutral'}
            iconPath="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"
          />
          <SummaryCard
            label="Disabled"
            value={summary.disabled}
            tone={summary.disabled > 0 ? 'red' : 'neutral'}
            iconPath="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636"
          />
          <SummaryCard
            label="Expiring Soon"
            value={summary.expiringSoon}
            tone={summary.expiringSoon > 0 ? 'amber' : 'neutral'}
            iconPath="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
          />
        </div>

        {/* ── Toolbar: search + platform chips ── */}
        <div className="flex flex-col gap-2.5 border-b border-slate-800 px-4 py-3">
          {/* Search */}
          <div className="relative max-w-xs">
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
              type="search"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search label, platform, status…"
              className="w-full rounded-lg border border-slate-800 bg-slate-950 py-1.5 pl-8 pr-3 text-xs text-slate-300 placeholder-slate-700 outline-none transition-colors focus:border-blue-700"
            />
          </div>

          {/* Platform filter chips */}
          <PlatformChips
            selected={platform}
            onChange={setPlatform}
            cookiesByPlatform={cookiesByPlatform}
          />

          {/* Result count */}
          {(search || platform !== 'all') && (
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-slate-600">
                {filtered.length} of {cookies.length} cookies
              </span>
              <button
                onClick={() => { setSearch(''); setPlatform('all') }}
                className="text-[11px] text-blue-500 hover:text-blue-400"
              >
                Clear filters
              </button>
            </div>
          )}
        </div>

        {/* ── Table ── */}
        <CookieTable
          cookies={filtered}
          loading={loading}
          error={error}
          onRetry={onRetry}
          onAction={handleAction}
          onAddCookie={() => setModalOpen(true)}
        />
      </Card>

      {/* ── Add Cookie Modal (portalled to panel, z-50) ── */}
      <AddCookieModal
        isOpen={modalOpen}
        onClose={() => setModalOpen(false)}
        onSave={handleSave}
        onTest={handleTest}
      />
    </>
  )
}

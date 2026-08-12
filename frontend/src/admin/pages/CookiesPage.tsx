import { useState, useEffect } from 'react'
import { CookiePoolPanel } from '../panels/cookies/CookiePoolPanel'
import {
  useAdminCookieList,
  useAdminCookieStatus,
  useDeleteCookie,
  useAddCookie,
  useTriggerCookieHealthCheck,
} from '../hooks/useAdminCookiePool'
import type { CookieItem, CookieAction, AddCookieFormData } from '../panels/cookies/cookie.types'
import type { ExpiryCookieEntry } from '../api/cookies'

// ─── Platform display helpers ──────────────────────────────────────────────────

const PLATFORM_ICONS: Record<string, string> = {
  youtube:    'YT',
  tiktok:     'TK',
  facebook:   'FB',
  instagram:  'IG',
  twitter:    'TW',
  x:          'X',
  reddit:     'RD',
  bilibili:   'BL',
  threads:    'TH',
  soundcloud: 'SC',
  spotify:    'SP',
}

const DEFAULT_PLATFORMS = ['youtube', 'tiktok', 'facebook', 'instagram']

function platformLabel(p: string): string {
  const map: Record<string, string> = {
    youtube: 'YouTube', tiktok: 'TikTok', facebook: 'Facebook',
    instagram: 'Instagram', twitter: 'Twitter/X', x: 'X',
    reddit: 'Reddit', bilibili: 'Bilibili', threads: 'Threads',
    soundcloud: 'SoundCloud', spotify: 'Spotify',
  }
  return map[p] ?? p.charAt(0).toUpperCase() + p.slice(1)
}

// ─── Mapping helpers ───────────────────────────────────────────────────────────

function formatRelativeTime(unixSec: number): string {
  const diffSec = Math.floor(Date.now() / 1000) - unixSec
  if (diffSec < 60) return `${diffSec}s ago`
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`
  return `${Math.floor(diffSec / 86400)}d ago`
}

function mapExpiryCookieToItem(entry: ExpiryCookieEntry, platform: string): CookieItem {
  let status: CookieItem['status'] = 'active'
  if (entry.health_status === 'hard') {
    status = 'hard_blocked'
  } else if (entry.health_status === 'soft' || entry.health_status === 'blocked') {
    status = 'soft_blocked'
  } else if (entry.expiry_status === 'expired') {
    status = 'expired'
  } else if (entry.cooldown_ttl_s != null && entry.cooldown_ttl_s > 0) {
    status = 'soft_blocked'
  }

  let expiryEstimate = 'Unknown'
  if (entry.expiry_status === 'expired') {
    expiryEstimate = 'Expired'
  } else if (entry.expiry_status === 'session_only') {
    expiryEstimate = 'Session only'
  } else if (entry.days_left != null) {
    const d = entry.days_left
    if (d === 0) expiryEstimate = 'Today'
    else if (d === 1) expiryEstimate = '~1 day'
    else if (d < 7) expiryEstimate = `~${d} days`
    else if (d < 60) expiryEstimate = `~${Math.round(d / 7)} weeks`
    else expiryEstimate = `~${Math.round(d / 30)} months`
  }

  let healthScore = 80
  if (entry.health_status === 'hard') healthScore = 10
  else if (entry.health_status === 'soft' || entry.health_status === 'blocked') healthScore = 40
  else if (entry.expiry_status === 'expired') healthScore = 0
  else if (entry.expiry_status === 'critical') healthScore = 30
  else if (entry.expiry_status === 'expiring_soon') healthScore = 60
  else if (entry.expiry_status === 'healthy') healthScore = 90

  const label = entry.label || entry.account_hint || `${platform}#${entry.index}`

  return {
    id: `${platform}-${entry.hash}-${entry.index}`,
    platform,
    accountLabel: label,
    status,
    healthScore,
    lastSuccessAt: entry.last_used > 0 ? formatRelativeTime(entry.last_used) : 'never',
    lastFailAt: null,
    failCount: 0,
    cooldownRemainingSec: entry.cooldown_ttl_s ?? 0,
    expiryEstimate,
  }
}

// ─── Page component ────────────────────────────────────────────────────────────

export function CookiesPage() {
  const [activePlatform, setActivePlatform] = useState('youtube')
  const [localOverrides, setLocalOverrides] = useState<Record<string, Partial<CookieItem>>>({})

  // Fetch all platform statuses to build dynamic tabs
  const { data: statusData } = useAdminCookieStatus()

  // Build tab list: platforms that have cookies + always show DEFAULT_PLATFORMS
  const availablePlatforms: string[] = (() => {
    const fromStatus = statusData?.pools ? Object.keys(statusData.pools).filter(p => (statusData.pools[p]?.total ?? 0) > 0) : []
    const merged = Array.from(new Set([...DEFAULT_PLATFORMS, ...fromStatus]))
    return merged
  })()

  // If the saved activePlatform has cookies but wasn't in DEFAULT_PLATFORMS, it'll appear after load
  useEffect(() => {
    if (statusData?.pools && !availablePlatforms.includes(activePlatform)) {
      setActivePlatform('youtube')
    }
  }, [statusData])

  const { data, isLoading, error, refetch } = useAdminCookieList(activePlatform)
  const deleteMut = useDeleteCookie(activePlatform)
  const addMut    = useAddCookie()
  const testMut   = useTriggerCookieHealthCheck(activePlatform)

  const rawCookies: CookieItem[] = (data?.cookies ?? []).map(e =>
    mapExpiryCookieToItem(e, activePlatform),
  )

  const cookies: CookieItem[] = rawCookies.map(c => ({
    ...c,
    ...(localOverrides[c.id] ?? {}),
  }))

  async function handleAction(id: string, action: CookieAction) {
    const item = cookies.find(c => c.id === id)
    if (!item) return

    const parts = id.split('-')
    const index = parseInt(parts[parts.length - 1], 10)

    switch (action) {
      case 'delete':
        deleteMut.mutate(index)
        break
      case 'test':
        testMut.mutate()
        break
      case 'disable':
        setLocalOverrides(prev => ({ ...prev, [id]: { status: 'disabled' as const } }))
        break
      case 'enable':
        setLocalOverrides(prev => ({ ...prev, [id]: { status: 'active' as const } }))
        break
      case 'reset_soft':
      case 'reset_hard':
        setLocalOverrides(prev => ({
          ...prev,
          [id]: { status: 'active' as const, cooldownRemainingSec: 0, failCount: 0 },
        }))
        break
      default:
        break
    }
  }

  async function handleAdd(formData: AddCookieFormData) {
    const target = formData.platform || activePlatform
    addMut.mutate(
      { platform: target, rawCookie: formData.rawCookie, label: formData.accountLabel },
      {
        onSuccess: () => {
          setActivePlatform(target)
        },
      },
    )
  }

  const errorMsg = error instanceof Error ? error.message : error ? 'Failed to fetch cookies' : undefined

  const summary = {
    active:       cookies.filter(c => c.status === 'active').length,
    cooldown:     cookies.filter(c => c.status === 'soft_blocked' || c.status === 'hard_blocked').length,
    disabled:     cookies.filter(c => c.status === 'disabled' || c.status === 'expired').length,
    expiringSoon: cookies.filter(c => c.expiryEstimate.includes('days') || c.expiryEstimate.includes('week')).length,
  }

  // Pool counts per tab from status data
  const poolCounts = statusData?.pools ?? {}

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-base font-semibold text-slate-100">Cookie Pool</h1>
        <p className="mt-0.5 text-xs text-slate-500">
          {summary.active} active · {summary.cooldown} in cooldown · {summary.disabled} disabled
          {summary.expiringSoon > 0 && (
            <span className="ml-2 font-medium text-amber-500">
              {summary.expiringSoon} expiring soon
            </span>
          )}
        </p>
      </div>

      {/* Dynamic platform tabs */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs text-slate-500">Platform:</span>
        <div className="flex flex-wrap gap-1.5">
          {availablePlatforms.map(p => {
            const count = poolCounts[p]?.total ?? 0
            const isActive = activePlatform === p
            return (
              <button
                key={p}
                onClick={() => { setActivePlatform(p); setLocalOverrides({}) }}
                className={[
                  'inline-flex items-center gap-1 rounded-full border px-3 py-0.5 text-[11px] font-medium transition-colors',
                  isActive
                    ? 'border-blue-700 bg-blue-950 text-blue-300'
                    : 'border-slate-700 text-slate-500 hover:border-slate-600 hover:text-slate-300',
                ].join(' ')}
              >
                <span className="font-mono text-[9px] opacity-60">{PLATFORM_ICONS[p] ?? p.slice(0,2).toUpperCase()}</span>
                {platformLabel(p)}
                {count > 0 && (
                  <span className={[
                    'ml-0.5 rounded-full px-1 py-0 font-mono text-[9px]',
                    isActive ? 'bg-blue-900 text-blue-300' : 'bg-slate-800 text-slate-400',
                  ].join(' ')}>
                    {count}
                  </span>
                )}
              </button>
            )
          })}
        </div>
      </div>

      <CookiePoolPanel
        cookies={cookies}
        loading={isLoading}
        error={errorMsg}
        onRetry={() => refetch()}
        onAction={handleAction}
        onAdd={handleAdd}
      />
    </div>
  )
}

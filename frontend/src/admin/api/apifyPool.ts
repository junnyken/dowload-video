import { getAdminToken } from '../hooks/useAdminAuth'
import { API_BASE } from '../../lib/apiBase'

// Apify token pool + cost metrics (backend: app/api/admin_china_platforms.py).
// The backend never returns a token: only id, label and the last 4 characters.

export type PoolState = 'active' | 'exhausted' | 'invalid' | 'disabled' | 'cooldown'

export interface PoolEntry {
  id: string
  label: string
  last4: string | null
  source: 'admin' | 'env'
  legacy_slot: boolean
  priority: number
  enabled: boolean
  monthly_ceiling_usd: number | null
  state: PoolState
  state_reason: string | null
  state_since: string | null
  cooldown_until: string | null
  exhausted_until: string | null
  eligible: boolean
  ineligible_reason: string | null
  account: { username?: string | null; plan?: string | null; actors_enabled?: boolean } | null
  usage: {
    monthly_usage_usd?: number | null
    max_monthly_usage_usd?: number | null
    cycle_start?: string | null
    cycle_end?: string | null
  } | null
  refreshed_at: string | null
  last_used_at: string | null
  spend_today_usd: number
  spend_month_usd: number
  spend_7d_usd: number
  calls_today: number
  apify_usage_now_usd: number | null
  apify_limit_usd: number | null
  remaining_usd: number | null
  burn_per_day_usd: number
  projected_days_left: number | null
}

export interface PoolSummary {
  entries: number
  eligible: number
  by_state: Record<PoolState, number>
  remaining_usd: number | null
  capacity_usd: number | null
  remaining_pct: number | null
  spend_today_usd: number
  spend_month_usd: number
  burn_per_day_usd: number
  projected_days_left: number | null
  low_pct_threshold: number
}

export interface PoolPayload {
  entries: PoolEntry[]
  summary: PoolSummary
  env_fallback_configured: boolean
}

export interface PeriodMetrics {
  managed_calls: number
  managed_success: number
  success_rate: number | null
  usable_known: number
  usable_rate: number | null
  cache_hits: number
  cache_hits_managed: number
  saved_usd: number
  spend_usd: number
  cost_per_success_usd: number | null
}

export type Period = 'today' | '7d' | 'month'
export type ChinaPlatform = 'douyin' | 'kuaishou' | 'xiaohongshu'

export interface MetricsPayload {
  day_utc: string
  month_utc: string
  platforms: Record<ChinaPlatform, Record<Period, PeriodMetrics>>
  totals: Record<Period, PeriodMetrics>
  entries: PoolEntry[]
  pool: PoolSummary
  estimated_cost_per_call_usd: Record<ChinaPlatform, number>
  cache: { ttl_sec: number; extended_ttl_sec: number; expiry_margin_sec: number }
}

const BASE = '/china-platforms/apify'

async function call<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getAdminToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(`${API_BASE}/api/v1/admin${BASE}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    let msg = `Lỗi HTTP ${res.status}`
    try {
      const data = await res.json()
      const d = data?.detail
      msg = typeof d === 'string' ? d : d?.message ?? msg
    } catch { /* keep the status text */ }
    throw new Error(msg)
  }
  return res.json() as Promise<T>
}

export const apifyPoolApi = {
  list: () => call<PoolPayload>('/pool'),
  metrics: () => call<MetricsPayload>('/metrics'),
  add: (body: { token: string; label: string; priority?: number; monthly_ceiling_usd?: number | null; reason: string }) =>
    call<PoolPayload & { entry: PoolEntry }>('/pool', 'POST', body),
  update: (id: string, body: Partial<Pick<PoolEntry, 'label' | 'priority' | 'monthly_ceiling_usd' | 'enabled'>> & { reason: string }) =>
    call<PoolPayload & { entry: PoolEntry }>(`/pool/${encodeURIComponent(id)}`, 'POST', body),
  remove: (id: string, reason: string) =>
    call<PoolPayload>(`/pool/${encodeURIComponent(id)}`, 'DELETE', { reason }),
  refresh: (id: string) => call<{ outcome: string; entry: PoolEntry }>(`/pool/${encodeURIComponent(id)}/refresh`, 'POST'),
  refreshAll: () => call<PoolPayload & { results: Record<string, string> }>('/pool/refresh', 'POST'),
}

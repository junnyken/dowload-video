import { getAdminToken } from '../hooks/useAdminAuth'
import { API_BASE } from '../../lib/apiBase'

// "Đo lường" (Phase 34C, task #6334). Backend: app/api/admin_metrics.py +
// app/services/unit_metrics.py. A figure the current data cannot give comes
// back as null / available=false — the page shows "chưa đo được", never 0.

export type MetricsDays = 7 | 30

export interface SuccessCell {
  attempts: number
  successes: number
  success_rate: number | null
  /** null = time to result not measured (no histogram); 0 = measured, no success. */
  latency_samples: number | null
  p50_ms: number | null
  p50_label: string | null
  p95_ms: number | null
  p95_label: string | null
}

export type SuccessDay = { date: string; available: false } | ({ date: string; available: true } & SuccessCell)

export interface SuccessPayload {
  days: number
  day_basis: string
  dates: string[]
  platforms: { platform: string; totals: SuccessCell; per_day: SuccessDay[] }[]
  totals: SuccessCell
  measurable: { attempts: boolean; time_to_result: boolean }
  days_without_data: string[]
  notes: string[]
}

export type CostSource = 'apify_china' | 'apify_watch' | 'proxy'

export interface CostPlatform {
  platform: string
  by_source_usd: Partial<Record<CostSource, number | null>>
  download_cost_usd: number | null
  watch_cost_usd: number | null
  successes: number
  cost_per_success_usd: number | null
}

export interface CeilingRow {
  provider: string
  source: string
  daily_ceiling_usd: number | null
  avg_daily_usd: number | null
  peak_daily_usd: number | null
  avg_pct_of_ceiling: number | null
  peak_pct_of_ceiling: number | null
  suggested_ceiling_usd: [number | null, number | null] | null
  available: boolean
}

export interface SourceInfo {
  available: boolean
  note?: string | null
  price_per_gb_usd?: number
  since_utc?: string | null
}

export interface CostsPayload {
  days: number
  day_basis: string
  dates: string[]
  per_day: {
    date: string
    platforms: Record<string, Partial<Record<CostSource, number | null>>>
    asr_usd: number | null
    total_usd: number | null
  }[]
  platforms: CostPlatform[]
  asr_total_usd: number | null
  total_usd: number | null
  top_users: { user_id: string; cost_usd: number | null }[] | null
  unattributed: { available: boolean; guests_usd: number | null; system_usd: number | null }
  ceilings: CeilingRow[]
  sources: Record<string, SourceInfo>
  attribution_since_utc: string | null
  notes: string[]
}

export interface NotAvailable { available: false; note: string }

export interface RetentionCell { cohort_users: number; returned: number; rate: number | null }

export interface AdoptionPayload {
  days: number
  day_basis: string
  from: string
  to: string
  registrations: NotAvailable | { available: true; total: number; per_day: { date: string; count: number }[] }
  retention: NotAvailable | { available: true; d1: RetentionCell; d7: RetentionCell; note: string }
  watch: NotAvailable | {
    available: true
    accounts: number
    active_sources: number
    avg_per_account: number | null
    max_per_account: number | null
    distribution: Record<'1' | '2-5' | '6+', number>
  }
  inbox: NotAvailable | { available: true; accounts_marked_read: number; active_in_period: number; note: string }
  app_devices: NotAvailable | {
    available: true
    total: number
    active_in_period: number
    new_in_period: number
    versions: { version: string; machines: number }[]
  }
  extension_versions: NotAvailable
}

export interface MetricsSettings { weekly_report_enabled: boolean; weekly_report_schedule?: string }

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { ...(init?.body ? { 'Content-Type': 'application/json' } : {}) }
  const token = getAdminToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(`${API_BASE}/api/v1/admin/metrics${path}`, { ...init, headers })
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

export const metricsApi = {
  success: (days: MetricsDays) => call<SuccessPayload>(`/success?days=${days}`),
  costs: (days: MetricsDays) => call<CostsPayload>(`/costs?days=${days}`),
  adoption: (days: MetricsDays) => call<AdoptionPayload>(`/adoption?days=${days}`),
  settings: () => call<MetricsSettings>('/settings'),
  setWeekly: (on: boolean) =>
    call<MetricsSettings>('/settings', { method: 'POST', body: JSON.stringify({ weekly_report_enabled: on }) }),
  sendWeekly: () => call<{ sent: boolean; text?: string; skipped?: string }>('/weekly-report/send', { method: 'POST' }),
}

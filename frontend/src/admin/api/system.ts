import { adminFetch } from '../utils/adminFetch'

export interface OutcomeWindow {
  attempts?: number | null
  success?: number | null
  failed?: number | null
  success_rate?: number | null
  // Hourly counters may not cover the whole 24h window yet.
  partial?: boolean
  coverage_hours?: number
  covered_since?: string | null
}

/** Which admin calendar day "today" is (Vietnam time) and how it was read. */
export interface AdminDayMeta {
  date?: string | null
  timezone?: string
  window_start?: string | null
  window_end?: string | null
  /** "hourly" = exact VN day; "utc_day" = approximated from the UTC day */
  source?: string | null
  approximate?: boolean
}

export interface StatsResponse {
  /** Today = Vietnam calendar day (see `today`). */
  total_downloads_today?: number
  timezone?: string
  today?: AdminDayMeta
  /** Every attempt in the last 24h (outcome store) — same source as ErrorsResponse.summary_24h. */
  downloads_24h?: OutcomeWindow
  failed_24h?: number | null
  total_users?: number
  providers?: Record<string, number>
  /** Raw download_jobs rows (status=failed): the reason is error_message,
   *  the time created_at — there is no error / time column. */
  failed_jobs?: Array<{
    id?: string
    platform?: string
    error_message?: string | null
    error_type?: string | null
    job_stage?: string | null
    created_at?: string
  }>
}

export interface DailyStatEntry {
  /** Vietnam calendar date, YYYY-MM-DD */
  date: string
  total?: number
  success?: number
  failed?: number
  source?: string | null
  /** True: not a whole VN day — read from the UTC-day counter instead */
  approximate?: boolean
  window_start?: string | null
  window_end?: string | null
}

export interface AnalyticsResponse {
  timezone?: string
  approximate_days?: string[]
  daily_stats?: DailyStatEntry[]
  summary?: {
    total_jobs?: number
    success_rate?: number | null
    total_failed?: number
    avg_daily?: number
  }
}

export interface OpsHealthResponse {
  anomaly_count?: number
  active_anomalies?: Array<{
    id?: string
    type?: string
    message?: string
    likely_cause?: string
    metric?: string
    platform?: string
    detected_at?: string
    magnitude?: number
    auto_mitigated?: boolean
    state?: string
    severity?: string
    // Dedupe fields: absent until the backend ships them.
    occurrences?: number
    first_seen?: string
    last_seen?: string
  }>
  queue_health?: {
    ok?: boolean
    depth?: number
    workers?: number
  }
  fallback_summary?: Record<string, {
    success_rate?: number | null
    top_layer?: string
    total?: number
  }>
}

export interface ErrorsResponse {
  summary_24h?: {
    total?: number
    failed?: number
    fail_rate?: number | null
    ok?: number
    success_rate?: number | null
    hours?: number
  }
  partial?: boolean
  coverage_hours?: number
  covered_since?: string | null
}

export interface PlatformStatsTotal {
  platform: string
  ok: number
  err: number
  total: number
  success_rate: number | null
}

export interface PlatformStatsResponse {
  totals?: PlatformStatsTotal[]
  days?: number
}

export interface SignupsResponse {
  today?: number
  total_period?: number
}

export interface ProxyPoolEntry {
  redis: number
  env: number
  total: number
}

export interface ProxyPoolsResponse {
  success?: boolean
  pools?: Record<string, ProxyPoolEntry>
}

export interface ActiveJobsCountResponse {
  processing_count?: number
  pending_count?: number
}

export interface YoutubeStatusResponse {
  enabled?: boolean
  circuit_state?: string   // "closed" | "open" | "half" | "unknown"
  proxy_enabled?: boolean
  status_color?: string    // "green" | "yellow" | "red"
  proxy_bytes_today_gb?: number
  proxy_limit_gb?: number
}

export interface CookiePoolStatusResponse {
  /** GET /admin/cookies/status answers `pools` (the Cookies page reads it).
   *  This read `platforms`, which never exists, so the Overview said
   *  "chưa có cookie" for every platform whatever the pool held. */
  pools?: Record<string, {
    total?: number
    healthy?: number
    soft_blocked?: number
    hard_blocked?: number
  }>
  /** Platforms whose public videos download with no pool cookie, and how. */
  no_cookie_route?: Record<string, Array<'cobalt' | 'anonymous'>>
}

export type SnapshotSource =
  | 'stats' | 'analytics' | 'analytics7d' | 'ops' | 'errors' | 'platformStats'
  | 'signups' | 'proxyPools' | 'youtubeStatus' | 'cookiePoolStatus'

export interface SystemSnapshot {
  /** Sources whose request failed. Their data below is an empty placeholder, NOT a real zero. */
  failed: SnapshotSource[]
  stats: StatsResponse
  analytics: AnalyticsResponse      // 1-day summary for today's cards
  analytics7d: AnalyticsResponse    // 7-day history for sparklines
  ops: OpsHealthResponse
  errors: ErrorsResponse
  platformStats: PlatformStatsResponse
  signups: SignupsResponse
  proxyPools: ProxyPoolsResponse
  youtubeStatus: YoutubeStatusResponse
  cookiePoolStatus: CookiePoolStatusResponse
}

export async function fetchSystemSnapshot(): Promise<SystemSnapshot> {
  const [stats, analytics, analytics7d, ops, errors, platformStats, signups, proxyPools, youtubeStatus, cookiePoolStatus] =
    await Promise.allSettled([
      adminFetch<StatsResponse>('/stats'),
      adminFetch<AnalyticsResponse>('/analytics?days=1'),
      adminFetch<AnalyticsResponse>('/analytics?days=7'),
      adminFetch<OpsHealthResponse>('/ops-health'),
      adminFetch<ErrorsResponse>('/errors'),
      adminFetch<PlatformStatsResponse>('/platform-stats?days=1'),
      adminFetch<SignupsResponse>('/users/signups?days=1'),
      adminFetch<ProxyPoolsResponse>('/proxies/status'),
      adminFetch<YoutubeStatusResponse>('/youtube/status'),
      adminFetch<CookiePoolStatusResponse>('/cookies/status'),
    ])

  const named: Array<[SnapshotSource, PromiseSettledResult<unknown>]> = [
    ['stats', stats], ['analytics', analytics], ['analytics7d', analytics7d], ['ops', ops],
    ['errors', errors], ['platformStats', platformStats], ['signups', signups],
    ['proxyPools', proxyPools], ['youtubeStatus', youtubeStatus], ['cookiePoolStatus', cookiePoolStatus],
  ]

  return {
    failed: named.filter(([, r]) => r.status === 'rejected').map(([n]) => n),
    stats:            stats.status            === 'fulfilled' ? stats.value            : {},
    analytics:        analytics.status        === 'fulfilled' ? analytics.value        : {},
    analytics7d:      analytics7d.status      === 'fulfilled' ? analytics7d.value      : {},
    ops:              ops.status              === 'fulfilled' ? ops.value              : {},
    errors:           errors.status           === 'fulfilled' ? errors.value           : {},
    platformStats:    platformStats.status    === 'fulfilled' ? platformStats.value    : {},
    signups:          signups.status          === 'fulfilled' ? signups.value          : {},
    proxyPools:       proxyPools.status       === 'fulfilled' ? proxyPools.value       : {},
    youtubeStatus:    youtubeStatus.status    === 'fulfilled' ? youtubeStatus.value    : {},
    cookiePoolStatus: cookiePoolStatus.status === 'fulfilled' ? cookiePoolStatus.value : {},
  }
}

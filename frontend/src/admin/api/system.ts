import { adminFetch } from '../utils/adminFetch'

export interface StatsResponse {
  total_downloads_today?: number
  total_users?: number
  providers?: Record<string, number>
  failed_jobs?: Array<{
    id?: string
    platform?: string
    phase?: string
    error?: string
    time?: string
    action?: string
  }>
}

export interface DailyStatEntry {
  date: string
  total?: number
  success?: number
  failed?: number
}

export interface AnalyticsResponse {
  daily_stats?: DailyStatEntry[]
  summary?: {
    total_jobs?: number
    success_rate?: number
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
  }>
  queue_health?: {
    ok?: boolean
    depth?: number
    workers?: number
  }
  fallback_summary?: Record<string, {
    success_rate?: number
    top_layer?: string
    total?: number
  }>
}

export interface ErrorsResponse {
  summary_24h?: {
    total?: number
    failed?: number
    fail_rate?: number
  }
}

export interface PlatformStatsTotal {
  platform: string
  ok: number
  err: number
  total: number
  success_rate: number
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
  platforms?: Record<string, {
    total?: number
    healthy?: number
    soft_blocked?: number
    hard_blocked?: number
  }>
}

export interface SystemSnapshot {
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

  return {
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

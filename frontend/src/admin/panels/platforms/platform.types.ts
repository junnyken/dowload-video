// ─── Core status vocabulary ────────────────────────────────────────────────────

export type PlatformStatus = 'healthy' | 'warning' | 'critical' | 'disabled'
export type CircuitState   = 'closed'  | 'open'    | 'half'     | 'exempt'
export type JobResult      = 'success' | 'failed'  | 'running'

// ─── Table row ─────────────────────────────────────────────────────────────────

export interface PlatformHealthRow {
  platform: string          // canonical slug: 'youtube', 'tiktok', etc.
  status: PlatformStatus
  circuitState: CircuitState
  lastSuccessAt: string     // human-readable, e.g. "3m ago"
  failRate1h: number        // 0–100 (percentage)
  cookieRequired: boolean
  proxyRequired: boolean
  totalJobs1h: number
  activeJobs: number
}

// ─── Detail drawer ─────────────────────────────────────────────────────────────

export interface RecentJob {
  id: string
  result: JobResult
  url: string
  durationMs: number
  startedAt: string         // human-readable
  phase?: string            // last phase reached
  error?: string
}

export interface ErrorBreakdownItem {
  errorType: string
  count: number
  pct: number               // 0–100
}

export interface PhaseStatItem {
  phase: 'resolve' | 'auth' | 'extract' | 'transcode' | 'download' | string
  successRate: number       // 0–100
  avgDurationMs: number
  totalJobs: number
}

export interface PlatformConfig {
  rateLimit: string
  cookiePool: string
  proxy: string
  retryPolicy: string
}

export interface PlatformDetail {
  platform: string
  status: PlatformStatus
  circuitState: CircuitState
  description: string
  recentJobs: RecentJob[]
  errorBreakdown: ErrorBreakdownItem[]
  phaseStats: PhaseStatItem[]
  config: PlatformConfig
}

// ─── Filter state ──────────────────────────────────────────────────────────────

export interface PlatformFilter {
  search: string
  status: PlatformStatus | 'all'
  cookieRequired: boolean | null  // null = show all
  proxyRequired: boolean | null   // null = show all
}

export const DEFAULT_FILTER: PlatformFilter = {
  search: '',
  status: 'all',
  cookieRequired: null,
  proxyRequired: null,
}

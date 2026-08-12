export type StatusLevel    = 'ok' | 'warn' | 'error' | 'disabled'
export type CircuitState   = 'closed' | 'open' | 'half' | 'exempt'
export type CookieStatus   = 'active' | 'soft_blocked' | 'hard_blocked' | 'expired' | 'disabled' | 'untested'
export type JobPhase       = 'queued' | 'resolving' | 'metadata' | 'auth' | 'download' | 'post-process' | 'done'
export type PhaseStatus    = 'pending' | 'running' | 'success' | 'failed' | 'skipped'
export type AlertSeverity  = 'critical' | 'warning' | 'info'
export type AdminRole      = 'viewer' | 'operator' | 'admin' | 'superadmin'

export interface PlatformHealthRow {
  platform: string
  displayName: string
  icon: string
  status: StatusLevel
  circuitState: CircuitState
  lastSuccessAt: string | null
  failRate1h: number
  totalJobs1h: number
  cookieRequired: boolean
  proxyRequired: boolean
  enabled: boolean
}

export interface CookieItem {
  id: string
  platform: string
  accountLabel: string
  status: CookieStatus
  healthScore: number
  lastSuccessAt: string | null
  lastFailAt: string | null
  failCount: number
  cooldownRemainingSec: number
  expiryEstimate: string | null
}

export interface JobTracePhase {
  phase: JobPhase
  status: PhaseStatus
  startedAt: string | null
  endedAt: string | null
  durationMs: number | null
  proxyUsed: string | null
  cookieUsed: string | null
  errorMessage: string | null
}

export interface SystemAlert {
  id: string
  severity: AlertSeverity
  title: string
  message: string
  timestamp: string
  dismissible: boolean
}

export interface HealthPanelData {
  id: string
  title: string
  icon: string
  status: StatusLevel
  primaryMetric: string
  secondaryMetric?: string
  lastUpdated: string
  href: string
}

export interface RecentFailure {
  jobId: string
  platform: string
  url: string
  errorType: string
  failedAt: string
  phase: string
}

export interface AdminUser {
  email: string
  role: AdminRole
  expiresAt: string
  sessionToken?: string   // Bearer token from POST /admin/login
}

// Phase 27A — Lane Observability Types
// Pure observability — no scheduling/routing semantics

export type LaneState = 'healthy' | 'constrained' | 'degraded' | 'paused' | 'disabled'

export type FailureBucket =
  | 'login_required'
  | 'soft_block'
  | 'hard_block'
  | 'proxy_missing'
  | 'proxy_failed'
  | 'circuit_open'
  | 'all_cookies_busy'
  | 'extraction_failed'
  | 'timeout'
  | 'upstream_unavailable'
  | 'private_content'
  | 'unknown'

export interface FailureBucketItem {
  bucket: FailureBucket
  count: number
}

export interface LaneObservation {
  platform:               string
  laneState:              LaneState
  constrainedReason:      string | null
  activeJobs:             number
  queueDepth:             number
  healthyCookies:         number
  coolingCookies:         number
  softBlockedCookies:     number
  hardBlockedCookies:     number
  expiredCookies:         number
  allBusyCount1h:         number
  cookieRequired:         boolean
  proxyRequired:          boolean
  effectiveRpmEstimate:   number        // display-only "~" prefix
  avgWaitSecEstimate:     number
  recentFailureReasons:   FailureBucketItem[]
  lastSuccessAt:          string | null  // ISO 8601
  lastFailureAt:          string | null
  circuitState:           'closed' | 'open' | 'half'
  throttleUtilizationPct: number         // 0–100
}

export interface LaneSnapshotResponse {
  success:   boolean
  updatedAt: string         // ISO 8601
  lanes:     LaneObservation[]
}

// Utility: derive display colour for a lane state
export function laneStateColor(state: LaneState): string {
  return {
    healthy:     'text-success',
    constrained: 'text-accent-text',
    degraded:    'text-warning',
    paused:      'text-danger',
    disabled:    'text-fg-muted',
  }[state] ?? 'text-fg-muted'
}

export function laneStateBg(state: LaneState): string {
  return {
    healthy:     'bg-success-soft',
    constrained: 'bg-accent-soft',
    degraded:    'bg-warning-soft',
    paused:      'bg-danger-soft',
    disabled:    'bg-surface-2',
  }[state] ?? 'bg-surface-2'
}

export function failureBucketLabel(bucket: FailureBucket): string {
  return {
    login_required:       'Login required',
    soft_block:           'Soft block (429)',
    hard_block:           'Hard block',
    proxy_missing:        'Proxy missing',
    proxy_failed:         'Proxy failed',
    circuit_open:         'Circuit open',
    all_cookies_busy:     'All cookies busy',
    extraction_failed:    'Extraction failed',
    timeout:              'Timeout',
    upstream_unavailable: 'Upstream down',
    private_content:      'Private / DRM',
    unknown:              'Unknown',
  }[bucket] ?? bucket
}

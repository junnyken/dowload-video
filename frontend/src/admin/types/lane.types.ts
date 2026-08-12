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
    healthy:     'text-emerald-400',
    constrained: 'text-yellow-400',
    degraded:    'text-orange-400',
    paused:      'text-red-400',
    disabled:    'text-slate-500',
  }[state] ?? 'text-slate-400'
}

export function laneStateBg(state: LaneState): string {
  return {
    healthy:     'bg-emerald-500/15',
    constrained: 'bg-yellow-500/15',
    degraded:    'bg-orange-500/15',
    paused:      'bg-red-500/15',
    disabled:    'bg-slate-700/30',
  }[state] ?? 'bg-slate-700/30'
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

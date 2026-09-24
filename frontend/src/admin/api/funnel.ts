import { adminFetch } from '../utils/adminFetch'

export interface FunnelStep {
  event: string
  label: string
  users: number
  events: number
  pct_of_top: number | null
  /** null when this step has more people than the one before it — see `ordered`. */
  drop_from_prev_pct: number | null
}

export interface FunnelFailure {
  stage: string
  ok_users: number
  failed_users: number
  /** null when nobody attempted this stage at all — different from 0%. */
  failure_rate_pct: number | null
}

export interface FunnelPlatform {
  platform: string
  download_ok: number
  download_failed: number
  failure_rate_pct: number | null
  fetch_failed: number
}

export interface FunnelResponse {
  success: boolean
  range: { days: number; since: string }
  /** Always false today: steps count "did this event", not "walked this path". */
  ordered: boolean
  note: string
  truncated: boolean
  steps: FunnelStep[]
  failures: FunnelFailure[]
  by_platform: FunnelPlatform[]
}

export function fetchFunnel(days: number): Promise<FunnelResponse> {
  return adminFetch<FunnelResponse>(`/funnel?days=${days}`)
}

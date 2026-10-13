import { adminFetch } from '../utils/adminFetch'

/** not_configured and stale are distinct from ok on purpose: "nobody has
 *  looked" must never render as "we looked and it was fine". */
export type ProbeStatus = 'ok' | 'failed' | 'unconfirmed' | 'stale' | 'not_configured'

export interface ProbeRow {
  platform: string
  status: ProbeStatus
  target: string | null
  reason: string | null
  at: number | null
  ms: number | null
  age_s: number | null
  /** ISO UTC time of the last SUCCESSFUL probe, null if never seen working. */
  last_verified_at?: string | null
  /** Short admin-written note shown with the public status; "" when none. */
  known_limitation?: string
  /** Minimum seconds between two probes (24 h for paid platforms). */
  min_interval_s?: number
  /** Probing this platform can reach a paid provider. */
  paid?: boolean
}

export interface ProbesResponse {
  success: boolean
  platforms: ProbeRow[]
  counts: Record<string, number>
  total: number
  note: string
}

export const fetchProbes = () => adminFetch<ProbesResponse>('/probes')

export const setProbeTarget = (platform: string, url: string | null) =>
  adminFetch<{ success: boolean; configured: number }>('/probes/target', {
    method: 'POST',
    body: JSON.stringify({ platform, url }),
  })

export const setProbeSettings = (platform: string, settings: { known_limitation?: string | null; min_interval_sec?: number | null }) =>
  adminFetch<{ success: boolean; known_limitation: string; min_interval_s: number }>('/probes/settings', {
    method: 'POST',
    body: JSON.stringify({ platform, ...settings }),
  })

export const runProbesNow = () =>
  adminFetch<{ success: boolean; queued: boolean }>('/probes/run', { method: 'POST' })

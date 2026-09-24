import { adminFetch } from '../utils/adminFetch'

/** not_configured and stale are distinct from ok on purpose: "nobody has
 *  looked" must never render as "we looked and it was fine". */
export type ProbeStatus = 'ok' | 'failed' | 'stale' | 'not_configured'

export interface ProbeRow {
  platform: string
  status: ProbeStatus
  target: string | null
  reason: string | null
  at: number | null
  ms: number | null
  age_s: number | null
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

export const runProbesNow = () =>
  adminFetch<{ success: boolean; queued: boolean }>('/probes/run', { method: 'POST' })

// Phase 27 — Smart Throughput Orchestrator — Admin types

export type LaneState = 'ACTIVE' | 'THROTTLED' | 'OFFLINE' | 'RECOVERY'

export interface LaneSnapshot {
  platform: string
  state: LaneState
  state_age_s: number | null
  rpm_current: number
  rpm_ceiling: number
  lane_active: number
}

export interface ThroughputSnapshot {
  platform: string
  rpm_current: number
  rpm_ceiling: number
  utilization: number   // 0–100
  over_limit: boolean
}

export interface FairnessSnapshot {
  global_depth: number
  global_cap: number
  utilization: number   // 0–100
  top_users: Array<{ user_id: string; active: number }>
}

export interface CookieScoreEntry {
  index: number
  hash: string
  label: string
  score: number         // 0.0–1.0
  tier: 'best' | 'ok' | 'degraded'
}

export interface OrchestratorSummary {
  lanes: LaneSnapshot[]
  throughput: ThroughputSnapshot[]
  fairness: FairnessSnapshot
  summary: {
    total_platforms: number
    by_state: Partial<Record<LaneState, number>>
    queue_depth: number
    over_limit_count: number
  }
}

// API response wrappers
export interface LanesResponse      { success: boolean; lanes: LaneSnapshot[] }
export interface ThroughputResponse { success: boolean; throughput: ThroughputSnapshot[] }
export interface FairnessResponse   { success: boolean; fairness: FairnessSnapshot }
export interface CookieScoresResponse { success: boolean; platform: string; scores: CookieScoreEntry[] }
export interface OrchestratorSummaryResponse { success: boolean } & OrchestratorSummary

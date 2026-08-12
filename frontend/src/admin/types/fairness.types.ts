// Phase 27C — Fairness and Admission Control — Admin Types

export type AdmissionDecision =
  | 'ACCEPT_IMMEDIATE'
  | 'ACCEPT_DELAYED'
  | 'REJECT_TEMPORARY'
  | 'REJECT_PLATFORM_UNAVAILABLE'

export type AdmissionReason =
  | 'ok'
  | 'admission_disabled'
  | 'circuit_open'
  | 'platform_disabled'
  | 'user_cap_reached'
  | 'lane_degraded_soft_cap'
  | 'lane_paused_soft_cap'
  | 'lane_constrained_soft_cap'
  | string  // future reasons

export interface AdmissionResult {
  admissionDecision:    AdmissionDecision
  admissionReason:      AdmissionReason
  delayed:              boolean
  estimatedWaitSec:     number | null
  retryAfter:           number
  fairnessPressure:     number        // 0.0–1.0
  userActivePlatform:   number
  platformActiveLimit:  number
  canRetry:             boolean
  shadow:               boolean
}

// Per-platform admission counter snapshot (last 24h)
export interface PlatformAdmissionCounters {
  immediate:   number
  delayed:     number
  temp_reject: number
  unavail:     number
  total:       number
  rejectRate:  number   // 0.0–1.0
  delayRate:   number   // 0.0–1.0
}

// Per-platform fairness (active slots per user)
export interface FairnessTopUser {
  user_key: string   // truncated, privacy-safe
  active:   number
}

export interface PlatformFairnessSnapshot {
  platform:      string
  totalActive:   number
  singleHardCap: number
  singleSoftCap: number
  batchHardCap:  number
  topUsers:      FairnessTopUser[]
}

// Delayed queue snapshot for a platform
export interface DelayedJobEntry {
  user_key:   string
  submit_ts:  number
  waited_sec: number
  is_batch:   boolean
  expire_at:  number
}

export interface PlatformDelayedQueue {
  platform:         string
  queueDepth:       number
  oldestWaitSec:    number | null
  maxWaitSec:       number
  estimatedWaitSec: number
  topJobs:          DelayedJobEntry[]
}

// Combined platform detail (from /fairness/platform/{platform})
export interface FairnessPlatformDetail {
  success:      boolean
  platform:     string
  fairness:     PlatformFairnessSnapshot
  admission:    PlatformAdmissionCounters
  delayedQueue: PlatformDelayedQueue
}

// Per-platform summary in overview
export interface PlatformAdmissionSummary {
  platform:    string
  immediate:   number
  delayed:     number
  temp_reject: number
  unavail:     number
  total:       number
  reject_rate: number
}

// Delayed queue list item for admin home bar
export interface DelayedQueueItem {
  platform:     string
  depth:        number
  oldestWaitSec: number | null
}

// Global fairness overview
export interface FairnessOverview {
  success:                 boolean
  admissionEnabled:        boolean
  fairnessEnabled:         boolean
  delayedAcceptEnabled:    boolean
  shadowMode:              boolean
  totalDelayed:            number
  platformsUnderPressure:  string[]
  queueDepths:             Record<string, number>
  platformSummaries:       PlatformAdmissionSummary[]
}

export interface DelayedQueuesResponse {
  success:      boolean
  totalDelayed: number
  queues:       DelayedQueueItem[]
}

// ── Display helpers ────────────────────────────────────────────────────────────

export const ADMISSION_DECISION_LABEL: Record<AdmissionDecision, string> = {
  ACCEPT_IMMEDIATE:              'Immediate',
  ACCEPT_DELAYED:                'Delayed',
  REJECT_TEMPORARY:              'Rejected (temp)',
  REJECT_PLATFORM_UNAVAILABLE:   'Unavailable',
}

export const ADMISSION_DECISION_COLOR: Record<AdmissionDecision, string> = {
  ACCEPT_IMMEDIATE:             'text-emerald-400',
  ACCEPT_DELAYED:               'text-amber-400',
  REJECT_TEMPORARY:             'text-orange-400',
  REJECT_PLATFORM_UNAVAILABLE:  'text-red-400',
}

export const ADMISSION_DECISION_BG: Record<AdmissionDecision, string> = {
  ACCEPT_IMMEDIATE:             'bg-emerald-500/10',
  ACCEPT_DELAYED:               'bg-amber-500/10',
  REJECT_TEMPORARY:             'bg-orange-500/10',
  REJECT_PLATFORM_UNAVAILABLE:  'bg-red-500/10',
}

export function pressureColor(p: number): string {
  if (p >= 0.9) return 'text-red-400'
  if (p >= 0.7) return 'text-orange-400'
  if (p >= 0.4) return 'text-amber-400'
  return 'text-emerald-400'
}

export function pressureLabel(p: number): string {
  if (p >= 0.9) return 'Critical'
  if (p >= 0.7) return 'High'
  if (p >= 0.4) return 'Medium'
  return 'Low'
}

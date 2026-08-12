// Phase 27B — Scored Cookie Selection — Admin Types

export type CookieScoreTier = 'best' | 'ok' | 'degraded'

export interface CookieScoreEntry {
  index:              number
  hash:               string        // 8-char prefix, display-safe
  label:              string
  score:              number         // 0.0–1.0
  tier:               CookieScoreTier
  health:             string         // "healthy" | "soft" | "hard"
  in_cooldown:        boolean
  disabled:           boolean        // admin manually disabled from scoring
  warmup:             boolean
  success_rate:       number         // 0.0–1.0
  freshness:          number         // 0.0–1.0
  expiry_factor:      number
  penalty:            number         // 0.0–0.8 total deducted
  c429:               number         // 429 events in last 1h
  cfail:              number         // other failures in last 1h
  eligibleForScoring: boolean
}

export interface CookieScoreResponse {
  success:           boolean
  platform:          string
  scoringEnabled:    boolean
  shadowLog:         boolean
  candidateCookies:  number
  warmingUpCookies:  number
  topScore:          number
  avgScore:          number
  cookies:           CookieScoreEntry[]
}

export interface SelectionHistoryResponse {
  success:        boolean
  platform:       string
  scoringEnabled: boolean
  shadowMode:     boolean
  recent:         string[]   // hash prefixes, last 20 in 1h
}

// Score tier display helpers
export const SCORE_TIER_COLOR: Record<CookieScoreTier, string> = {
  best:     'text-emerald-400',
  ok:       'text-slate-300',
  degraded: 'text-orange-400',
}

export const SCORE_TIER_BG: Record<CookieScoreTier, string> = {
  best:     'bg-emerald-500/15',
  ok:       'bg-slate-700/30',
  degraded: 'bg-orange-500/15',
}

export function scoreBar(score: number): number {
  return Math.round(Math.max(0, Math.min(1, score)) * 100)
}

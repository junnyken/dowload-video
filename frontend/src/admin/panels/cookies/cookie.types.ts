// ─── Status vocabulary ─────────────────────────────────────────────────────────

export type CookieStatus =
  | 'active'        // working normally
  | 'soft_blocked'  // temp blocked, 15-min cooldown
  | 'hard_blocked'  // blocked long-term, 6h cooldown
  | 'expired'       // cookie TTL elapsed or session invalidated
  | 'disabled'      // manually taken offline
  | 'untested'      // newly added, not yet verified

export type ImportMode = 'netscape' | 'json' | 'raw'

// ─── Core row ──────────────────────────────────────────────────────────────────

export interface CookieItem {
  id: string
  platform: string
  accountLabel: string
  status: CookieStatus
  /** 0–100; computed from fail_count + success_count */
  healthScore: number
  lastSuccessAt: string         // human-readable, e.g. "2m ago"
  lastFailAt: string | null
  failCount: number
  cooldownRemainingSec: number  // 0 when not in cooldown
  expiryEstimate: string        // e.g. "~8 months" | "Expired" | "Unknown"
}

// ─── Summary ───────────────────────────────────────────────────────────────────

export interface CookiePoolSummary {
  total: number
  active: number
  cooldown: number      // soft + hard blocked
  disabled: number      // disabled + expired
  expiringSoon: number  // expiry within 30 days
}

// ─── Add-cookie form ───────────────────────────────────────────────────────────

export interface AddCookieFormData {
  platform: string
  accountLabel: string
  rawCookie: string
  importMode: ImportMode
  notes: string
}

export const DEFAULT_ADD_FORM: AddCookieFormData = {
  platform: '',
  accountLabel: '',
  rawCookie: '',
  importMode: 'netscape',
  notes: '',
}

// ─── Actions ───────────────────────────────────────────────────────────────────

export type CookieAction =
  | 'test'
  | 'reset_soft'
  | 'reset_hard'
  | 'rotate'
  | 'disable'
  | 'enable'
  | 'delete'

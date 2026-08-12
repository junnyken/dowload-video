// Phase 28 — Platform Policy & Runtime Override Types

export interface PlatformPolicy {
  platform:             string
  aggressiveness:       'conservative' | 'balanced' | 'ample'
  cookie_required:      boolean
  proxy_required:       boolean
  base_rpm:             number
  wave_size_min:        number
  wave_size_default:    number
  wave_size_max:        number
  wave_delay_min_s:     number
  wave_delay_default_s: number
  wave_delay_max_s:     number
  fairness_single_cap:  number
  fairness_batch_cap:   number
  soft_block_ttl_s:     number
  hard_block_ttl_s:     number
  warmup_s:             number
  delayed_backlog_alert:    number
  reduced_mode_fail_rate:   number
  scoring_eligible:     boolean
  adaptive_eligible:    boolean
}

export interface PlatformModeOverride {
  mode:       string
  reason:     string
  set_at:     string
  expires_at: string
}

export interface PlatformOverrides {
  mode?:          PlatformModeOverride | null
  adaptive_frozen?: boolean
  scoring_frozen?:  boolean
  policy_fields?:   Record<string, string>
}

export interface GlobalSafeModeInfo {
  active?:  string
  reason?:  string
  set_at?:  string
}

export interface OverrideAuditEntry {
  ts:       number
  platform: string
  action:   string
  value:    string
  reason:   string
  set_by:   string
}

export interface PolicyPlatformResponse {
  success:         boolean
  platform:        string
  policy:          PlatformPolicy
  modeOverride:    PlatformModeOverride | null
  adaptiveFrozen:  boolean
  scoringFrozen:   boolean
}

export interface PolicyAllResponse {
  success:        boolean
  policies:       Record<string, PlatformPolicy>
  overrides:      {
    global_safe_mode: GlobalSafeModeInfo
    platforms: Record<string, PlatformOverrides>
  }
  globalSafeMode: boolean
}

export interface PolicyOverridesResponse {
  success:        boolean
  globalSafeMode: boolean
  globalInfo:     GlobalSafeModeInfo
  overrides: {
    global_safe_mode: GlobalSafeModeInfo
    platforms: Record<string, PlatformOverrides>
  }
  auditLog: OverrideAuditEntry[]
}

// ── Display helpers ───────────────────────────────────────────────────────────

export const aggressivenessLabel: Record<string, string> = {
  conservative: 'Conservative',
  balanced:     'Balanced',
  ample:        'Ample',
}

export const aggressivenessColor: Record<string, string> = {
  conservative: 'text-orange-400',
  balanced:     'text-yellow-400',
  ample:        'text-green-400',
}

export function formatTtl(seconds: number): string {
  if (seconds < 60)  return `${seconds}s`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  return `${Math.round(seconds / 3600)}h`
}

// Phase 27D — Adaptive Wave Scheduler — Admin Types

export type WaveMode =
  | 'aggressive'
  | 'balanced'
  | 'conservative'
  | 'reduced'
  | 'emergency'
  | 'disabled'

export type WaveProfile = 'conservative' | 'balanced' | 'ample'

export interface WaveParams {
  platform:        string
  wave_size:       number
  wave_delay:      number      // seconds
  mode:            WaveMode
  adaptive_active: boolean
  shadow_only:     boolean
  reason:          string
  profile:         WaveProfile
  lane_state:      string
  healthy_cookies: number      // -1 = not applicable
  base_size:       number      // auto_tuner global value
  ts:              number      // Unix timestamp
}

export interface WaveDecision {
  ts:         number
  wave_size:  number
  wave_delay: number
  mode:       WaveMode
  reason:     string
  lane_state: string
}

export interface PlatformWaveSnapshot {
  platform:          string
  current:           Record<string, string>   // raw hash from Redis
  history:           WaveDecision[]
  inCooldown:        boolean
  cooldownExpiresIn: number | null            // seconds
  adaptiveEnabled:   boolean
  shadowOnly:        boolean
}

export interface PlatformWaveResponse {
  success:         boolean
  platform:        string
  current:         WaveParams
  snapshot:        PlatformWaveSnapshot
  adaptiveEnabled: boolean
  shadowOnly:      boolean
}

export interface WaveSnapshotResponse {
  success:         boolean
  adaptiveEnabled: boolean
  shadowOnly:      boolean
  platforms:       PlatformWaveSnapshot[]
}

// ── Display helpers ──────────────────────────────────────────────────────────

export const WAVE_MODE_COLOR: Record<WaveMode, string> = {
  aggressive:   'text-emerald-400',
  balanced:     'text-slate-300',
  conservative: 'text-amber-400',
  reduced:      'text-orange-400',
  emergency:    'text-red-400',
  disabled:     'text-slate-500',
}

export const WAVE_MODE_BG: Record<WaveMode, string> = {
  aggressive:   'bg-emerald-500/10',
  balanced:     'bg-slate-700/30',
  conservative: 'bg-amber-500/10',
  reduced:      'bg-orange-500/10',
  emergency:    'bg-red-500/10',
  disabled:     'bg-slate-800/40',
}

export const WAVE_MODE_LABEL: Record<WaveMode, string> = {
  aggressive:   'Aggressive',
  balanced:     'Balanced',
  conservative: 'Conservative',
  reduced:      'Reduced',
  emergency:    'Emergency',
  disabled:     'Disabled',
}

export function waveModeUrgency(mode: WaveMode): 'ok' | 'warn' | 'error' {
  if (mode === 'aggressive' || mode === 'balanced') return 'ok'
  if (mode === 'conservative' || mode === 'reduced') return 'warn'
  return 'error'
}

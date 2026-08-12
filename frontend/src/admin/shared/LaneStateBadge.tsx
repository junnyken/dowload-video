import { StatusPill, type PillStatus } from './StatusPill'
import type { LaneState } from '../types/lane.types'

const LANE_TO_PILL: Record<LaneState, PillStatus> = {
  healthy:     'healthy',
  constrained: 'warning',
  degraded:    'degraded',
  paused:      'open',       // circuit-open red
  disabled:    'disabled',
}

interface LaneStateBadgeProps {
  state:       LaneState
  reason?:     string | null   // shown as tooltip via title
  size?:       'xs' | 'sm' | 'md'
  dot?:        boolean
  className?:  string
}

export function LaneStateBadge({ state, reason, size = 'xs', dot = true, className }: LaneStateBadgeProps) {
  return (
    <span title={reason ?? undefined}>
      <StatusPill
        status={LANE_TO_PILL[state]}
        label={state}
        size={size}
        dot={dot}
        className={className}
      />
    </span>
  )
}

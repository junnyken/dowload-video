import { useQuery } from '@tanstack/react-query'
import { fetchLaneSnapshot, fetchLaneDetail } from '../api/lanes'
import type { LaneSnapshotResponse, LaneObservation } from '../types/lane.types'

// Query keys
const laneKeys = {
  all:    ['admin', 'lanes'] as const,
  snapshot: () => [...laneKeys.all, 'snapshot'] as const,
  detail: (platform: string) => [...laneKeys.all, 'detail', platform] as const,
}

/**
 * Lane health snapshot for all platforms.
 * Refreshes every 20s — fast enough to catch degradation, slow enough not to hammer Redis.
 */
export function usePlatformLaneHealth(refetchMs = 20_000) {
  return useQuery<LaneSnapshotResponse>({
    queryKey:        laneKeys.snapshot(),
    queryFn:         fetchLaneSnapshot,
    refetchInterval: refetchMs,
    staleTime:       10_000,
  })
}

/**
 * Single-platform lane detail. Load on demand (e.g. detail drawer open).
 */
export function usePlatformLaneDetail(platform: string | null, refetchMs = 30_000) {
  return useQuery<{ success: boolean } & LaneObservation>({
    queryKey:        laneKeys.detail(platform ?? ''),
    queryFn:         () => fetchLaneDetail(platform!),
    enabled:         !!platform,
    refetchInterval: refetchMs,
    staleTime:       15_000,
  })
}

/**
 * Convenience: look up a single platform's observation from the snapshot cache.
 * Returns undefined while loading.
 */
export function useLaneObservation(platform: string) {
  const { data } = usePlatformLaneHealth()
  return data?.lanes.find(l => l.platform === platform)
}

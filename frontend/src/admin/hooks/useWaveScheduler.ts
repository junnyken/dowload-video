// Phase 27D — Adaptive Wave Scheduler — Admin Hooks

import { useQuery } from '@tanstack/react-query'
import { adminFetch } from '../utils/adminFetch'
import type { PlatformWaveResponse, WaveSnapshotResponse } from '../types/waveScheduler.types'

const waveKeys = {
  all:      ['admin', 'wave-scheduler'] as const,
  platform: (p: string) => [...waveKeys.all, 'platform', p] as const,
  snapshot: ()          => [...waveKeys.all, 'snapshot'] as const,
}

/** Current adaptive wave params for a single platform (20s refresh). */
export function usePlatformWaveParams(platform: string | null, enabled = true) {
  return useQuery<PlatformWaveResponse>({
    queryKey:        waveKeys.platform(platform ?? ''),
    queryFn:         () => adminFetch<PlatformWaveResponse>(`/orchestrator/wave-params/${platform}`),
    enabled:         enabled && !!platform,
    refetchInterval: 20_000,
    staleTime:       10_000,
  })
}

/** Wave state across all platforms with adaptive state (30s refresh). */
export function useWaveSnapshot(refetchMs = 30_000) {
  return useQuery<WaveSnapshotResponse>({
    queryKey:        waveKeys.snapshot(),
    queryFn:         () => adminFetch<WaveSnapshotResponse>('/orchestrator/wave-snapshot'),
    refetchInterval: refetchMs,
    staleTime:       15_000,
  })
}

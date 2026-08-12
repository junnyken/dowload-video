// Phase 27C — Fairness and Admission Control — Admin Hooks

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { adminFetch, adminPost } from '../utils/adminFetch'
import type {
  FairnessOverview,
  FairnessPlatformDetail,
  DelayedQueuesResponse,
} from '../types/fairness.types'

// ── Query keys ────────────────────────────────────────────────────────────────
export const fairnessKeys = {
  all:              ['admin', 'fairness'] as const,
  overview:         () => [...fairnessKeys.all, 'overview'] as const,
  platformDetail:   (p: string) => [...fairnessKeys.all, 'platform', p] as const,
  delayedQueues:    () => [...fairnessKeys.all, 'delayed-queues'] as const,
}

// ── Hooks ─────────────────────────────────────────────────────────────────────

/**
 * Cross-platform fairness overview — admission counters, queue depths, pressure.
 * Refresh every 20s; shown in AdminHomePage alert bar.
 */
export function useFairnessOverview(refetchMs = 20_000) {
  return useQuery<FairnessOverview>({
    queryKey:        fairnessKeys.overview(),
    queryFn:         () => adminFetch<FairnessOverview>('/fairness/overview'),
    refetchInterval: refetchMs,
    staleTime:       10_000,
  })
}

/**
 * Full fairness snapshot for a single platform.
 * Used in PlatformDetailDrawer → Fairness tab.
 */
export function useFairnessPlatformDetail(platform: string | null, enabled = true) {
  return useQuery<FairnessPlatformDetail>({
    queryKey:        fairnessKeys.platformDetail(platform ?? ''),
    queryFn:         () => adminFetch<FairnessPlatformDetail>(`/fairness/platform/${platform}`),
    enabled:         enabled && !!platform,
    refetchInterval: 15_000,
    staleTime:        8_000,
  })
}

/**
 * All delayed queue depths — lightweight snapshot for alert bar.
 */
export function useDelayedQueues(refetchMs = 15_000) {
  return useQuery<DelayedQueuesResponse>({
    queryKey:        fairnessKeys.delayedQueues(),
    queryFn:         () => adminFetch<DelayedQueuesResponse>('/fairness/delayed-queues'),
    refetchInterval: refetchMs,
    staleTime:        8_000,
  })
}

/**
 * Manually promote the top delayed job on a platform.
 * Admin action — used in PlatformDetailDrawer.
 */
export function useForcePromoteDelayed(platform: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => adminPost(`/fairness/promote/${platform}`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: fairnessKeys.platformDetail(platform) })
      qc.invalidateQueries({ queryKey: fairnessKeys.delayedQueues() })
      qc.invalidateQueries({ queryKey: fairnessKeys.overview() })
    },
  })
}

// Phase 28 — Platform Policy & Runtime Override Hooks

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { adminFetch } from '../utils/adminFetch'
import type {
  PolicyPlatformResponse,
  PolicyAllResponse,
  PolicyOverridesResponse,
} from '../types/policy.types'

const policyKeys = {
  all:       ['admin', 'policy'] as const,
  platform:  (p: string) => [...policyKeys.all, 'platform', p] as const,
  allPlats:  ()           => [...policyKeys.all, 'all']          as const,
  overrides: ()           => [...policyKeys.all, 'overrides']    as const,
}

/** Effective policy for a single platform (merged: static < env < runtime). */
export function usePlatformPolicy(platform: string | null, enabled = true) {
  return useQuery<PolicyPlatformResponse>({
    queryKey:        policyKeys.platform(platform ?? ''),
    queryFn:         () => adminFetch<PolicyPlatformResponse>(`/policy/platform/${platform}`),
    enabled:         enabled && !!platform,
    refetchInterval: 30_000,
    staleTime:       15_000,
  })
}

/** Effective policy for all known platforms (60s refresh). */
export function useAllPlatformPolicies(enabled = true) {
  return useQuery<PolicyAllResponse>({
    queryKey:        policyKeys.allPlats(),
    queryFn:         () => adminFetch<PolicyAllResponse>('/policy/all'),
    enabled,
    refetchInterval: 60_000,
    staleTime:       30_000,
  })
}

/** All active runtime overrides + audit log (30s refresh). */
export function usePolicyOverrides(enabled = true) {
  return useQuery<PolicyOverridesResponse>({
    queryKey:        policyKeys.overrides(),
    queryFn:         () => adminFetch<PolicyOverridesResponse>('/policy/overrides'),
    enabled,
    refetchInterval: 30_000,
    staleTime:       15_000,
  })
}

// ── Mutations ─────────────────────────────────────────────────────────────────

interface SetModeArgs   { platform: string; mode: string; reason?: string; ttl?: number }
interface FreezeArgs    { platform: string; reason?: string; ttl?: number }
interface SafeModeArgs  { reason?: string }

export function useSetPlatformMode() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ platform, mode, reason = '', ttl }: SetModeArgs) =>
      adminFetch(`/policy/platform/${platform}/mode`, {
        method: 'POST',
        body:   JSON.stringify({ mode, reason, ttl }),
      }),
    onSuccess: (_d, { platform }) => {
      qc.invalidateQueries({ queryKey: policyKeys.platform(platform) })
      qc.invalidateQueries({ queryKey: policyKeys.overrides() })
    },
  })
}

export function useClearPlatformMode() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ platform }: { platform: string }) =>
      adminFetch(`/policy/platform/${platform}/mode`, { method: 'DELETE' }),
    onSuccess: (_d, { platform }) => {
      qc.invalidateQueries({ queryKey: policyKeys.platform(platform) })
      qc.invalidateQueries({ queryKey: policyKeys.overrides() })
    },
  })
}

export function useFreezeAdaptive() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ platform, reason = '', ttl = 3600 }: FreezeArgs) =>
      adminFetch(`/policy/platform/${platform}/freeze-adaptive`, {
        method: 'POST',
        body:   JSON.stringify({ reason, ttl }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: policyKeys.all }),
  })
}

export function useUnfreezeAdaptive() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ platform }: { platform: string }) =>
      adminFetch(`/policy/platform/${platform}/freeze-adaptive`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: policyKeys.all }),
  })
}

export function useFreezeScoring() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ platform, reason = '', ttl = 7200 }: FreezeArgs) =>
      adminFetch(`/policy/platform/${platform}/freeze-scoring`, {
        method: 'POST',
        body:   JSON.stringify({ reason, ttl }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: policyKeys.all }),
  })
}

export function useUnfreezeScoring() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ platform }: { platform: string }) =>
      adminFetch(`/policy/platform/${platform}/freeze-scoring`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: policyKeys.all }),
  })
}

export function useSetGlobalSafeMode() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ reason = 'admin_manual' }: SafeModeArgs) =>
      adminFetch('/policy/safe-mode', {
        method: 'POST',
        body:   JSON.stringify({ reason }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: policyKeys.all }),
  })
}

export function useClearGlobalSafeMode() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => adminFetch('/policy/safe-mode', { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: policyKeys.all }),
  })
}

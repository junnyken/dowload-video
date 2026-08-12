import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { adminFetch, adminPost } from '../utils/adminFetch'
import type { CookieScoreResponse, SelectionHistoryResponse } from '../types/cookieScore.types'

// ── Query keys ────────────────────────────────────────────────────────────────
const scoreKeys = {
  all:       ['admin', 'cookie-score'] as const,
  breakdown: (platform: string) => [...scoreKeys.all, 'breakdown', platform] as const,
  history:   (platform: string) => [...scoreKeys.all, 'history',   platform] as const,
}

// ── Hooks ─────────────────────────────────────────────────────────────────────

/** Score breakdown for all cookies on a platform (20s refresh). */
export function useCookieScoreBreakdown(platform: string | null, enabled = true) {
  return useQuery<CookieScoreResponse>({
    queryKey:        scoreKeys.breakdown(platform ?? ''),
    queryFn:         () => adminFetch<CookieScoreResponse>(`/cookies/scores/${platform}`),
    enabled:         enabled && !!platform,
    refetchInterval: 20_000,
    staleTime:       10_000,
  })
}

/** Recent selection history for a platform (30s refresh). */
export function useCookieSelectionHistory(platform: string | null) {
  return useQuery<SelectionHistoryResponse>({
    queryKey:        scoreKeys.history(platform ?? ''),
    queryFn:         () => adminFetch<SelectionHistoryResponse>(`/cookies/selection-history/${platform}`),
    enabled:         !!platform,
    refetchInterval: 30_000,
  })
}

/** Set manual priority weight for a cookie. */
export function useSetCookiePriority(platform: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ hash, weight }: { hash: string; weight: number }) =>
      adminPost(`/cookies/priority/${platform}/${hash}`, { weight }),
    onSuccess: () => qc.invalidateQueries({ queryKey: scoreKeys.breakdown(platform) }),
  })
}

/** Toggle admin disable/enable a cookie from scoring (not a hard-block). */
export function useToggleCookieDisabled(platform: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ hash, disable }: { hash: string; disable: boolean }) => {
      const url = `/cookies/disable/${platform}/${hash}`
      return disable ? adminPost(url, {}) : adminFetch(url, { method: 'DELETE' })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: scoreKeys.breakdown(platform) })
    },
  })
}

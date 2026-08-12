import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { adminKeys } from '../lib/queryKeys'
import {
  fetchPlatformHealth,
  fetchPlatformDetail,
  postCircuitAction,
  type PlatformHealthResponse,
} from '../api/platforms'
import type { PlatformDetail } from '../panels/platforms/platform.types'

export function useAdminPlatformHealth(refetchIntervalMs = 30_000) {
  return useQuery<PlatformHealthResponse>({
    queryKey:        adminKeys.platformHealth(),
    queryFn:         fetchPlatformHealth,
    refetchInterval: refetchIntervalMs,
  })
}

export function useAdminPlatformDetail(platform: string | null) {
  return useQuery<PlatformDetail>({
    queryKey: adminKeys.platformDetail(platform ?? ''),
    queryFn:  () => fetchPlatformDetail(platform!),
    enabled:  !!platform,
    staleTime: 10_000,
  })
}

export function useAdminCircuitAction() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ platform, action }: { platform: string; action: string }) =>
      postCircuitAction(platform, action),
    onSuccess: (_data, { platform }) => {
      qc.invalidateQueries({ queryKey: adminKeys.platformHealth() })
      qc.invalidateQueries({ queryKey: adminKeys.platformDetail(platform) })
    },
  })
}

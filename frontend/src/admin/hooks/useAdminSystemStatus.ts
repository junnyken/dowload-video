import { useQuery } from '@tanstack/react-query'
import { adminKeys } from '../lib/queryKeys'
import { fetchSystemSnapshot, type SystemSnapshot } from '../api/system'

export function useAdminSystemStatus(refetchIntervalMs = 60_000) {
  return useQuery<SystemSnapshot>({
    queryKey:       adminKeys.systemStatus(),
    queryFn:        fetchSystemSnapshot,
    refetchInterval: refetchIntervalMs,
  })
}

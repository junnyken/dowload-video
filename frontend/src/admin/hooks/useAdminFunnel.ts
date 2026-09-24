import { useQuery } from '@tanstack/react-query'
import { fetchFunnel, type FunnelResponse } from '../api/funnel'

export function useAdminFunnel(days: number) {
  return useQuery<FunnelResponse>({
    queryKey:        ['admin', 'funnel', days],
    queryFn:         () => fetchFunnel(days),
    refetchInterval: 120_000,
  })
}

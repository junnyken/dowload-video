import { useQuery } from '@tanstack/react-query'
import { adminKeys } from '../lib/queryKeys'
import { fetchActiveJobs, type ActiveJobsResponse } from '../api/jobs'

export function useAdminActiveJobs(refetchIntervalMs = 15_000) {
  return useQuery<ActiveJobsResponse>({
    queryKey:        adminKeys.activeJobs(),
    queryFn:         fetchActiveJobs,
    refetchInterval: refetchIntervalMs,
  })
}

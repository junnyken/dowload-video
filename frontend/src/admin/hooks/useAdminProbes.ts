import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { fetchProbes, setProbeTarget, runProbesNow, type ProbesResponse } from '../api/probes'

const KEY = ['admin', 'probes']

export function useAdminProbes() {
  return useQuery<ProbesResponse>({ queryKey: KEY, queryFn: fetchProbes, refetchInterval: 60_000 })
}

export function useSetProbeTarget() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ platform, url }: { platform: string; url: string | null }) =>
      setProbeTarget(platform, url),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  })
}

export function useRunProbes() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: runProbesNow,
    // The first version refetched once after 8 seconds, which was wrong twice
    // over: each platform can take up to EXTRACTION_TIMEOUT_SECONDS=30, so a
    // sweep runs far longer than that, and a single early refetch showed the
    // OLD numbers — indistinguishable from the button doing nothing. Poll
    // instead, for long enough to outlast a real sweep.
    onSuccess: () => {
      let n = 0
      const t = setInterval(() => {
        n += 1
        qc.invalidateQueries({ queryKey: KEY })
        if (n >= 15) clearInterval(t)   // 15 × 6s = 90s
      }, 6000)
    },
  })
}

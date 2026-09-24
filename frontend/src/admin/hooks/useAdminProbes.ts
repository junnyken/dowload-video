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
    // The sweep runs on a worker; give it a moment before asking for results.
    onSuccess: () => setTimeout(() => qc.invalidateQueries({ queryKey: KEY }), 8000),
  })
}

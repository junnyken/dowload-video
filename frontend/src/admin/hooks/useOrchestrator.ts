import { useQuery } from '@tanstack/react-query'
import {
  fetchLaneStates, fetchThroughput, fetchFairness,
  fetchOrchestratorSummary, fetchCookieScores,
} from '../api/orchestrator'
import type { OrchestratorSummaryResponse } from '../types/orchestrator'

export function useOrchestratorSummary(refetchMs = 15_000) {
  return useQuery<OrchestratorSummaryResponse>({
    queryKey:        ['admin', 'orchestrator', 'summary'],
    queryFn:         fetchOrchestratorSummary,
    refetchInterval: refetchMs,
  })
}

export function useOrchestratorLanes(refetchMs = 10_000) {
  return useQuery({
    queryKey:        ['admin', 'orchestrator', 'lanes'],
    queryFn:         fetchLaneStates,
    refetchInterval: refetchMs,
  })
}

export function useOrchestratorThroughput(refetchMs = 15_000) {
  return useQuery({
    queryKey:        ['admin', 'orchestrator', 'throughput'],
    queryFn:         fetchThroughput,
    refetchInterval: refetchMs,
  })
}

export function useOrchestratorFairness(refetchMs = 30_000) {
  return useQuery({
    queryKey:        ['admin', 'orchestrator', 'fairness'],
    queryFn:         fetchFairness,
    refetchInterval: refetchMs,
  })
}

export function useCookieScores(platform: string, enabled = true) {
  return useQuery({
    queryKey:        ['admin', 'orchestrator', 'cookie-scores', platform],
    queryFn:         () => fetchCookieScores(platform),
    enabled:         enabled && !!platform,
    refetchInterval: 30_000,
  })
}

import { adminFetch } from '../utils/adminFetch'
import type {
  LanesResponse, ThroughputResponse, FairnessResponse,
  CookieScoresResponse, OrchestratorSummaryResponse,
} from '../types/orchestrator'

export const fetchLaneStates    = () => adminFetch<LanesResponse>('/orchestrator/lanes')
export const fetchThroughput    = () => adminFetch<ThroughputResponse>('/orchestrator/throughput')
export const fetchFairness      = () => adminFetch<FairnessResponse>('/orchestrator/fairness')
export const fetchOrchestratorSummary = () => adminFetch<OrchestratorSummaryResponse>('/orchestrator/summary')
export const fetchCookieScores  = (platform: string) =>
  adminFetch<CookieScoresResponse>(`/orchestrator/cookie-scores/${platform}`)

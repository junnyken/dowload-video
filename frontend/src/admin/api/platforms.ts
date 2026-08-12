import { adminFetch, adminPost } from '../utils/adminFetch'
import type { PlatformHealthRow, PlatformDetail } from '../panels/platforms/platform.types'

export interface PlatformHealthResponse {
  platforms: Array<{
    platform: string
    status: PlatformHealthRow['status']
    circuitState: PlatformHealthRow['circuitState']
    lastSuccessAt: string
    failRate1h: number
    totalJobs1h: number
    activeJobs: number
    cookieRequired: boolean
    proxyRequired: boolean
  }>
}

export function fetchPlatformHealth(): Promise<PlatformHealthResponse> {
  return adminFetch<PlatformHealthResponse>('/platforms/health')
}

export function fetchPlatformDetail(platform: string): Promise<PlatformDetail> {
  return adminFetch<PlatformDetail>(`/platforms/${platform}/detail`)
}

export function postCircuitAction(platform: string, action: string): Promise<unknown> {
  return adminPost(`/platforms/${platform}/circuit`, { action })
}

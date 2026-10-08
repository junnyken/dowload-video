import { adminFetch, adminPost } from '../utils/adminFetch'

export interface ScraperKey {
  index: number
  key_hash: string
  key_prefix: string
  credits: number | null
  active: boolean
  exhausted: boolean
}

export interface ScraperKeysResponse {
  success: boolean
  keys: ScraperKey[]
  total_credits: number
  key_count: number
}

export interface ScraperRefreshResponse {
  success: boolean
  keys: ScraperKey[]
  total_credits: number
}

export interface ScraperAddResponse {
  success: boolean
  pool_size: number
  credits: number
  key_prefix: string
}

export interface ScraperRemoveResponse {
  success: boolean
  pool_size: number
}

export interface ScraperRemoveAllResponse {
  success: boolean
  removed: number
  pool_size: number
}

export function fetchScraperKeys(): Promise<ScraperKeysResponse> {
  return adminFetch<ScraperKeysResponse>('/scraperapi/keys')
}

export function refreshScraperCredits(): Promise<ScraperRefreshResponse> {
  return adminPost<ScraperRefreshResponse>('/scraperapi/refresh-credits')
}

export function addScraperKey(key: string): Promise<ScraperAddResponse> {
  return adminPost<ScraperAddResponse>('/scraperapi/keys/add', { key })
}

export function removeScraperKey(index: number): Promise<ScraperRemoveResponse> {
  return adminFetch<ScraperRemoveResponse>(`/scraperapi/keys/remove?index=${encodeURIComponent(String(index))}`, {
    method: 'DELETE',
  })
}

export function removeAllScraperKeys(): Promise<ScraperRemoveAllResponse> {
  return adminFetch<ScraperRemoveAllResponse>('/scraperapi/keys', { method: 'DELETE' })
}

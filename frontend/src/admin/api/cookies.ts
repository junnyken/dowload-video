import { adminFetch, adminPost } from '../utils/adminFetch'

export interface ExpiryCookieEntry {
  index: number
  hash: string
  label: string
  account_hint: string
  expires_at: number
  expires_at_str: string | null
  days_left: number | null
  expiry_status: string
  health_status: string
  blocked: boolean
  blocked_ttl_s: number | null
  cooldown_ttl_s: number | null
  last_used: number
  added_at?: number
  last_test_at?: number
  last_test_status?: string
  last_test_message?: string
  verified_ok_at?: number
}

export interface CookieListResponse {
  success: boolean
  platform: string
  total: number
  cookies: ExpiryCookieEntry[]
}

export interface CookieRemoveResponse {
  success: boolean
  platform: string
  pool_size: number
}

export interface CookieAddResponse {
  success: boolean
  platform: string
  pool_size: number
}

export function fetchCookieList(platform: string): Promise<CookieListResponse> {
  return adminFetch<CookieListResponse>(`/cookies/list/${platform}`)
}

export function deleteCookie(platform: string, index: number): Promise<CookieRemoveResponse> {
  return adminFetch<CookieRemoveResponse>('/cookies/remove', {
    method: 'DELETE',
    body: JSON.stringify({ platform, index }),
  })
}

export interface CookieRemoveBatchResponse {
  success: boolean
  platform: string
  removed: string[]
  missing: string[]
  pool_size: number
}

/** Xoá nhiều cookie một lần theo hash (không bao giờ gửi giá trị cookie). */
export function removeCookiesBatch(platform: string, hashes: string[]): Promise<CookieRemoveBatchResponse> {
  return adminPost<CookieRemoveBatchResponse>('/cookies/remove-batch', { platform, hashes })
}

export function addCookie(
  platform: string,
  rawCookie: string,
  label?: string,
): Promise<CookieAddResponse> {
  const cookieBytes = new TextEncoder().encode(rawCookie)
  const cookies_b64 = btoa(Array.from(cookieBytes, b => String.fromCharCode(b)).join(''))
  return adminPost<CookieAddResponse>('/cookies/add', { platform, cookies_b64, label })
}

export interface CookiePlatformSummary {
  total: number
  healthy: number
  in_cooldown: number
  soft_blocked: number
  hard_blocked: number
  expiry_warnings: number
  min_days_left: number | null
}

export interface CookieStatusResponse {
  success: boolean
  pools: Record<string, CookiePlatformSummary>
}

export function fetchCookieStatus(): Promise<CookieStatusResponse> {
  return adminFetch<CookieStatusResponse>('/cookies/status')
}

export interface TestCookieResponse {
  ok: boolean
  message: string
  found_keys?: string[]
  missing_keys?: string[]
  expires_at_str?: string | null
}

export function testCookieApi(platform: string, rawCookie: string): Promise<TestCookieResponse> {
  const cookieBytes = new TextEncoder().encode(rawCookie)
  const cookies_b64 = btoa(Array.from(cookieBytes, b => String.fromCharCode(b)).join(''))
  return adminPost<TestCookieResponse>('/cookies/test', { platform, cookies_b64 })
}

export function triggerCookieHealthCheck(): Promise<unknown> {
  return adminPost('/cookies/check-expiry')
}

// ─── Live re-test + storage status ─────────────────────────────────────────────

export type RetestStatus =
  | 'ok' | 'rejected' | 'inconclusive' | 'unsupported'
  | 'skipped_disabled' | 'not_found' | 'rate_limited'

export interface RetestResponse {
  success: boolean
  platform: string
  hash: string
  status: RetestStatus
  message: string
  cleared_expired?: boolean
  reason?: string
  retry_after_s?: number
}

/** Takes the cookie's hash, never its value. */
export function retestCookie(platform: string, hash: string): Promise<RetestResponse> {
  return adminPost<RetestResponse>(`/cookies/retest/${platform}/${hash}`)
}

export interface RetestInfoResponse {
  success: boolean
  supported_platforms: string[]
  limits: {
    per_cookie_cooldown_s: number
    per_platform_gap_s: number
    hourly_cap: number
    batch_cap: number
  }
}

export function fetchRetestInfo(): Promise<RetestInfoResponse> {
  return adminFetch<RetestInfoResponse>('/cookies/retest-info')
}

export interface StorageStatusResponse {
  success: boolean
  store: string
  readable: boolean
  total_cookies: number | null
  message: string
  aof_enabled?: boolean
  rdb_last_save_time?: number | null
  rdb_last_bgsave_status?: string | null
  aof_last_write_status?: string | null
}

export function fetchStorageStatus(): Promise<StorageStatusResponse> {
  return adminFetch<StorageStatusResponse>('/cookies/storage-status')
}

/** Real pool-level disable / re-enable (persisted in Redis). */
export function setCookieDisabled(platform: string, hash: string, disabled: boolean): Promise<unknown> {
  const path = `/cookies/pool-state/${platform}/${hash}/disable`
  return disabled
    ? adminPost(path, { reason: 'Tắt thủ công từ trang Cookies' })
    : adminFetch(path, { method: 'DELETE' })
}

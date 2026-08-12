import type { PlatformHealthRow, PlatformDetail } from './platform.types'

// ─── Table rows ────────────────────────────────────────────────────────────────

export const MOCK_PLATFORM_ROWS: PlatformHealthRow[] = [
  { platform: 'youtube',    status: 'critical', circuitState: 'open',   lastSuccessAt: '3h ago',  failRate1h: 100, cookieRequired: true,  proxyRequired: true,  totalJobs1h: 42, activeJobs: 0 },
  { platform: 'instagram',  status: 'warning',  circuitState: 'half',   lastSuccessAt: '8m ago',  failRate1h: 34,  cookieRequired: true,  proxyRequired: false, totalJobs1h: 29, activeJobs: 2 },
  { platform: 'twitter',    status: 'warning',  circuitState: 'half',   lastSuccessAt: '22m ago', failRate1h: 23,  cookieRequired: false, proxyRequired: false, totalJobs1h: 17, activeJobs: 1 },
  { platform: 'threads',    status: 'warning',  circuitState: 'closed', lastSuccessAt: '14m ago', failRate1h: 18,  cookieRequired: false, proxyRequired: false, totalJobs1h: 11, activeJobs: 0 },
  { platform: 'tiktok',     status: 'healthy',  circuitState: 'closed', lastSuccessAt: '1m ago',  failRate1h: 2,   cookieRequired: true,  proxyRequired: false, totalJobs1h: 84, activeJobs: 5 },
  { platform: 'facebook',   status: 'healthy',  circuitState: 'closed', lastSuccessAt: '3m ago',  failRate1h: 0,   cookieRequired: true,  proxyRequired: false, totalJobs1h: 31, activeJobs: 3 },
  { platform: 'soundcloud', status: 'healthy',  circuitState: 'closed', lastSuccessAt: '6m ago',  failRate1h: 3,   cookieRequired: false, proxyRequired: false, totalJobs1h: 15, activeJobs: 0 },
  { platform: 'reddit',     status: 'healthy',  circuitState: 'closed', lastSuccessAt: '4m ago',  failRate1h: 1,   cookieRequired: false, proxyRequired: false, totalJobs1h: 12, activeJobs: 1 },
  { platform: 'bilibili',   status: 'healthy',  circuitState: 'closed', lastSuccessAt: '5m ago',  failRate1h: 0,   cookieRequired: false, proxyRequired: false, totalJobs1h: 22, activeJobs: 1 },
  { platform: 'douyin',     status: 'healthy',  circuitState: 'exempt', lastSuccessAt: '2m ago',  failRate1h: 0,   cookieRequired: false, proxyRequired: false, totalJobs1h: 18, activeJobs: 2 },
  { platform: 'pinterest',  status: 'healthy',  circuitState: 'exempt', lastSuccessAt: '11m ago', failRate1h: 0,   cookieRequired: false, proxyRequired: false, totalJobs1h: 8,  activeJobs: 0 },
  { platform: 'vimeo',      status: 'healthy',  circuitState: 'closed', lastSuccessAt: '19m ago', failRate1h: 0,   cookieRequired: false, proxyRequired: false, totalJobs1h: 6,  activeJobs: 0 },
]

// ─── Detail pane data ──────────────────────────────────────────────────────────

const DEFAULT_DETAIL = (platform: string): PlatformDetail => ({
  platform,
  status: 'healthy',
  circuitState: 'closed',
  description: 'Operating normally. No active incidents.',
  recentJobs: [
    { id: 'j1', result: 'success', url: `https://${platform}.com/watch?v=abc`, durationMs: 3200, startedAt: '2m ago' },
    { id: 'j2', result: 'success', url: `https://${platform}.com/watch?v=def`, durationMs: 4100, startedAt: '5m ago' },
    { id: 'j3', result: 'success', url: `https://${platform}.com/watch?v=ghi`, durationMs: 2800, startedAt: '9m ago' },
  ],
  errorBreakdown: [
    { errorType: 'content_unavailable', count: 1, pct: 100 },
  ],
  phaseStats: [
    { phase: 'resolve',   successRate: 100, avgDurationMs: 420,  totalJobs: 12 },
    { phase: 'extract',   successRate: 99,  avgDurationMs: 780,  totalJobs: 12 },
    { phase: 'download',  successRate: 99,  avgDurationMs: 3800, totalJobs: 12 },
  ],
  config: {
    rateLimit: '60 req/min',
    cookiePool: 'Not required',
    proxy: 'iproyal (rotate)',
    retryPolicy: '3 retries, exp backoff',
  },
})

export const MOCK_PLATFORM_DETAIL: Record<string, PlatformDetail> = {
  youtube: {
    platform: 'youtube',
    status: 'critical',
    circuitState: 'open',
    description: 'Oracle VPS IP range (AS31898) blocked by YouTube bot detection. Set YOUTUBE_ENABLED=true + YTDL_PROXY to restore. Circuit breaker open — all requests fail-fast.',
    recentJobs: [
      { id: 'j1', result: 'failed', url: 'https://youtube.com/watch?v=dQw4w9WgXcQ', durationMs: 1200, startedAt: '5m ago',  phase: 'extract', error: 'IP blocked: AS31898 — YouTube rejected datacenter IP' },
      { id: 'j2', result: 'failed', url: 'https://youtube.com/watch?v=abc123',       durationMs: 1100, startedAt: '8m ago',  phase: 'extract', error: 'IP blocked: AS31898' },
      { id: 'j3', result: 'failed', url: 'https://youtube.com/shorts/xyz789',        durationMs: 900,  startedAt: '15m ago', phase: 'resolve', error: 'Bot detection triggered' },
      { id: 'j4', result: 'failed', url: 'https://youtube.com/watch?v=mn0456',       durationMs: 1050, startedAt: '21m ago', phase: 'extract', error: 'IP blocked: AS31898' },
      { id: 'j5', result: 'failed', url: 'https://youtube.com/watch?v=pq7890',       durationMs: 980,  startedAt: '34m ago', phase: 'extract', error: 'IP blocked: AS31898' },
    ],
    errorBreakdown: [
      { errorType: 'ip_blocked',      count: 38, pct: 90 },
      { errorType: 'bot_detection',   count: 4,  pct: 10 },
    ],
    phaseStats: [
      { phase: 'resolve',   successRate: 0,  avgDurationMs: 900,  totalJobs: 42 },
      { phase: 'extract',   successRate: 0,  avgDurationMs: 1100, totalJobs: 42 },
      { phase: 'download',  successRate: 0,  avgDurationMs: 0,    totalJobs: 0  },
    ],
    config: {
      rateLimit: 'N/A — platform disabled',
      cookiePool: 'yt_pool_1 (3 accounts — unused)',
      proxy: 'Not configured (YTDL_PROXY unset)',
      retryPolicy: '0 retries — circuit open',
    },
  },
  instagram: {
    platform: 'instagram',
    status: 'warning',
    circuitState: 'half',
    description: 'Cookie pool degraded — 3 of 5 accounts soft-blocked. Circuit in HALF-OPEN state, auto-recovering. Rotating to unblocked accounts.',
    recentJobs: [
      { id: 'j4', result: 'success', url: 'https://instagram.com/p/abc123', durationMs: 4200, startedAt: '8m ago' },
      { id: 'j5', result: 'failed',  url: 'https://instagram.com/p/def456', durationMs: 1800, startedAt: '12m ago', phase: 'auth', error: 'Cookie soft-block: account_id:8821' },
      { id: 'j6', result: 'success', url: 'https://instagram.com/reel/ghi789', durationMs: 6100, startedAt: '15m ago' },
      { id: 'j7', result: 'failed',  url: 'https://instagram.com/p/jkl012', durationMs: 1500, startedAt: '18m ago', phase: 'auth', error: 'Cookie soft-block: account_id:4412' },
      { id: 'j8', result: 'success', url: 'https://instagram.com/p/mno345', durationMs: 3800, startedAt: '25m ago' },
    ],
    errorBreakdown: [
      { errorType: 'cookie_soft_block', count: 8, pct: 72 },
      { errorType: 'rate_limited',       count: 2, pct: 18 },
      { errorType: 'content_removed',    count: 1, pct: 10 },
    ],
    phaseStats: [
      { phase: 'resolve',   successRate: 100, avgDurationMs: 380,  totalJobs: 29 },
      { phase: 'auth',      successRate: 66,  avgDurationMs: 1500, totalJobs: 29 },
      { phase: 'extract',   successRate: 85,  avgDurationMs: 2200, totalJobs: 19 },
      { phase: 'download',  successRate: 95,  avgDurationMs: 8500, totalJobs: 16 },
    ],
    config: {
      rateLimit: '30 req/min',
      cookiePool: 'ig_pool_1 (5 accounts — 2 active)',
      proxy: 'iproyal (rotate)',
      retryPolicy: '2 retries, 30s delay',
    },
  },
  tiktok: {
    platform: 'tiktok',
    status: 'healthy',
    circuitState: 'closed',
    description: 'Operating normally via TikWM API. No cookie or proxy required for most content.',
    recentJobs: [
      { id: 'j9',  result: 'success', url: 'https://tiktok.com/@user1/video/7123', durationMs: 3100, startedAt: '1m ago'  },
      { id: 'j10', result: 'success', url: 'https://tiktok.com/@user2/video/7456', durationMs: 2800, startedAt: '2m ago'  },
      { id: 'j11', result: 'success', url: 'https://tiktok.com/@user3/video/7789', durationMs: 4200, startedAt: '3m ago'  },
      { id: 'j12', result: 'success', url: 'https://tiktok.com/@user4/video/7012', durationMs: 3600, startedAt: '5m ago'  },
      { id: 'j13', result: 'failed',  url: 'https://tiktok.com/@user5/video/7345', durationMs: 800,  startedAt: '11m ago', phase: 'resolve', error: 'Content removed by author' },
    ],
    errorBreakdown: [
      { errorType: 'content_unavailable', count: 2, pct: 100 },
    ],
    phaseStats: [
      { phase: 'resolve',  successRate: 100, avgDurationMs: 420,  totalJobs: 84 },
      { phase: 'extract',  successRate: 98,  avgDurationMs: 780,  totalJobs: 84 },
      { phase: 'download', successRate: 97,  avgDurationMs: 4200, totalJobs: 82 },
    ],
    config: {
      rateLimit: '60 req/min (TikWM)',
      cookiePool: 'Not required — TikWM API',
      proxy: 'iproyal (rotate)',
      retryPolicy: '3 retries, 15s delay',
    },
  },
}

export function getPlatformDetail(platform: string): PlatformDetail {
  return (
    MOCK_PLATFORM_DETAIL[platform] ??
    DEFAULT_DETAIL(platform)
  )
}

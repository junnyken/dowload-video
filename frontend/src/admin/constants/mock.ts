import type {
  PlatformHealthRow,
  CookieItem,
  JobTracePhase,
  SystemAlert,
  HealthPanelData,
  RecentFailure,
} from '../types/admin.types'

export const MOCK_ALERTS: SystemAlert[] = [
  {
    id: 'a1',
    severity: 'critical',
    title: 'YouTube circuit OPEN',
    message: 'Oracle VPS IP (AS31898) bot-blocked. Single video downloads disabled.',
    timestamp: '2026-06-30T09:45:00Z',
    dismissible: false,
  },
  {
    id: 'a2',
    severity: 'warning',
    title: 'Instagram cookie pool low',
    message: '1 of 2 cookies soft-blocked. Pool at 50% capacity.',
    timestamp: '2026-06-30T09:50:00Z',
    dismissible: true,
  },
  {
    id: 'a3',
    severity: 'info',
    title: 'yt-dlp updated',
    message: 'Auto-updated to 2025.6.29 at 03:00 UTC.',
    timestamp: '2026-06-30T03:02:00Z',
    dismissible: true,
  },
]

export const MOCK_HEALTH_PANELS: HealthPanelData[] = [
  { id: 'platforms', title: 'Platform Health', icon: '🔌', status: 'warn',  primaryMetric: '18 / 20 OK',        secondaryMetric: '1 circuit OPEN · 1 disabled',  lastUpdated: '2026-06-30T09:59:00Z', href: '/vid-admin/platforms' },
  { id: 'cookies',   title: 'Cookie Pool',     icon: '🍪', status: 'warn',  primaryMetric: '3 active',           secondaryMetric: '1 soft-blocked · 1 expired',   lastUpdated: '2026-06-30T09:58:00Z', href: '/vid-admin/cookies'   },
  { id: 'proxy',     title: 'Proxy Health',    icon: '🌐', status: 'ok',    primaryMetric: 'Proxying.io OK',     secondaryMetric: '180 MB used today',             lastUpdated: '2026-06-30T09:59:45Z', href: '/vid-admin/proxy'     },
  { id: 'queue',     title: 'Queue',           icon: '⚙️', status: 'ok',    primaryMetric: '3 / 8 workers busy', secondaryMetric: '12 pending · 0 stuck',          lastUpdated: '2026-06-30T09:59:58Z', href: '/vid-admin/queue'     },
]

// failRate1h is a 0–100 percentage
export const MOCK_PLATFORMS: PlatformHealthRow[] = [
  { platform: 'tiktok',     displayName: 'TikTok',      icon: '🎵', status: 'ok',    circuitState: 'exempt', lastSuccessAt: '2026-06-30T09:58:12Z', failRate1h: 2,   totalJobs1h: 143, cookieRequired: false, proxyRequired: false, enabled: true  },
  { platform: 'douyin',     displayName: 'Douyin',      icon: '🎶', status: 'ok',    circuitState: 'exempt', lastSuccessAt: '2026-06-30T09:57:44Z', failRate1h: 5,   totalJobs1h: 28,  cookieRequired: false, proxyRequired: true,  enabled: true  },
  { platform: 'instagram',  displayName: 'Instagram',   icon: '📸', status: 'warn',  circuitState: 'half',   lastSuccessAt: '2026-06-30T09:45:00Z', failRate1h: 32,  totalJobs1h: 25,  cookieRequired: true,  proxyRequired: false, enabled: true  },
  { platform: 'youtube',    displayName: 'YouTube',     icon: '▶️', status: 'error', circuitState: 'open',   lastSuccessAt: '2026-06-28T14:22:00Z', failRate1h: 100, totalJobs1h: 0,   cookieRequired: false, proxyRequired: true,  enabled: false },
  { platform: 'facebook',   displayName: 'Facebook',    icon: '👤', status: 'ok',    circuitState: 'closed', lastSuccessAt: '2026-06-30T09:56:00Z', failRate1h: 3,   totalJobs1h: 17,  cookieRequired: false, proxyRequired: false, enabled: true  },
  { platform: 'threads',    displayName: 'Threads',     icon: '🧵', status: 'ok',    circuitState: 'closed', lastSuccessAt: '2026-06-30T09:55:30Z', failRate1h: 0,   totalJobs1h: 9,   cookieRequired: false, proxyRequired: false, enabled: true  },
  { platform: 'spotify',    displayName: 'Spotify',     icon: '🎧', status: 'ok',    circuitState: 'exempt', lastSuccessAt: '2026-06-30T09:59:00Z', failRate1h: 8,   totalJobs1h: 34,  cookieRequired: false, proxyRequired: false, enabled: true  },
  { platform: 'soundcloud', displayName: 'SoundCloud',  icon: '☁️', status: 'ok',    circuitState: 'exempt', lastSuccessAt: '2026-06-30T09:58:40Z', failRate1h: 1,   totalJobs1h: 22,  cookieRequired: false, proxyRequired: false, enabled: true  },
  { platform: 'bilibili',   displayName: 'Bilibili',    icon: '📺', status: 'ok',    circuitState: 'closed', lastSuccessAt: '2026-06-30T09:50:00Z', failRate1h: 0,   totalJobs1h: 5,   cookieRequired: true,  proxyRequired: false, enabled: true  },
  { platform: 'twitter',    displayName: 'Twitter / X', icon: '✖️', status: 'warn',  circuitState: 'closed', lastSuccessAt: '2026-06-30T08:30:00Z', failRate1h: 45,  totalJobs1h: 2,   cookieRequired: true,  proxyRequired: false, enabled: true  },
  { platform: 'pinterest',  displayName: 'Pinterest',   icon: '📌', status: 'ok',    circuitState: 'exempt', lastSuccessAt: '2026-06-30T09:54:00Z', failRate1h: 0,   totalJobs1h: 11,  cookieRequired: false, proxyRequired: false, enabled: true  },
  { platform: 'reddit',     displayName: 'Reddit',      icon: '🤖', status: 'ok',    circuitState: 'closed', lastSuccessAt: '2026-06-30T09:52:00Z', failRate1h: 5,   totalJobs1h: 7,   cookieRequired: false, proxyRequired: false, enabled: true  },
]

// cooldownRemainingSec is in seconds (0 = no cooldown)
export const MOCK_COOKIES: CookieItem[] = [
  { id: 'ig_main_2026',   platform: 'instagram', accountLabel: 'ig_account_1',   status: 'active',       healthScore: 72, lastSuccessAt: '2026-06-30T09:45:00Z', lastFailAt: '2026-06-30T09:30:00Z', failCount: 2,  cooldownRemainingSec: 0,    expiryEstimate: '2026-09-15T00:00:00Z' },
  { id: 'ig_backup_2026', platform: 'instagram', accountLabel: 'ig_account_2',   status: 'soft_blocked', healthScore: 30, lastSuccessAt: '2026-06-30T09:00:00Z', lastFailAt: '2026-06-30T09:35:00Z', failCount: 7,  cooldownRemainingSec: 540,  expiryEstimate: '2026-10-01T00:00:00Z' },
  { id: 'tw_main_2026',   platform: 'twitter',   accountLabel: 'tw_account_1',   status: 'active',       healthScore: 55, lastSuccessAt: '2026-06-30T08:30:00Z', lastFailAt: '2026-06-30T07:45:00Z', failCount: 4,  cooldownRemainingSec: 0,    expiryEstimate: null                   },
  { id: 'yt_main_2026',   platform: 'youtube',   accountLabel: 'yt_account_1',   status: 'hard_blocked', healthScore: 0,  lastSuccessAt: '2026-06-28T14:00:00Z', lastFailAt: '2026-06-30T06:00:00Z', failCount: 15, cooldownRemainingSec: 12600, expiryEstimate: '2026-12-01T00:00:00Z' },
  { id: 'bili_main_2026', platform: 'bilibili',  accountLabel: 'bili_account_1', status: 'active',       healthScore: 95, lastSuccessAt: '2026-06-30T09:50:00Z', lastFailAt: null,                   failCount: 0,  cooldownRemainingSec: 0,    expiryEstimate: '2027-01-01T00:00:00Z' },
]

export const MOCK_JOB_TRACE: JobTracePhase[] = [
  { phase: 'queued',       status: 'success', startedAt: '2026-06-30T09:58:00.000Z', endedAt: '2026-06-30T09:58:00.120Z', durationMs: 120,  proxyUsed: null,           cookieUsed: null,          errorMessage: null                                                  },
  { phase: 'resolving',    status: 'success', startedAt: '2026-06-30T09:58:00.120Z', endedAt: '2026-06-30T09:58:00.340Z', durationMs: 220,  proxyUsed: null,           cookieUsed: null,          errorMessage: null                                                  },
  { phase: 'metadata',     status: 'success', startedAt: '2026-06-30T09:58:00.340Z', endedAt: '2026-06-30T09:58:02.665Z', durationMs: 2325, proxyUsed: 'proxying_io',  cookieUsed: null,          errorMessage: null                                                  },
  { phase: 'auth',         status: 'failed',  startedAt: '2026-06-30T09:58:02.665Z', endedAt: '2026-06-30T09:58:03.115Z', durationMs: 450,  proxyUsed: null,           cookieUsed: 'ig_main_2026', errorMessage: 'Cookie session expired or account logged out (401)' },
  { phase: 'download',     status: 'skipped', startedAt: null, endedAt: null, durationMs: null, proxyUsed: null, cookieUsed: null, errorMessage: null },
  { phase: 'post-process', status: 'skipped', startedAt: null, endedAt: null, durationMs: null, proxyUsed: null, cookieUsed: null, errorMessage: null },
  { phase: 'done',         status: 'skipped', startedAt: null, endedAt: null, durationMs: null, proxyUsed: null, cookieUsed: null, errorMessage: null },
]

export const MOCK_RECENT_FAILURES: RecentFailure[] = [
  { jobId: 'job-uuid-001', platform: 'instagram', url: 'https://www.instagram.com/p/abc123/',    errorType: 'login_required', failedAt: '2026-06-30T09:58:03Z', phase: 'auth'     },
  { jobId: 'job-uuid-002', platform: 'youtube',   url: 'https://youtube.com/watch?v=xyz789',     errorType: 'bot_detected',   failedAt: '2026-06-30T09:50:11Z', phase: 'metadata' },
  { jobId: 'job-uuid-003', platform: 'twitter',   url: 'https://x.com/user/status/123',          errorType: '429',            failedAt: '2026-06-30T09:44:30Z', phase: 'download' },
  { jobId: 'job-uuid-004', platform: 'instagram', url: 'https://www.instagram.com/reel/def456/', errorType: 'login_required', failedAt: '2026-06-30T09:35:22Z', phase: 'auth'     },
  { jobId: 'job-uuid-005', platform: 'bilibili',  url: 'https://www.bilibili.com/video/BV1xx/',  errorType: 'timeout',        failedAt: '2026-06-30T09:20:05Z', phase: 'download' },
]

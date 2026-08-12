import type { JobInspectorData } from './job.types'

export const MOCK_JOBS: JobInspectorData[] = [
  // ── YouTube: failed at auth (cookie mismatch) ─────────────────────────────
  {
    jobId:         'job_yt_3f8a2b',
    batchId:       'batch_morning_run_01',
    platform:      'youtube',
    url:           'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
    userIp:        '103.138.88.100',
    currentStatus: 'failed',
    createdAt:     '10:23:01',
    updatedAt:     '10:23:04',
    traces: [
      {
        phase: 'queued',   status: 'success',
        startedAt: '10:23:01.000', endedAt: '10:23:01.012', durationMs: 12,
        proxyUsed: null, cookieUsed: null, errorMessage: null,
      },
      {
        phase: 'resolving', status: 'success',
        startedAt: '10:23:01.012', endedAt: '10:23:01.352', durationMs: 340,
        proxyUsed: '145.131.xx.xx:8823', cookieUsed: null, errorMessage: null,
      },
      {
        phase: 'metadata', status: 'success',
        startedAt: '10:23:01.352', endedAt: '10:23:02.242', durationMs: 890,
        proxyUsed: '145.131.xx.xx:8823', cookieUsed: null, errorMessage: null,
      },
      {
        phase: 'auth', status: 'failed',
        startedAt: '10:23:02.242', endedAt: '10:23:02.362', durationMs: 120,
        proxyUsed: null, cookieUsed: 'yt_main_2024',
        errorMessage: '403 Forbidden: Cookie rejected by YouTube (VISITOR_INFO1_LIVE mismatch — session token stale, retry limit 3/3 exceeded)',
      },
      {
        phase: 'download',     status: 'skipped',
        startedAt: null, endedAt: null, durationMs: null,
        proxyUsed: null, cookieUsed: null, errorMessage: null,
      },
      {
        phase: 'post-process', status: 'skipped',
        startedAt: null, endedAt: null, durationMs: null,
        proxyUsed: null, cookieUsed: null, errorMessage: null,
      },
      {
        phase: 'done',         status: 'skipped',
        startedAt: null, endedAt: null, durationMs: null,
        proxyUsed: null, cookieUsed: null, errorMessage: null,
      },
    ],
    summary: {
      totalPhases: 7, completedPhases: 3, failedPhases: 1,
      totalDurationMs: 1362,
      lastError: '403 Forbidden: Cookie rejected by YouTube',
    },
    recentErrors: [
      '403 Forbidden: Cookie rejected (VISITOR_INFO1_LIVE mismatch)',
      'Auth retry 1/3 — 401 Unauthorized',
      'Auth retry 2/3 — 403 Forbidden',
      'Auth retry 3/3 — 403 Forbidden: retry limit reached',
    ],
  },

  // ── Instagram: currently running (download phase) ─────────────────────────
  {
    jobId:         'job_ig_7c9d1e',
    batchId:       null,
    platform:      'instagram',
    url:           'https://www.instagram.com/reel/CxyzABC123/',
    userIp:        '192.168.1.55',
    currentStatus: 'running',
    createdAt:     '10:24:12',
    updatedAt:     '10:24:15',
    traces: [
      {
        phase: 'queued',   status: 'success',
        startedAt: '10:24:12.000', endedAt: '10:24:12.008', durationMs: 8,
        proxyUsed: null, cookieUsed: null, errorMessage: null,
      },
      {
        phase: 'resolving', status: 'success',
        startedAt: '10:24:12.008', endedAt: '10:24:12.290', durationMs: 282,
        proxyUsed: '145.131.xx.xx:8823', cookieUsed: null, errorMessage: null,
      },
      {
        phase: 'metadata', status: 'success',
        startedAt: '10:24:12.290', endedAt: '10:24:13.101', durationMs: 811,
        proxyUsed: '145.131.xx.xx:8823', cookieUsed: 'ig_personal_01', errorMessage: null,
      },
      {
        phase: 'auth', status: 'success',
        startedAt: '10:24:13.101', endedAt: '10:24:13.388', durationMs: 287,
        proxyUsed: null, cookieUsed: 'ig_personal_01', errorMessage: null,
      },
      {
        phase: 'download', status: 'running',
        startedAt: '10:24:13.388', endedAt: null, durationMs: null,
        proxyUsed: '145.131.xx.xx:8823', cookieUsed: 'ig_personal_01', errorMessage: null,
      },
      {
        phase: 'post-process', status: 'pending',
        startedAt: null, endedAt: null, durationMs: null,
        proxyUsed: null, cookieUsed: null, errorMessage: null,
      },
      {
        phase: 'done',         status: 'pending',
        startedAt: null, endedAt: null, durationMs: null,
        proxyUsed: null, cookieUsed: null, errorMessage: null,
      },
    ],
    summary: {
      totalPhases: 7, completedPhases: 4, failedPhases: 0,
      totalDurationMs: null, lastError: null,
    },
    recentErrors: [],
  },

  // ── TikTok: completed successfully ─────────────────────────────────────────
  {
    jobId:         'job_tt_1a4f8c',
    batchId:       'batch_evening_01',
    platform:      'tiktok',
    url:           'https://www.tiktok.com/@creator/video/7123456789012345678',
    userIp:        '10.0.0.45',
    currentStatus: 'done',
    createdAt:     '10:20:00',
    updatedAt:     '10:20:04',
    traces: [
      {
        phase: 'queued',   status: 'success',
        startedAt: '10:20:00.000', endedAt: '10:20:00.009', durationMs: 9,
        proxyUsed: null, cookieUsed: null, errorMessage: null,
      },
      {
        phase: 'resolving', status: 'success',
        startedAt: '10:20:00.009', endedAt: '10:20:00.271', durationMs: 262,
        proxyUsed: null, cookieUsed: null, errorMessage: null,
      },
      {
        phase: 'metadata', status: 'success',
        startedAt: '10:20:00.271', endedAt: '10:20:00.982', durationMs: 711,
        proxyUsed: null, cookieUsed: 'tt_primary', errorMessage: null,
      },
      {
        phase: 'auth', status: 'success',
        startedAt: '10:20:00.982', endedAt: '10:20:01.104', durationMs: 122,
        proxyUsed: null, cookieUsed: 'tt_primary', errorMessage: null,
      },
      {
        phase: 'download', status: 'success',
        startedAt: '10:20:01.104', endedAt: '10:20:03.891', durationMs: 2787,
        proxyUsed: null, cookieUsed: 'tt_primary', errorMessage: null,
      },
      {
        phase: 'post-process', status: 'success',
        startedAt: '10:20:03.891', endedAt: '10:20:04.122', durationMs: 231,
        proxyUsed: null, cookieUsed: null, errorMessage: null,
      },
      {
        phase: 'done', status: 'success',
        startedAt: '10:20:04.122', endedAt: '10:20:04.123', durationMs: 1,
        proxyUsed: null, cookieUsed: null, errorMessage: null,
      },
    ],
    summary: {
      totalPhases: 7, completedPhases: 7, failedPhases: 0,
      totalDurationMs: 4123, lastError: null,
    },
    recentErrors: [],
  },

  // ── Facebook: stuck in download ────────────────────────────────────────────
  {
    jobId:         'job_fb_2e5c9a',
    batchId:       null,
    platform:      'facebook',
    url:           'https://www.facebook.com/watch/?v=987654321',
    userIp:        '172.16.0.12',
    currentStatus: 'stuck',
    createdAt:     '10:18:05',
    updatedAt:     '10:19:05',
    traces: [
      {
        phase: 'queued',   status: 'success',
        startedAt: '10:18:05.000', endedAt: '10:18:05.011', durationMs: 11,
        proxyUsed: null, cookieUsed: null, errorMessage: null,
      },
      {
        phase: 'resolving', status: 'success',
        startedAt: '10:18:05.011', endedAt: '10:18:05.388', durationMs: 377,
        proxyUsed: '145.131.xx.xx:8823', cookieUsed: null, errorMessage: null,
      },
      {
        phase: 'metadata', status: 'success',
        startedAt: '10:18:05.388', endedAt: '10:18:06.219', durationMs: 831,
        proxyUsed: '145.131.xx.xx:8823', cookieUsed: 'fb_main', errorMessage: null,
      },
      {
        phase: 'auth', status: 'success',
        startedAt: '10:18:06.219', endedAt: '10:18:06.501', durationMs: 282,
        proxyUsed: null, cookieUsed: 'fb_main', errorMessage: null,
      },
      {
        phase: 'download', status: 'running',
        startedAt: '10:18:06.501', endedAt: null, durationMs: null,
        proxyUsed: '145.131.xx.xx:8823', cookieUsed: 'fb_main',
        errorMessage: 'Download stalled — no bytes received for 58s (watchdog timeout threshold: 60s)',
      },
      {
        phase: 'post-process', status: 'pending',
        startedAt: null, endedAt: null, durationMs: null,
        proxyUsed: null, cookieUsed: null, errorMessage: null,
      },
      {
        phase: 'done',         status: 'pending',
        startedAt: null, endedAt: null, durationMs: null,
        proxyUsed: null, cookieUsed: null, errorMessage: null,
      },
    ],
    summary: {
      totalPhases: 7, completedPhases: 4, failedPhases: 0,
      totalDurationMs: null,
      lastError: 'Download stalled — no bytes received for 58s',
    },
    recentErrors: [
      'Download stalled — no bytes received for 58s (watchdog threshold: 60s)',
      'Proxy latency spike: 145.131.xx.xx:8823 → p99=4.2s (normal <500ms)',
    ],
  },
]

export function findJob(query: string): JobInspectorData | null {
  if (!query.trim()) return null
  const q = query.toLowerCase().trim()
  return (
    MOCK_JOBS.find(j =>
      j.jobId.toLowerCase().includes(q) ||
      (j.batchId?.toLowerCase().includes(q) ?? false) ||
      j.url.toLowerCase().includes(q) ||
      j.platform.toLowerCase().includes(q) ||
      j.userIp.includes(q),
    ) ?? null
  )
}

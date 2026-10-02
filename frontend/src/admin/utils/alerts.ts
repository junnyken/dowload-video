import type { AlertItem } from '../panels/ActiveAlertsBanner'
import type { SystemSnapshot } from '../api/system'
import { anomalyLabel, anomalySeverity, groupAnomalies, isStale } from './anomalies'

export interface BuiltAlerts {
  /** Active within the last 24h (or synthetic "right now" alerts). Drives the bell count. */
  fresh: AlertItem[]
  /** Last activity older than 24h. Collapsed on Overview, not counted in the bell. */
  stale: AlertItem[]
  /** Distinct active anomaly types seen in the last 24h (not raw detector records). */
  anomalyCount: number
}

export function buildAlerts(snapshot: SystemSnapshot): BuiltAlerts {
  const { ops, youtubeStatus, cookiePoolStatus } = snapshot
  const failedSrc = new Set(snapshot.failed ?? [])

  const alerts: AlertItem[] = []

  // YouTube circuit + proxy alerts (platform health first)
  const yt = youtubeStatus ?? {}
  if (failedSrc.has('youtubeStatus')) {
    // Unknown, not an outage: say nothing rather than guess.
  } else if (yt.enabled === false) {
    alerts.push({ id: 'yt-disabled', severity: 'critical', message: 'YouTube bị tắt (YOUTUBE_ENABLED=false)', time: 'Hiện tại', platform: 'YouTube' })
  } else if (yt.circuit_state === 'open') {
    alerts.push({ id: 'yt-circuit-open', severity: 'critical', message: 'YouTube circuit breaker OPEN — đang tạm dừng do lỗi liên tiếp', time: 'Hiện tại', platform: 'YouTube' })
  } else if (yt.circuit_state === 'half') {
    alerts.push({ id: 'yt-circuit-half', severity: 'warning', message: 'YouTube circuit breaker HALF-OPEN — đang thử khôi phục', time: 'Hiện tại', platform: 'YouTube' })
  } else if (yt.proxy_bytes_today_gb != null && yt.proxy_limit_gb != null && yt.proxy_bytes_today_gb > yt.proxy_limit_gb * 0.85) {
    alerts.push({ id: 'yt-proxy-cost', severity: 'warning', message: `YouTube proxy bandwidth ${yt.proxy_bytes_today_gb.toFixed(1)}GB / ${yt.proxy_limit_gb}GB (85%+)`, time: 'Hôm nay', platform: 'YouTube' })
  }

  // Cookie pool alerts
  const pools = cookiePoolStatus?.platforms ?? {}
  const twitterTotal = (pools['twitter']?.total ?? pools['x']?.total ?? 0)
  if (failedSrc.has('cookiePoolStatus')) {
    // Fetch failed: an empty pool here would be a false "no cookie" alarm.
  } else if (twitterTotal === 0) {
    alerts.push({ id: 'tw-no-cookie', severity: 'warning', message: 'Twitter/X chưa có cookie — tải Twitter sẽ thất bại', time: 'Hiện tại', platform: 'Twitter' })
  }
  const igTotal = pools['instagram']?.total ?? 0
  if (failedSrc.has('cookiePoolStatus')) {
    // see above
  } else if (igTotal > 0 && igTotal < 3) {
    alerts.push({ id: 'ig-low-pool', severity: 'warning', message: `Instagram cookie pool thấp (${igTotal} account) — dễ bị rate limit`, time: 'Hiện tại', platform: 'Instagram' })
  } else if (igTotal === 0) {
    alerts.push({ id: 'ig-no-cookie', severity: 'warning', message: 'Instagram chưa có cookie — tải Instagram private sẽ thất bại', time: 'Hiện tại', platform: 'Instagram' })
  }

  // Anomaly alerts: one row per type+platform, resolved ones dropped.
  const active = (ops.active_anomalies ?? []).filter(a => a.state !== 'resolved')
  const groups = groupAnomalies(active)

  const anomalyAlerts: AlertItem[] = groups.map((g, i) => {
    const a = g.latest
    return {
      id: g.key || a.id || `anomaly-${i}`,
      severity: anomalySeverity(a),
      title: anomalyLabel(g.type),
      message: a.likely_cause ?? a.message ?? anomalyLabel(g.type),
      time: '',
      lastSeen: g.lastSeen,
      count: g.count,
      platform: g.platform,
    }
  })

  const fresh = anomalyAlerts.filter(a => !isStale(a.lastSeen))
    .sort((a, b) => SEV_RANK[a.severity] - SEV_RANK[b.severity])
    .slice(0, 8)
  const stale = anomalyAlerts.filter(a => isStale(a.lastSeen))

  return { fresh: [...alerts, ...fresh], stale, anomalyCount: anomalyAlerts.length - stale.length }
}

const SEV_RANK = { critical: 0, warning: 1, info: 2 } as const

import type { AlertItem } from '../panels/ActiveAlertsBanner'
import type { SystemSnapshot } from '../api/system'

export function relativeTime(iso: string | undefined): string {
  if (!iso) return '—'
  try {
    const diffMs = Date.now() - new Date(iso).getTime()
    const diffMin = Math.floor(diffMs / 60_000)
    if (diffMin < 1) return 'just now'
    if (diffMin < 60) return `${diffMin}m ago`
    const diffHr = Math.floor(diffMin / 60)
    if (diffHr < 24) return `${diffHr}h ago`
    return `${Math.floor(diffHr / 24)}d ago`
  } catch { return '—' }
}

export function buildAlerts(snapshot: SystemSnapshot): AlertItem[] {
  const { ops, analytics, youtubeStatus, cookiePoolStatus } = snapshot
  const failedJobs24h = analytics.summary?.total_failed ?? 0
  const successRate = analytics.summary?.success_rate ?? 100

  const alerts: AlertItem[] = []

  // YouTube circuit + proxy alerts (platform health first)
  const yt = youtubeStatus ?? {}
  if (yt.enabled === false) {
    alerts.push({ id: 'yt-disabled', severity: 'critical', message: 'YouTube bị tắt (YOUTUBE_ENABLED=false)', time: 'now', platform: 'YouTube' })
  } else if (yt.circuit_state === 'open') {
    alerts.push({ id: 'yt-circuit-open', severity: 'critical', message: 'YouTube circuit breaker OPEN — đang tạm dừng do lỗi liên tiếp', time: 'now', platform: 'YouTube' })
  } else if (yt.circuit_state === 'half') {
    alerts.push({ id: 'yt-circuit-half', severity: 'warning', message: 'YouTube circuit breaker HALF-OPEN — đang thử khôi phục', time: 'now', platform: 'YouTube' })
  } else if (yt.proxy_bytes_today_gb != null && yt.proxy_limit_gb != null && yt.proxy_bytes_today_gb > yt.proxy_limit_gb * 0.85) {
    alerts.push({ id: 'yt-proxy-cost', severity: 'warning', message: `YouTube proxy bandwidth ${yt.proxy_bytes_today_gb.toFixed(1)}GB / ${yt.proxy_limit_gb}GB (85%+)`, time: 'today', platform: 'YouTube' })
  }

  // Cookie pool alerts
  const pools = cookiePoolStatus?.platforms ?? {}
  const twitterTotal = (pools['twitter']?.total ?? pools['x']?.total ?? 0)
  if (twitterTotal === 0) {
    alerts.push({ id: 'tw-no-cookie', severity: 'warning', message: 'Twitter/X chưa có cookie — tải Twitter sẽ thất bại', time: 'now', platform: 'Twitter' })
  }
  const igTotal = pools['instagram']?.total ?? 0
  if (igTotal > 0 && igTotal < 3) {
    alerts.push({ id: 'ig-low-pool', severity: 'warning', message: `Instagram cookie pool thấp (${igTotal} account) — dễ bị rate limit`, time: 'now', platform: 'Instagram' })
  } else if (igTotal === 0) {
    alerts.push({ id: 'ig-no-cookie', severity: 'warning', message: 'Instagram chưa có cookie — tải Instagram private sẽ thất bại', time: 'now', platform: 'Instagram' })
  }

  // Anomaly alerts — deduplicated by metric key, capped at 8
  const all = ops.active_anomalies ?? []
  const seen = new Map<string, typeof all[0]>()
  for (const a of all) {
    const key = a.metric ?? a.type ?? a.likely_cause ?? 'unknown'
    const prev = seen.get(key)
    if (!prev || (a.detected_at ?? '') > (prev.detected_at ?? '')) seen.set(key, a)
  }

  const deduped = [...seen.values()].sort((a, b) => {
    const aIsCrit = (a.metric ?? '').toLowerCase().includes('drift')
    const bIsCrit = (b.metric ?? '').toLowerCase().includes('drift')
    if (aIsCrit !== bIsCrit) return aIsCrit ? -1 : 1
    return (b.detected_at ?? '').localeCompare(a.detected_at ?? '')
  }).slice(0, 8)

  const anomalyAlerts: AlertItem[] = deduped.map((a, i) => {
    const isCrit = (a.metric ?? '').toLowerCase().includes('drift') && (a.magnitude ?? 1) > 1.5
    const sev: AlertItem['severity'] =
      i === 0 && (isCrit || failedJobs24h > 20 || successRate < 80) ? 'critical'
      : (failedJobs24h > 0 || successRate < 99) && i < 2 ? 'warning'
      : 'info'
    return {
      id: a.id ?? `anomaly-${i}`,
      severity: sev,
      message: a.likely_cause ?? a.message ?? a.metric ?? 'Anomaly detected',
      time: relativeTime(a.detected_at),
      platform: a.platform ?? a.metric,
    }
  })

  return [...alerts, ...anomalyAlerts]
}

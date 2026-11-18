import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { adminFetch, adminPost } from '../utils/adminFetch'
import { cn } from '../utils/cn'
import { Button } from '../shared/Button'
import { FormError, Modal } from '../shared/PartnerKeyUi'

type ProbeStatus = 'ok' | 'failed' | 'unconfirmed' | 'stale' | 'not_configured'
type Verdict = 'broken' | 'critical' | 'warning' | null

interface Probe { status: ProbeStatus; reason: string | null; at: number | null; verified_at: number | null; ms: number | null }
interface Traffic { ok?: number; err?: number; user_err?: number; system_err?: number; broken?: number; total?: number }
interface PlatformRow {
  platform: string
  probe: Probe
  traffic_2h: Traffic
  verdict: Verdict
  degraded: { since: number; reason: string } | null
  prefer_cobalt: boolean
  cobalt_capable: boolean
}
interface HistoryEntry { at: number; action: string; [k: string]: unknown }
interface Health {
  platforms: PlatformRow[]
  ytdlp: {
    active: string | null
    override: string | null
    previous: string | null
    pinned: { version: string; source: string; at: number } | null
    latest: { stable: string | null; nightly: string | null }
    history: HistoryEntry[]
  }
  thresholds: Record<string, number>
}

const KEY = ['admin', 'platform-health']

// wording: BA review
const PROBE_LABEL: Record<ProbeStatus, string> = {
  ok: 'Tải được',
  failed: 'Hỏng',
  unconfirmed: 'Lỗi 1 lần, chờ xác nhận',
  stale: 'Kết quả đã cũ',
  not_configured: 'Chưa cấu hình',
}
const PROBE_TONE: Record<ProbeStatus, string> = {
  ok: 'text-success border-success/30 bg-success-soft',
  failed: 'text-danger border-danger/30 bg-danger-soft',
  unconfirmed: 'text-warning border-warning/30 bg-warning-soft',
  stale: 'text-fg-muted border-line-strong bg-surface-2',
  not_configured: 'text-fg-muted border-line-strong bg-surface-2',
}
// wording: BA review
const VERDICT_LABEL: Record<string, string> = { broken: 'Có thể đã đổi trang', critical: 'Lỗi nhiều', warning: 'Lỗi một phần' }
const VERDICT_TONE: Record<string, string> = {
  broken: 'text-danger border-danger/30 bg-danger-soft',
  critical: 'text-danger border-danger/30 bg-danger-soft',
  warning: 'text-warning border-warning/30 bg-warning-soft',
}
// wording: BA review
const ACTION_LABEL: Record<string, string> = {
  promoted: 'Đã nâng bản yt-dlp',
  rolled_back: 'Đã quay về bản trước',
  remediate: 'Thử khắc phục nền tảng lỗi',
  daily_check: 'Kiểm tra bản stable mới',
  restarted: 'Khởi động lại tiến trình',
  boot_reinstalled: 'Khởi động: cài lại bản đã chốt',
  boot_unpinned: 'Khởi động: bỏ chốt (bản trong máy mới hơn)',
  boot_install_failed: 'Khởi động: cài bản đã chốt thất bại',
}
// wording: BA review
const SOURCE_LABEL: Record<string, string> = { stable: 'stable', nightly: 'nightly', rollback: 'quay về' }

const BADGE = 'inline-flex items-center rounded-control border px-2 py-0.5 text-[11px] font-semibold whitespace-nowrap'

/** Compare the first 4 numeric parts of a yt-dlp version; trailing devN ignored. */
function versionParts(v: string): number[] {
  return v.split('.').slice(0, 4).map(p => parseInt(p, 10)).map(n => (Number.isNaN(n) ? 0 : n))
}
function isNewer(candidate: string | null, active: string | null): boolean {
  if (!candidate || !active) return false
  const a = versionParts(candidate)
  const b = versionParts(active)
  for (let i = 0; i < 4; i++) {
    const x = a[i] ?? 0
    const y = b[i] ?? 0
    if (x !== y) return x > y
  }
  return false
}

function ago(epoch: number | null | undefined): string {
  if (!epoch) return '—'
  const s = Math.max(0, Math.floor(Date.now() / 1000 - epoch))
  if (s < 60) return `${s} giây trước`
  if (s < 3600) return `${Math.floor(s / 60)} phút trước`
  if (s < 86400) return `${Math.floor(s / 3600)} giờ trước`
  return `${Math.floor(s / 86400)} ngày trước`
}
function when(epoch: number | null | undefined): string {
  return epoch ? new Date(epoch * 1000).toLocaleString('vi-VN') : '—'
}

function sysPct(t: Traffic): string {
  const denom = (t.total ?? 0) - (t.user_err ?? 0)
  if (denom <= 0) return '—'
  return `${Math.round(((t.system_err ?? 0) / denom) * 100)}%`
}

function rank(r: PlatformRow): number {
  if (r.verdict === 'broken' || r.verdict === 'critical') return 0
  if (r.degraded) return 1
  if (r.probe.status === 'failed') return 2
  if (r.verdict === 'warning') return 3
  if (r.probe.status === 'unconfirmed') return 4
  return 5
}

function historyDetail(h: HistoryEntry): string {
  const parts: string[] = []
  const s = (k: string) => (h[k] === undefined || h[k] === null || h[k] === '' ? null : String(h[k]))
  if (s('version')) parts.push(`bản ${s('version')}`)
  if (s('source')) parts.push(`nguồn ${SOURCE_LABEL[s('source')!] ?? s('source')}`)
  if (s('from') || s('to')) parts.push(`${s('from') ?? '?'} → ${s('to') ?? '?'}`)
  if (s('result')) parts.push(`kết quả: ${s('result')}`)
  if (Array.isArray(h.platforms) && h.platforms.length) parts.push(`nền tảng: ${(h.platforms as unknown[]).join(', ')}`)
  if (s('reason')) parts.push(`lý do: ${s('reason')}`)
  return parts.join(' · ')
}

function triedList(h: HistoryEntry): string[] {
  if (!Array.isArray(h.tried)) return []
  return (h.tried as unknown[]).map(t => {
    if (t && typeof t === 'object') {
      const o = t as Record<string, unknown>
      const v = o.version ?? o.v ?? ''
      const res = o.result ?? o.ok ?? ''
      return `${v}${res !== '' ? ` (${String(res)})` : ''}`.trim() || JSON.stringify(t)
    }
    return String(t)
  })
}

function HistoryItem({ h }: { h: HistoryEntry }) {
  const [open, setOpen] = useState(false)
  const tried = triedList(h)
  const detail = historyDetail(h)
  const shown = open ? tried : tried.slice(0, 3)
  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <span className="text-sm font-medium text-fg">{ACTION_LABEL[h.action] ?? h.action}</span>
        <span className="font-mono text-[11px] text-fg-muted" title={when(h.at)}>{when(h.at)}</span>
      </div>
      {detail && <p className="mt-0.5 break-words text-xs text-fg-muted">{detail}</p>}
      {tried.length > 0 && (
        <p className="mt-0.5 break-words text-xs text-fg-muted">
          {/* wording: BA review */}
          Đã thử: {shown.join(', ')}
          {tried.length > 3 && (
            <button type="button" onClick={() => setOpen(o => !o)} className="ml-1 text-accent hover:underline">
              {open ? 'thu gọn' : `… +${tried.length - 3} nữa`}
            </button>
          )}
        </p>
      )}
    </li>
  )
}

interface SelfHealingPlatformRow {
  total_requests: number
  total_errors: number
  window_errors: number
  window_total: number
  error_rate_pct: number
  active_reroute: string | null
  reroute_expires: string | null
  status: 'healthy' | 'degraded' | 'rerouted'
}

interface SelfHealingStatus {
  enabled: boolean
  platforms: Record<string, SelfHealingPlatformRow>
}

interface SelfHealingConfig {
  enabled: boolean
  window_minutes: number
  error_threshold_pct: number
  min_samples: number
  cooldown_minutes: number
  telegram_enabled: boolean
  fallback_routes: Record<string, string[]>
}

const SH_TONE: Record<string, string> = {
  healthy: 'text-success border-success/30 bg-success-soft',
  degraded: 'text-danger border-danger/30 bg-danger-soft',
  rerouted: 'text-warning border-warning/30 bg-warning-soft',
}
const SH_LABEL: Record<string, string> = {
  healthy: 'Ổn định',
  degraded: 'Lỗi cao',
  rerouted: 'Đã chuyển hướng',
}

function SelfHealingSection() {
  const qc = useQueryClient()
  const [selectedPlatform, setSelectedPlatform] = useState('')
  const [selectedMethod, setSelectedMethod] = useState('cobalt')
  const [shNotice, setShNotice] = useState<{ ok: boolean; text: string } | null>(null)

  const statusQ = useQuery<SelfHealingStatus>({
    queryKey: ['admin', 'self-healing', 'status'],
    queryFn: () => adminFetch<SelfHealingStatus>('/self-healing/status'),
    retry: false,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  })

  const configQ = useQuery<SelfHealingConfig>({
    queryKey: ['admin', 'self-healing', 'config'],
    queryFn: () => adminFetch<SelfHealingConfig>('/self-healing/config'),
    retry: false,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  })

  const rerouteMut = useMutation({
    mutationFn: (payload: { platform: string; method: string | null; duration_minutes?: number }) =>
      adminPost('/self-healing/reroute', payload),
    onSuccess: (_res, vars) => {
      qc.invalidateQueries({ queryKey: ['admin', 'self-healing', 'status'] })
      setShNotice({
        ok: true,
        text: vars.method
          ? `Đã chuyển hướng ${vars.platform} sang "${vars.method}" trong 60 phút.`
          : `Đã gỡ chuyển hướng cho ${vars.platform}.`,
      })
    },
    onError: e => setShNotice({ ok: false, text: (e as Error).message }),
  })

  if (statusQ.isError || configQ.isError) {
    return (
      <section>
        <h2 className="mb-2 text-sm font-semibold text-fg">AI Self-Healing Extractor</h2>
        <div className="rounded-card border border-line bg-surface p-4 text-xs text-fg-muted">
          Mô-đun AI Self-Healing sẽ hiển thị sau khi backend hoàn tất triển khai bản mới.
        </div>
      </section>
    )
  }

  if (statusQ.isLoading || configQ.isLoading) {
    return (
      <section>
        <h2 className="mb-2 text-sm font-semibold text-fg">AI Self-Healing Extractor</h2>
        <div className="rounded-card border border-line bg-surface p-4 text-xs text-fg-muted animate-pulse">
          Đang tải trạng thái AI Self-Healing…
        </div>
      </section>
    )
  }

  const statusData = statusQ.data
  const configData = configQ.data
  const fallbackKeys = Object.keys(configData?.fallback_routes || {})
  const activePlatform = selectedPlatform || fallbackKeys[0] || ''
  const availableMethods = Array.from(
    new Set([
      ...(configData?.fallback_routes?.[activePlatform] || []),
      'cobalt',
      'browser_direct',
      'proxy_pool',
      'proxy_cn',
    ]),
  )
  const platformRows = Object.entries(statusData?.platforms || {})

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-fg">AI Self-Healing Extractor</h2>
          <span className={cn(BADGE, statusData?.enabled ? 'text-success border-success/30 bg-success-soft' : 'text-fg-muted border-line-strong bg-surface-2')}>
            {statusData?.enabled ? 'Đang bật' : 'Đang tắt'}
          </span>
        </div>
        {configData && (
          <p className="text-xs text-fg-muted">
            Ngưỡng: <span className="font-mono text-fg-2">{configData.error_threshold_pct}%</span> · tối thiểu <span className="font-mono text-fg-2">{configData.min_samples}</span> mẫu / <span className="font-mono text-fg-2">{configData.window_minutes} phút</span> · Telegram: <span className="font-mono text-fg-2">{configData.telegram_enabled ? 'bật' : 'tắt'}</span>
          </p>
        )}
      </div>

      {shNotice && (
        <div className={cn('rounded-control border px-3 py-2 text-xs', shNotice.ok ? 'border-success/30 bg-success-soft text-success' : 'border-danger/30 bg-danger-soft text-danger')}>
          {shNotice.text}
        </div>
      )}

      {/* Quick manual reroute bar */}
      {fallbackKeys.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-card border border-line bg-surface p-3">
          <span className="text-xs font-medium text-fg-2">Chuyển hướng thủ công:</span>
          <select
            value={activePlatform}
            onChange={e => setSelectedPlatform(e.target.value)}
            className="rounded-control border border-line-strong bg-surface-2 px-2.5 py-1.5 text-xs text-fg"
          >
            {fallbackKeys.map(p => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
          <select
            value={availableMethods.includes(selectedMethod) ? selectedMethod : availableMethods[0]}
            onChange={e => setSelectedMethod(e.target.value)}
            className="rounded-control border border-line-strong bg-surface-2 px-2.5 py-1.5 text-xs text-fg"
          >
            {availableMethods.map(m => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
          <Button
            size="sm"
            disabled={!activePlatform || rerouteMut.isPending}
            onClick={() => {
              setShNotice(null)
              const method = availableMethods.includes(selectedMethod) ? selectedMethod : availableMethods[0]
              rerouteMut.mutate({ platform: activePlatform, method, duration_minutes: 60 })
            }}
          >
            Chuyển hướng 60 phút
          </Button>
        </div>
      )}

      {/* Platform health table */}
      {platformRows.length === 0 ? (
        <div className="rounded-card border border-line bg-surface p-4 text-center text-xs text-fg-muted">
          Chưa ghi nhận lưu lượng lỗi/thành công trên Self-Healing Monitor trong cửa sổ hiện tại.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-card border border-line bg-surface">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs text-fg-muted">
                <th className="px-4 py-2 font-medium">Nền tảng</th>
                <th className="px-4 py-2 font-medium">Tỷ lệ lỗi</th>
                <th className="px-4 py-2 font-medium">Cửa sổ ({configData?.window_minutes ?? 60}p)</th>
                <th className="px-4 py-2 font-medium">Trạng thái</th>
                <th className="px-4 py-2 font-medium">Kênh chuyển hướng</th>
                <th className="px-4 py-2 font-medium">Hành động</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {platformRows.map(([platform, info]) => (
                <tr key={platform} className="align-top">
                  <td className="px-4 py-2 font-medium capitalize text-fg">{platform}</td>
                  <td className="px-4 py-2 font-mono text-xs text-fg-2">{info.error_rate_pct}%</td>
                  <td className="px-4 py-2 text-xs text-fg-2">{info.window_errors} / {info.window_total}</td>
                  <td className="px-4 py-2">
                    <span className={cn(BADGE, SH_TONE[info.status] ?? SH_TONE.healthy)}>
                      {SH_LABEL[info.status] ?? info.status}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-xs text-fg-2">
                    {info.active_reroute ? (
                      <div>
                        <span className="font-mono font-semibold text-warning">{info.active_reroute}</span>
                        {info.reroute_expires && (
                          <div className="text-[11px] text-fg-muted">hết hạn: {new Date(info.reroute_expires).toLocaleTimeString('vi-VN')}</div>
                        )}
                      </div>
                    ) : <span className="text-fg-muted">—</span>}
                  </td>
                  <td className="px-4 py-2">
                    {info.active_reroute && (
                      <Button
                        size="sm"
                        disabled={rerouteMut.isPending}
                        onClick={() => {
                          setShNotice(null)
                          rerouteMut.mutate({ platform, method: null })
                        }}
                      >
                        Gỡ chuyển hướng
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

export default function PlatformHealthPage() {
  const qc = useQueryClient()
  const { data, isLoading, isError, error, refetch, isFetching } = useQuery<Health>({
    queryKey: KEY,
    queryFn: () => adminFetch<Health>('/platform-health'),
    staleTime: 60_000,
    refetchInterval: 60_000,
    refetchOnWindowFocus: false,
  })
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null)
  const [confirmRollback, setConfirmRollback] = useState(false)

  const remediate = useMutation({
    mutationFn: () => adminPost<{ queued: boolean; platforms: string[] }>('/ytdlp/remediate', { platforms: [] }),
    // wording: BA review
    onSuccess: r => setNotice({ ok: true, text: `Đã đưa vào hàng chờ xử lý${r.platforms?.length ? ` (${r.platforms.join(', ')})` : ''}. Mất vài phút, chạy nền. Kết quả báo qua Telegram và ghi ở Lịch sử yt-dlp bên dưới (bấm Làm mới để thấy).` }),
    onError: e => setNotice({ ok: false, text: (e as Error).message }),
  })
  const daily = useMutation({
    mutationFn: () => adminPost<{ queued: boolean }>('/ytdlp/daily-check'),
    // wording: BA review
    onSuccess: () => setNotice({ ok: true, text: 'Đã đưa vào hàng chờ: kiểm tra bản yt-dlp stable mới. Kết quả báo qua Telegram và ghi ở Lịch sử yt-dlp sau khi làm mới.' }),
    onError: e => setNotice({ ok: false, text: (e as Error).message }),
  })
  const rollback = useMutation({
    mutationFn: () => adminPost<{ from?: string; to?: string }>('/ytdlp/rollback'),
    onSuccess: r => {
      setConfirmRollback(false)
      // wording: BA review
      setNotice({ ok: true, text: `Đã quay về bản trước${r.to ? ` (${r.from ?? '?'} → ${r.to})` : ''}. Máy chủ đang khởi động lại trong vài giây.` })
      qc.invalidateQueries({ queryKey: KEY })
    },
  })
  const cobalt = useMutation({
    mutationFn: (v: { platform: string; on: boolean }) => adminPost('/platform-health/cobalt', v),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
    onError: e => setNotice({ ok: false, text: (e as Error).message }),
  })
  const recovered = useMutation({
    mutationFn: (platform: string) => adminPost('/platform-health/recovered', { platform }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
    onError: e => setNotice({ ok: false, text: (e as Error).message }),
  })

  if (isLoading) {
    return <div className="flex min-h-[60vh] items-center justify-center">
      {/* wording: BA review */}
      <div className="animate-pulse text-sm text-fg-muted">Đang tải sức khoẻ nền tảng (có thể mất vài giây)…</div>
    </div>
  }
  if (isError || !data) {
    return <div className="mx-auto mt-16 max-w-2xl rounded-card border border-danger/30 bg-danger-soft p-6">
      {/* wording: BA review */}
      <p className="mb-1 font-semibold text-danger">Không tải được dữ liệu sức khoẻ nền tảng</p>
      <p className="mb-4 break-words text-sm text-fg-muted">{(error as Error)?.message}</p>
      <Button onClick={() => refetch()}>Thử lại</Button>
    </div>
  }

  const y = data.ytdlp
  const rows = [...data.platforms].sort((a, b) =>
    rank(a) - rank(b) || (b.traffic_2h.total ?? 0) - (a.traffic_2h.total ?? 0) || a.platform.localeCompare(b.platform))
  const canRollback = !!(y.previous || y.pinned)
  const busy = remediate.isPending || daily.isPending || rollback.isPending

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          {/* wording: BA review */}
          <h1 className="text-lg font-semibold tracking-tight text-fg">Sức khoẻ nền tảng</h1>
          <ul className="mt-0.5 list-disc list-inside space-y-0.5 text-sm text-fg-muted">
            <li>Mỗi 30 phút: thử lấy thông tin một video mẫu của từng nền tảng.</li>
            <li>Mỗi 5 phút: xem tỷ lệ lỗi 2 giờ qua (không tính video bị xoá/riêng tư).</li>
            <li>Khi kiểm tra tự động xác nhận một nền tảng hỏng: báo Telegram; Facebook, Instagram, X chuyển sang Cobalt trước 6 giờ; thử yt-dlp stable mới hơn rồi nightly (tối đa 1 lần mỗi 6 giờ cho mỗi nền tảng), chỉ giữ bản mới nếu sửa được mà không làm hỏng nền tảng khác.</li>
            <li>Lỗi chỉ thấy trong lượt tải của người dùng (chưa được kiểm tra tự động xác nhận) thì chỉ báo Telegram.</li>
          </ul>
        </div>
        <Button onClick={() => refetch()} disabled={isFetching}>{isFetching ? 'Đang làm mới…' : 'Làm mới'}</Button>
      </div>

      {notice && (
        <div role="status" className={cn('break-words rounded-card border px-4 py-3 text-sm',
          notice.ok ? 'border-success/30 bg-success-soft text-success' : 'border-danger/30 bg-danger-soft text-danger')}>
          {notice.text}
        </div>
      )}

      <section className="rounded-card border border-line bg-surface p-4 shadow-card">
        <h2 className="text-sm font-semibold text-fg">yt-dlp</h2>
        {/* wording: BA review */}
        <p className="mt-0.5 text-xs text-fg-muted">stable = bản ổn định; nightly = bản thử nghiệm hằng đêm</p>
        <dl className="mt-3 grid grid-cols-1 gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <dt className="text-xs text-fg-muted">{/* wording: BA review */}Bản đang dùng</dt>
            <dd className="break-all font-mono text-fg">{y.active ?? '—'}{y.override && <span className="ml-1 text-xs text-fg-muted">(bản cài thêm {y.override})</span>}</dd>
          </div>
          <div>
            <dt className="text-xs text-fg-muted">{/* wording: BA review */}Bản đã chốt</dt>
            <dd className="break-all text-fg">
              {y.pinned
                ? <><span className="font-mono">{y.pinned.version}</span> <span className="text-xs text-fg-muted">· {SOURCE_LABEL[y.pinned.source] ?? y.pinned.source} · {ago(y.pinned.at)}</span></>
                : <span className="text-fg-muted">Không chốt bản nào</span>}
            </dd>
          </div>
          {(['stable', 'nightly'] as const).map(k => (
            <div key={k}>
              <dt className="text-xs text-fg-muted">{/* wording: BA review */}Mới nhất ({k})</dt>
              <dd className="break-all text-fg">
                <span className="font-mono">{y.latest[k] ?? '—'}</span>
                {isNewer(y.latest[k], y.active) && (
                  <span className={cn(BADGE, 'ml-2 border-accent/40 text-accent')}>{/* wording: BA review */}mới hơn</span>
                )}
              </dd>
            </div>
          ))}
        </dl>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button onClick={() => { setNotice(null); daily.mutate() }} disabled={busy}>
            {/* wording: BA review */}Kiểm tra bản stable mới
          </Button>
          <Button variant="primary" onClick={() => { setNotice(null); remediate.mutate() }} disabled={busy}>
            {/* wording: BA review */}{remediate.isPending ? 'Đang gửi lệnh…' : 'Thử khắc phục nền tảng đang lỗi'}
          </Button>
          <Button variant="danger" onClick={() => { rollback.reset(); setConfirmRollback(true) }} disabled={busy || !canRollback}
            title={canRollback ? undefined : 'Chưa có bản trước để quay về'}>
            {/* wording: BA review */}Quay về bản trước
          </Button>
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-fg">{/* wording: BA review */}Nền tảng</h2>
        {rows.length === 0 ? (
          <div className="rounded-card border border-line bg-surface p-6 text-center text-sm text-fg-muted">
            {/* wording: BA review */}Chưa có nền tảng nào.
          </div>
        ) : (
          <div className="overflow-x-auto rounded-card border border-line">
            <table className="w-full min-w-[860px] text-sm">
              <thead className="bg-surface text-xs text-fg-muted">
                <tr>
                  {/* wording: BA review */}
                  <th className="px-4 py-2 text-left font-semibold">Nền tảng</th>
                  <th className="px-4 py-2 text-left font-semibold">Kiểm tra tự động</th>
                  <th className="px-4 py-2 text-left font-semibold">Lượt tải 2 giờ qua</th>
                  <th className="px-4 py-2 text-left font-semibold">Mức báo động</th>
                  <th className="px-4 py-2 text-left font-semibold">Đường Cobalt</th>
                  <th className="px-4 py-2 text-left font-semibold">Thao tác</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(r => {
                  const t = r.traffic_2h
                  const hasTraffic = (t.total ?? 0) > 0
                  return (
                    <tr key={r.platform} className="border-t border-line align-top">
                      <td className="px-4 py-2.5 font-medium text-fg-2">{r.platform}</td>
                      <td className="px-4 py-2.5">
                        <span className={cn(BADGE, PROBE_TONE[r.probe.status])} title={r.probe.reason ?? undefined}>{PROBE_LABEL[r.probe.status]}</span>
                        {r.probe.reason && <div className="mt-1 max-w-[260px] break-words text-xs text-fg-muted">{r.probe.reason}</div>}
                        {/* wording: BA review */}
                        <div className="mt-0.5 text-[11px] text-fg-muted" title={when(r.probe.at)}>kiểm {ago(r.probe.at)}</div>
                      </td>
                      <td className="px-4 py-2.5 text-xs text-fg-2">
                        {hasTraffic ? (
                          <>
                            {/* wording: BA review */}
                            <div><span className="font-mono tabular-nums">{t.total}</span> lượt · lỗi hệ thống <span className="font-mono">{sysPct(t)}</span> · không đọc được trang: <span className="font-mono">{t.broken ?? 0}</span></div>
                          </>
                        ) : <span className="text-fg-muted">Chưa có lượt nào</span>}
                      </td>
                      <td className="px-4 py-2.5">
                        {r.verdict && <span className={cn(BADGE, VERDICT_TONE[r.verdict])}>{VERDICT_LABEL[r.verdict]}</span>}
                        {r.degraded && (
                          <div className="mt-1">
                            {/* wording: BA review */}
                            <span className={cn(BADGE, 'border-warning/30 bg-warning-soft text-warning')} title={r.degraded.reason}>Đang trục trặc</span>
                            <div className="mt-0.5 max-w-[220px] break-words text-[11px] text-fg-muted">từ {ago(r.degraded.since)}{r.degraded.reason ? ` · ${r.degraded.reason}` : ''}</div>
                          </div>
                        )}
                        {!r.verdict && !r.degraded && <span className="text-xs text-fg-muted">—</span>}
                      </td>
                      <td className="px-4 py-2.5">
                        {r.cobalt_capable ? (
                          <label className="inline-flex cursor-pointer items-center gap-2 text-xs text-fg-2">
                            <input type="checkbox" checked={r.prefer_cobalt}
                              disabled={cobalt.isPending && cobalt.variables?.platform === r.platform}
                              onChange={e => { setNotice(null); cobalt.mutate({ platform: r.platform, on: e.target.checked }) }} />
                            {/* wording: BA review */}
                            {r.prefer_cobalt ? 'Dùng Cobalt trước (6 giờ)' : 'Không dùng Cobalt trước'}
                          </label>
                        ) : <span className="text-xs text-fg-muted">—</span>}
                      </td>
                      <td className="px-4 py-2.5">
                        {r.degraded && (
                          <Button size="sm" disabled={recovered.isPending && recovered.variables === r.platform}
                            onClick={() => { setNotice(null); recovered.mutate(r.platform) }}>
                            {/* wording: BA review */}Đánh dấu đã ổn
                          </Button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <SelfHealingSection />

      <section>
        <h2 className="mb-2 text-sm font-semibold text-fg">{/* wording: BA review */}Lịch sử yt-dlp</h2>
        {y.history.length === 0 ? (
          <div className="rounded-card border border-line bg-surface p-6 text-center text-sm text-fg-muted">
            {/* wording: BA review */}Chưa có thao tác nào.
          </div>
        ) : (
          <ul className="divide-y divide-line rounded-card border border-line bg-surface">
            {y.history.map((h, i) => <HistoryItem key={`${h.at}-${i}`} h={h} />)}
          </ul>
        )}
      </section>

      {confirmRollback && (
        // wording: BA review
        <Modal title="Quay về bản yt-dlp trước?"
          onClose={() => setConfirmRollback(false)} locked={rollback.isPending}>
          <div className="space-y-3">
            <p className="text-sm text-fg-2">
              {/* wording: BA review */}
              Hệ thống sẽ quay về bản yt-dlp trước{y.previous ? ` (${y.previous})` : ''}. Máy chủ khởi động lại trong vài giây.
            </p>
            <FormError message={rollback.isError ? (rollback.error as Error).message : null} />
            <div className="flex justify-end gap-2">
              <Button onClick={() => setConfirmRollback(false)} disabled={rollback.isPending}>{/* wording: BA review */}Huỷ</Button>
              <Button variant="danger" onClick={() => rollback.mutate()} disabled={rollback.isPending}>
                {/* wording: BA review */}{rollback.isPending ? 'Đang quay về…' : 'Quay về bản trước'}
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )
}

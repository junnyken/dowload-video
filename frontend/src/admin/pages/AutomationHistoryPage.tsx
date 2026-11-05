import { useCallback, useEffect, useState } from 'react'
import { intelFetch, intelPost } from '../utils/adminFetch'
import { cn } from '../utils/cn'
import { Button } from '../shared/Button'

interface Event {
  timestamp: string
  source: string
  action: string
  reason: string
  outcome: string
}

interface RecommendedAction {
  action: string
  params: Record<string, unknown>
}

interface DiagnosticIssue {
  id: string
  severity: 'critical' | 'warning'
  title: string
  cause: string
  solution_summary: string
  recommended_actions: RecommendedAction[]
}

interface AutomationPayload {
  events: Event[]
  count: number
  auto_heal_enabled?: boolean
  last_auto_heal_at?: string | null
  issues?: DiagnosticIssue[]
}

const SOURCE_STYLE: Record<string, string> = {
  auto_heal:        'bg-success-soft text-success border-success/30',
  auto_tuner:       'bg-surface-2 text-fg-2 border-line',
  playbooks:        'bg-surface-2 text-fg-2 border-line',
  anomaly_detector: 'bg-warning-soft text-warning border-warning/30',
}

const SOURCE_LABEL: Record<string, string> = {
  auto_heal:        'Tự khắc phục (Auto-Heal)',
  auto_tuner:       'Tự tinh chỉnh tải',
  playbooks:        'Kịch bản (Playbook)',
  anomaly_detector: 'Phát hiện bất thường',
}

const QUICK_ACTIONS: Array<{
  action: string
  label: string
  desc: string
  params: Record<string, unknown>
}> = [
  {
    action: 'recover_stale_jobs',
    label: 'Giải phóng Job bị treo',
    desc: 'Tìm và giải phóng các job kẹt trạng thái chờ/đang xử lý quá 15 phút.',
    params: {},
  },
  {
    action: 'refresh_cookies',
    label: 'Làm mới bể Cookie',
    desc: 'Gỡ cờ tạm khoá (cooldown) và nạp lại bể Cookie cho các nền tảng.',
    params: { platform: 'all' },
  },
  {
    action: 'reset_proxy_circuits',
    label: 'Đặt lại mạch ngắt Proxy/Provider',
    desc: 'Mở lại các Circuit Breaker và gỡ trạng thái suy giảm để thử tải lại.',
    params: {},
  },
  {
    action: 'rotate_fallback_order',
    label: 'Ưu tiên Cobalt (FB / IG / X)',
    desc: 'Chuyển hướng tải Facebook, Instagram, X ưu tiên qua cụm Cobalt trong 6 giờ.',
    params: { platform: 'all' },
  },
  {
    action: 'upgrade_ytdlp',
    label: 'Nâng cấp nóng yt-dlp',
    desc: 'Tải và kiểm thử bản yt-dlp mới nhất khi nền tảng vừa đổi cấu trúc trang.',
    params: { platform: 'youtube' },
  },
  {
    action: 'trigger_cleanup',
    label: 'Dọn file tạm ổ cứng',
    desc: 'Xoá ngay các file tải dở (.part, .ytdl, .tmp) để giải phóng dung lượng.',
    params: {},
  },
  {
    action: 'restore_normal_mode',
    label: 'Khôi phục cấu hình mặc định',
    desc: 'Đưa thông số tải đồng thời về mặc định và bật lại các lịch quét định kỳ.',
    params: {},
  },
]

export default function AutomationHistoryPage() {
  const [events, setEvents] = useState<Event[]>([])
  const [issues, setIssues] = useState<DiagnosticIssue[]>([])
  const [autoHealEnabled, setAutoHealEnabled] = useState<boolean>(true)
  const [lastRunAt, setLastRunAt] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [bannerMsg, setBannerMsg] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [toggling, setToggling] = useState(false)
  const [scanning, setScanning] = useState(false)
  const [busyAction, setBusyAction] = useState<Record<string, boolean>>({})
  const [source, setSource] = useState<string>('all')

  const load = useCallback(async () => {
    try {
      const r = await intelFetch<AutomationPayload>('/automation-history')
      setEvents(r.events || [])
      setIssues(r.issues || [])
      if (typeof r.auto_heal_enabled === 'boolean') {
        setAutoHealEnabled(r.auto_heal_enabled)
      }
      setLastRunAt(r.last_auto_heal_at ?? null)
      setErr(null)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 30_000)
    return () => clearInterval(t)
  }, [load])

  async function handleToggleAutoHeal() {
    setToggling(true)
    setErr(null)
    try {
      const res = await intelPost<{ success: boolean; auto_heal_enabled: boolean }>(
        '/auto-heal/toggle',
        { enabled: !autoHealEnabled },
      )
      setAutoHealEnabled(res.auto_heal_enabled)
      setBannerMsg(
        res.auto_heal_enabled
          ? 'Đã BẬT chế độ Tự động khắc phục lỗi (Auto-Heal). Hệ thống sẽ tự chẩn đoán và sửa lỗi khi phát hiện sự cố.'
          : 'Đã TẮT chế độ tự động thực thi. Hệ thống vẫn chẩn đoán và đề xuất giải pháp để bạn bấm duyệt.',
      )
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setToggling(false)
    }
  }

  async function handleRunAutoHealNow() {
    setScanning(true)
    setErr(null)
    setBannerMsg(null)
    try {
      const res = await intelPost<{
        issues_found: number
        actions_executed: Array<{ issue_title: string; result?: { message?: string } }>
      }>('/auto-heal/run', {})
      const count = res.actions_executed?.length ?? 0
      if (res.issues_found === 0) {
        setBannerMsg('✅ Đã quét toàn bộ hệ thống: Không phát hiện sự cố nào — mọi dịch vụ đều đang hoạt động tốt.')
      } else {
        setBannerMsg(
          `⚡ Đã phát hiện ${res.issues_found} vấn đề và tự động thực thi ${count} bước khắc phục thành công!`,
        )
      }
      await load()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setScanning(false)
    }
  }

  async function handleFixIssue(issue: DiagnosticIssue) {
    setBusyAction(p => ({ ...p, [issue.id]: true }))
    setErr(null)
    try {
      const messages: string[] = []
      for (const step of issue.recommended_actions) {
        const r = await intelPost<{ message?: string }>('/playbooks/execute', {
          playbook_id: issue.id,
          action: step.action,
          params: step.params || {},
        })
        if (r?.message) messages.push(r.message)
      }
      setBannerMsg(`✅ Đã thực thi giải pháp cho "${issue.title}": ${messages.join(' · ') || 'Hoàn tất'}`)
      await load()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusyAction(p => ({ ...p, [issue.id]: false }))
    }
  }

  async function handleQuickAction(act: string, params: Record<string, unknown>, label: string) {
    setBusyAction(p => ({ ...p, [act]: true }))
    setErr(null)
    try {
      const r = await intelPost<{ message?: string }>('/playbooks/execute', {
        playbook_id: `quick_${act}`,
        action: act,
        params,
      })
      setBannerMsg(`✅ ${label}: ${r?.message || 'Đã thực thi thành công.'}`)
      await load()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusyAction(p => ({ ...p, [act]: false }))
    }
  }

  const sources = Array.from(new Set(events.map(e => e.source))).sort()
  const shown = source === 'all' ? events : events.filter(e => e.source === source)
  const autoHealCount = events.filter(e => e.source === 'auto_heal' || e.source === 'playbooks').length

  return (
    <div className="space-y-6">
      {/* Header + Auto-Heal Switch */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-fg">
            Tự động hoá & Tự khắc phục lỗi (Auto-Heal)
          </h1>
          <p className="mt-0.5 text-xs text-fg-muted">
            Tự động phát hiện sự cố, đưa ra giải pháp và tiến hành khắc phục ngay mà không cần chờ can thiệp thủ công
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            disabled={toggling}
            onClick={handleToggleAutoHeal}
            className={cn(
              'rounded-control border px-3 py-1.5 text-xs font-semibold transition-colors disabled:opacity-50',
              autoHealEnabled
                ? 'border-success/40 bg-success-soft text-success hover:bg-success/20'
                : 'border-warning/40 bg-warning-soft text-warning hover:bg-warning/20',
            )}
          >
            {toggling
              ? 'Đang lưu…'
              : autoHealEnabled
              ? '🟢 Tự khắc phục: ĐANG BẬT'
              : '🟡 Tự khắc phục: ĐANG TẮT (Chỉ đề xuất)'}
          </button>
          <Button variant="primary" onClick={handleRunAutoHealNow} disabled={scanning}>
            {scanning ? 'Đang quét & xử lý…' : '🔍 Quét & Tự khắc phục ngay'}
          </Button>
          <Button onClick={load}>↺ Tải lại</Button>
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="rounded-card border border-line bg-surface p-4 shadow-card">
          <p className="font-mono text-xs text-fg-muted">Chế độ Auto-Heal</p>
          <p className={cn('mt-1 font-mono text-lg font-bold', autoHealEnabled ? 'text-success' : 'text-warning')}>
            {autoHealEnabled ? 'Tự động 100%' : 'Bán tự động'}
          </p>
          <p className="mt-0.5 text-[11px] text-fg-muted">
            {lastRunAt ? `Quét gần nhất: ${lastRunAt.replace('T', ' ').slice(0, 19)}` : 'Chạy định kỳ mỗi 5 phút'}
          </p>
        </div>
        <div className="rounded-card border border-line bg-surface p-4 shadow-card">
          <p className="font-mono text-xs text-fg-muted">Sự cố đang phát hiện</p>
          <p className={cn('mt-1 font-mono text-2xl font-bold', issues.length > 0 ? 'text-warning' : 'text-success')}>
            {issues.length}
          </p>
          <p className="mt-0.5 text-[11px] text-fg-muted">
            {issues.length === 0 ? 'Mọi nền tảng ổn định' : 'Đã có giải pháp đề xuất bên dưới'}
          </p>
        </div>
        <div className="rounded-card border border-line bg-surface p-4 shadow-card">
          <p className="font-mono text-xs text-fg-muted">Lượt khắc phục đã chạy</p>
          <p className="mt-1 font-mono text-2xl font-bold text-fg">{autoHealCount}</p>
          <p className="mt-0.5 text-[11px] text-fg-muted">Ghi nhận trong lịch sử gần nhất</p>
        </div>
        <div className="rounded-card border border-line bg-surface p-4 shadow-card">
          <p className="font-mono text-xs text-fg-muted">Tổng sự kiện tự động</p>
          <p className="mt-1 font-mono text-2xl font-bold text-fg">{events.length}</p>
          <p className="mt-0.5 text-[11px] text-fg-muted">Tự làm mới mỗi 30 giây</p>
        </div>
      </div>

      {err && (
        <div className="rounded-control border border-danger/30 bg-danger-soft px-4 py-3 text-xs text-danger">
          Lỗi: {err}
        </div>
      )}
      {bannerMsg && (
        <div className="rounded-control border border-success/30 bg-success-soft px-4 py-3 text-xs text-success">
          {bannerMsg}
        </div>
      )}

      {/* Section 1: Live Diagnostic Issues & Proposed Solutions */}
      <section className="rounded-card border border-line bg-surface p-5 shadow-card space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold text-fg">
              1. Chẩn đoán thời gian thực & Giải pháp khắc phục
            </h2>
            <p className="text-xs text-fg-muted">
              Khi phát hiện lỗi, hệ thống tự động phân tích nguyên nhân, đưa ra giải pháp và tiến hành xử lý
            </p>
          </div>
        </div>

        {loading ? (
          <p className="text-xs text-fg-muted animate-pulse">Đang kiểm tra sức khoẻ toàn hệ thống…</p>
        ) : issues.length === 0 ? (
          <div className="rounded-control border border-success/30 bg-success-soft px-4 py-3 text-xs text-success">
            ✅ Hệ thống đang hoạt động ổn định — Hiện không có nền tảng, hàng đợi, ổ cứng hay bể Cookie nào gặp sự cố.
          </div>
        ) : (
          <div className="space-y-3">
            {issues.map(issue => (
              <div
                key={issue.id}
                className={cn(
                  'rounded-control border p-4',
                  issue.severity === 'critical'
                    ? 'border-danger/40 bg-danger-soft/40'
                    : 'border-warning/40 bg-warning-soft/40',
                )}
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="space-y-1 max-w-2xl">
                    <div className="flex items-center gap-2">
                      <span
                        className={cn(
                          'rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase',
                          issue.severity === 'critical'
                            ? 'bg-danger-soft text-danger'
                            : 'bg-warning-soft text-warning',
                        )}
                      >
                        {issue.severity === 'critical' ? 'Nghiêm trọng' : 'Cảnh báo'}
                      </span>
                      <h3 className="text-sm font-semibold text-fg">{issue.title}</h3>
                    </div>
                    <p className="text-xs text-fg-muted">
                      <strong className="text-fg-2">Nguyên nhân:</strong> {issue.cause}
                    </p>
                    <p className="text-xs text-fg-2">
                      <strong className="text-success">Giải pháp hệ thống:</strong> {issue.solution_summary}
                    </p>
                  </div>
                  <Button
                    variant="primary"
                    size="sm"
                    disabled={!!busyAction[issue.id]}
                    onClick={() => handleFixIssue(issue)}
                  >
                    {busyAction[issue.id] ? 'Đang khắc phục…' : '⚡ Thực thi giải pháp ngay'}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Section 2: One-Click Self-Healing Toolkit */}
      <section className="rounded-card border border-line bg-surface p-5 shadow-card space-y-3">
        <div>
          <h2 className="text-sm font-semibold text-fg">
            2. Bộ công cụ khắc phục nhanh (1-Click Remediation)
          </h2>
          <p className="text-xs text-fg-muted">
            Các hành động hệ thống tự động chạy khi gặp lỗi — bạn cũng có thể bấm kích hoạt thủ công bất cứ lúc nào
          </p>
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {QUICK_ACTIONS.map(item => (
            <div
              key={item.action}
              className="flex flex-col justify-between rounded-control border border-line bg-surface-2 p-3.5"
            >
              <div>
                <h3 className="text-xs font-semibold text-fg">{item.label}</h3>
                <p className="mt-1 text-[11px] leading-relaxed text-fg-muted">{item.desc}</p>
              </div>
              <div className="mt-3 flex justify-end">
                <button
                  disabled={!!busyAction[item.action]}
                  onClick={() => handleQuickAction(item.action, item.params, item.label)}
                  className="rounded border border-line-strong bg-surface px-2.5 py-1 text-[11px] font-medium text-fg hover:bg-surface-2 disabled:opacity-50"
                >
                  {busyAction[item.action] ? 'Đang chạy…' : '▶ Chạy ngay'}
                </button>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Section 3: Event Log */}
      <section className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-fg">
              3. Nhật ký Tự khắc phục & Tự động hoá ({shown.length})
            </h2>
            <p className="text-xs text-fg-muted">
              Mọi vấn đề phát hiện và giải pháp hệ thống đã tự thực hiện đều được lưu lại tại đây
            </p>
          </div>
          <div className="flex flex-wrap gap-1">
            {['all', ...sources].map(s => (
              <button
                key={s}
                onClick={() => setSource(s)}
                className={`rounded px-2 py-1 text-[10px] border transition ${
                  source === s
                    ? 'border-line-strong bg-surface text-fg font-semibold'
                    : 'border-line text-fg-muted hover:bg-surface'
                }`}
              >
                {s === 'all' ? 'Tất cả' : SOURCE_LABEL[s] ?? s}
              </button>
            ))}
          </div>
        </div>

        {!loading && shown.length === 0 && (
          <p className="rounded-control border border-line bg-surface px-4 py-6 text-center text-xs text-fg-muted">
            Chưa có sự kiện tự động nào được ghi lại. Bạn có thể bấm "Quét & Tự khắc phục ngay" hoặc chạy một công cụ ở trên.
          </p>
        )}

        {shown.length > 0 && (
          <div className="overflow-x-auto rounded-control border border-line bg-surface">
            <table className="w-full text-[11px]">
              <thead>
                <tr className="border-b border-line bg-surface-2 text-left text-fg-muted">
                  <th className="px-3 py-2 font-medium">Thời điểm</th>
                  <th className="px-3 py-2 font-medium">Bộ phận xử lý</th>
                  <th className="px-3 py-2 font-medium">Hành động đã thực thi</th>
                  <th className="px-3 py-2 font-medium">Vấn đề / Lý do</th>
                  <th className="px-3 py-2 font-medium">Kết quả khắc phục</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((e, i) => (
                  <tr key={i} className="border-b border-line align-top hover:bg-surface-2">
                    <td className="px-3 py-2 font-mono text-fg-muted whitespace-nowrap">
                      {(e.timestamp || '').replace('T', ' ').slice(0, 19) || '—'}
                    </td>
                    <td className="px-3 py-2">
                      <span
                        className={`rounded border px-1.5 py-0.5 text-[10px] ${
                          SOURCE_STYLE[e.source] ?? 'bg-surface text-fg-2 border-line'
                        }`}
                      >
                        {SOURCE_LABEL[e.source] ?? e.source}
                      </span>
                    </td>
                    <td className="px-3 py-2 font-mono text-fg">{e.action || '—'}</td>
                    <td className="px-3 py-2 text-fg-muted">{e.reason || '—'}</td>
                    <td className="px-3 py-2 text-fg-2">{e.outcome || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

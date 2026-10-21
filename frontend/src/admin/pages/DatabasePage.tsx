import { useCallback, useEffect, useState } from 'react'
import { adminFetch, adminPost } from '../utils/adminFetch'
import { PageHeader } from '../shared/PageHeader'
import { Card } from '../shared/Card'
import { Button } from '../shared/Button'
import { SectionHeader } from '../shared/SectionHeader'
import { StatCard } from '../shared/StatCard'
import { TABLE, TABLE_SCROLL, TD, TD_NUM, TH, TH_NUM, TR } from '../shared/tableStyles'

/**
 * Cơ sở dữ liệu (21/10/2026): moving VidGrab's data from Supabase to the
 * Postgres Vibe Host provides (docs/MIGRATION-DB-VIBEHOST.md). For now a
 * read-only check of both databases; the copy step comes later. The backend
 * never returns a connection string or password.
 */

interface DsnInfo { set: boolean; kind?: string; port?: number; user?: string; database?: string; host_tail?: string }
interface TableInfo { name: string; kind: string; rows_est: number; size_kb: number }
interface Side {
  ok?: boolean; error?: string; server_version?: string; db_size_mb?: number; took_s?: number
  tables?: TableInfo[]; extensions?: string[]; auth_users_est?: number; auth_users_readable?: boolean
  target?: DsnInfo
}
interface Probe { at: number; pg_dump: string | null; psql: string | null; source: Side; target: Side }
interface Status { source_set: boolean; target_set: boolean; probe: Probe | null }
interface CopyRun {
  status: 'running' | 'ok' | 'mismatch' | 'failed' | 'refused'; started: number; finished?: number
  steps: { t: number; msg: string }[]; error?: string
  counts?: Record<string, number>; diff?: Record<string, [number | null, number | null]>
}
interface CopyStatus { running: boolean; last: CopyRun | null }

const COPY_VI: Record<CopyRun['status'], string> = {
  running: 'Đang chép…', ok: 'Xong — số dòng khớp', mismatch: 'Xong nhưng số dòng lệch',
  failed: 'Lỗi', refused: 'Chưa chép',
}

const KIND_VI: Record<string, string> = {
  'supabase-pooler': 'Supabase (qua pooler)',
  'supabase-direct': 'Supabase (kết nối thẳng)',
  other: 'Máy chủ khác',
}
const when = (t?: number) => (t ? new Date(t * 1000).toLocaleString('vi-VN') : '—')
const num = (n?: number) => (n == null ? '—' : n.toLocaleString('vi-VN'))

function SideCard({ title, side, set }: { title: string; side?: Side; set: boolean }) {
  const d = side?.target
  return (
    <Card as="section">
      <SectionHeader title={title} subtitle={!set ? 'Chưa cấu hình' : d?.kind ? KIND_VI[d.kind] ?? d.kind : ''} />
      <div className="flex flex-col gap-3 p-4">
        {!set ? <p className="text-sm text-fg-muted">Chưa đặt biến kết nối.</p> : !side ? (
          <p className="text-sm text-fg-muted">Chưa dò lần nào.</p>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3">
              <StatCard label="Kết nối" value={side.ok ? 'Được' : 'Lỗi'} tone={side.ok ? 'success' : 'danger'}
                hint={side.took_s != null ? `${side.took_s} s` : undefined} />
              <StatCard label="Phiên bản" value={side.server_version?.split(' ')[0] ?? '—'} />
              <StatCard label="Dung lượng" value={side.db_size_mb != null ? `${side.db_size_mb} MB` : '—'} />
              <StatCard label="Bảng / view" value={num(side.tables?.length)}
                hint={side.auth_users_est != null ? `~${num(side.auth_users_est)} tài khoản${side.auth_users_readable ? '' : ' (không đọc được)'}` : undefined} />
            </div>
            {side.error && <p className="break-words text-xs text-danger">{side.error}</p>}
            {d && d.set && (
              <p className="text-[11px] text-fg-muted">
                Cổng {d.port ?? '—'} · người dùng {d.user || '—'} · CSDL {d.database || '—'} · …{d.host_tail}
              </p>
            )}
          </>
        )}
      </div>
    </Card>
  )
}

export default function DatabasePage() {
  const [st, setSt] = useState<Status | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [copy, setCopy] = useState<CopyStatus | null>(null)
  const [replace, setReplace] = useState(false)
  const [copyBusy, setCopyBusy] = useState(false)

  const loadCopy = useCallback(async () => {
    try {
      setCopy(await adminFetch<CopyStatus>('/db-migration/copy'))
    } catch (e) {
      setErr((e as Error).message)
    }
  }, [])

  useEffect(() => { loadCopy() }, [loadCopy])
  // the lock, not the last status: a container restart mid-copy leaves status 'running'
  const running = !!copy?.running
  useEffect(() => {
    if (!running) return
    const t = setInterval(loadCopy, 3000)
    return () => clearInterval(t)
  }, [running, loadCopy])

  async function startCopy() {
    const typed = window.prompt(
      replace
        ? 'Chép đè: XOÁ các bảng đang có ở Postgres Vibe Host (trừ bản sao lưu) rồi chép lại từ Supabase. Gõ CHEP để tiếp tục.'
        : 'Chép toàn bộ dữ liệu từ Supabase sang Postgres Vibe Host. Website vẫn dùng Supabase. Gõ CHEP để tiếp tục.')
    if (typed !== 'CHEP') return
    setCopyBusy(true)
    setErr(null)
    try {
      await adminPost('/db-migration/copy', { confirm: 'CHEP', replace })
      await loadCopy()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setCopyBusy(false)
    }
  }

  const load = useCallback(async () => {
    try {
      setSt(await adminFetch<Status>('/db-migration'))
      setErr(null)
    } catch (e) {
      setErr((e as Error).message)
    }
  }, [])

  useEffect(() => { load() }, [load])

  async function probe() {
    setBusy(true)
    setErr(null)
    try {
      setSt(await adminPost<Status>('/db-migration/probe'))
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const p = st?.probe
  const tables = p?.source?.tables ?? []
  const targetNames = new Set((p?.target?.tables ?? []).map((t) => t.name))

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Cơ sở dữ liệu"
        description="Chuyển dữ liệu từ Supabase sang Postgres của Vibe Host. Chép xong website vẫn dùng Supabase cho tới bước chuyển."
        actions={<Button variant="primary" onClick={probe} disabled={busy}>{busy ? 'Đang dò...' : 'Dò lại'}</Button>}
      />

      {err && <div className="rounded-control border border-danger/30 bg-danger-soft px-4 py-3 text-sm text-danger">{err}</div>}

      {p && (
        <p className="text-xs text-fg-muted">
          Lần dò: {when(p.at)} · pg_dump: {p.pg_dump ?? 'chưa cài'}
        </p>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <SideCard title="Nguồn: Supabase" side={p?.source} set={!!st?.source_set} />
        <SideCard title="Đích: Postgres Vibe Host" side={p?.target} set={!!st?.target_set} />
      </div>

      <Card as="section">
        <SectionHeader title="Chép dữ liệu" subtitle="Chép sơ đồ, dữ liệu và tài khoản từ Supabase sang Postgres Vibe Host, rồi đếm lại từng bảng. Website vẫn dùng Supabase cho tới bước chuyển." />
        <div className="flex flex-col gap-3 p-4">
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="primary" onClick={startCopy}
              disabled={copyBusy || running || !st?.source_set || !st?.target_set}>
              {running ? 'Đang chép...' : 'Chép dữ liệu sang Vibe Host'}
            </Button>
            <label className="flex items-center gap-2 text-sm text-fg">
              <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} disabled={running} />
              Chép đè (xoá dữ liệu cũ ở đích trước)
            </label>
          </div>
          {copy?.last && (
            <>
              <p className={`text-sm ${copy.last.status === 'ok' ? 'text-success' : copy.last.status === 'running' ? 'text-fg' : 'text-danger'}`}>
                {COPY_VI[copy.last.status] ?? copy.last.status} · bắt đầu {when(copy.last.started)}
                {copy.last.finished ? ` · ${Math.round(copy.last.finished - copy.last.started)} s` : ''}
              </p>
              {copy.last.error && <p className="break-words text-xs text-danger">{copy.last.error}</p>}
              <ol className="space-y-0.5 font-mono text-[11px] text-fg-2">
                {copy.last.steps.map((s, i) => <li key={i}>+{s.t}s {s.msg}</li>)}
              </ol>
              {copy.last.diff && Object.keys(copy.last.diff).length > 0 && (
                <div className="text-xs text-danger">
                  Lệch: {Object.entries(copy.last.diff).map(([t, [a, b]]) => `${t} (${a ?? '—'} → ${b ?? '—'})`).join(', ')}
                </div>
              )}
              {copy.last.counts && (
                <p className="text-xs text-fg-muted">
                  Đã đếm {Object.keys(copy.last.counts).length} bảng · tài khoản: {num(copy.last.counts['auth.users'])} ·
                  download_jobs: {num(copy.last.counts['download_jobs'])}
                </p>
              )}
            </>
          )}
        </div>
      </Card>

      {tables.length > 0 && (
        <Card as="section">
          <SectionHeader title="Bảng ở Supabase" subtitle="Số dòng là ước lượng của Postgres (không đếm lại, để khỏi tốn đọc/ghi đĩa)" />
          <div className={TABLE_SCROLL}>
            <table className={TABLE}>
              <thead>
                <tr>
                  <th className={TH}>Tên</th>
                  <th className={TH}>Loại</th>
                  <th className={TH_NUM}>Số dòng (ước)</th>
                  <th className={TH_NUM}>Dung lượng</th>
                  <th className={TH}>Đã có ở đích</th>
                </tr>
              </thead>
              <tbody>
                {tables.map((t) => (
                  <tr key={t.name} className={TR}>
                    <td className={TD}><span className="font-mono text-xs">{t.name}</span></td>
                    <td className={TD}>{t.kind}</td>
                    <td className={TD_NUM}>{num(t.rows_est)}</td>
                    <td className={TD_NUM}>{t.size_kb >= 1024 ? `${(t.size_kb / 1024).toFixed(1)} MB` : `${t.size_kb} KB`}</td>
                    <td className={TD}>{targetNames.has(t.name) ? 'Có' : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}

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
        description="Chuyển dữ liệu từ Supabase sang Postgres của Vibe Host. Hiện chỉ kiểm tra hai bên, chưa chép gì."
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

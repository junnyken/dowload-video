import { useCallback, useEffect, useState } from 'react'
import { adminFetch, adminPost } from '../utils/adminFetch'
import { PageHeader } from '../shared/PageHeader'
import { Button } from '../shared/Button'
import { EmptyState } from '../shared/EmptyState'
import { ErrorState } from '../shared/ErrorState'
import { BlockSkeleton } from '../shared/LoadingSkeleton'
import { TABLE_CARD, TABLE_SCROLL, TABLE, TH, TR, TD } from '../shared/tableStyles'

// wording: BA review
interface TakedownItem {
  id: string
  created_at: string
  status: Status
  name: string
  email: string
  capacity: 'owner' | 'agent'
  content_url: string
  related: string
  description: string
}
type Status = 'new' | 'reviewing' | 'done' | 'rejected'

const STATUS_VI: Record<Status, string> = {
  new: 'Mới', reviewing: 'Đang xem xét', done: 'Đã xử lý', rejected: 'Từ chối',
}
const CAPACITY_VI = { owner: 'Chủ sở hữu', agent: 'Đại diện' }

export default function TakedownAdminPage() {
  const [items, setItems] = useState<TakedownItem[] | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState<string | null>(null)

  const load = useCallback(async () => {
    setErr('')
    try {
      const d = await adminFetch<{ items: TakedownItem[] }>('/takedown')
      setItems(d.items)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Lỗi không xác định')
    }
  }, [])
  useEffect(() => { load() }, [load])

  async function change(id: string, status: Status) {
    setBusy(id)
    try {
      await adminPost(`/takedown/${id}/status`, { status })
      setItems((cur) => cur && cur.map((i) => (i.id === id ? { ...i, status } : i)))
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Không đổi được trạng thái')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="Yêu cầu gỡ nội dung"
        description="Gửi từ trang /takedown, mới nhất ở trên. Tối đa 1000 yêu cầu gần nhất."
        actions={<Button variant="secondary" size="sm" onClick={load}>Làm mới</Button>}
      />
      {err && <ErrorState compact title="Có lỗi" message={err} onRetry={load} />}
      {!items && !err && <BlockSkeleton height="h-40" />}
      {items && items.length === 0 && <EmptyState compact title="Chưa có yêu cầu nào" />}
      {items && items.length > 0 && (
        <div className={TABLE_CARD}>
          <div className={TABLE_SCROLL}>
            <table className={TABLE}>
              <thead>
                <tr>
                  <th className={TH}>Thời gian</th>
                  <th className={TH}>Người gửi</th>
                  <th className={TH}>Nội dung</th>
                  <th className={TH}>Trạng thái</th>
                </tr>
              </thead>
              <tbody>
                {items.map((i) => (
                  <tr key={i.id} className={`${TR} align-top`}>
                    <td className={`${TD} whitespace-nowrap`}>{new Date(i.created_at).toLocaleString('vi-VN')}</td>
                    <td className={TD}>
                      <div>{i.name}</div>
                      <a href={`mailto:${i.email}`} className="text-xs text-accent-text underline">{i.email}</a>
                      <div className="text-xs text-fg-muted">{CAPACITY_VI[i.capacity] ?? i.capacity}</div>
                    </td>
                    <td className={`${TD} max-w-md`}>
                      <a href={i.content_url} target="_blank" rel="noopener noreferrer"
                        className="block break-all text-xs text-accent-text underline">{i.content_url}</a>
                      {i.related && <div className="mt-1 break-all text-xs text-fg-2">VidGrab: {i.related}</div>}
                      <p className="mt-1 whitespace-pre-wrap break-words text-xs text-fg-2">{i.description}</p>
                    </td>
                    <td className={TD}>
                      <select
                        aria-label="Trạng thái"
                        value={i.status}
                        disabled={busy === i.id}
                        onChange={(e) => change(i.id, e.target.value as Status)}
                        className="h-8 rounded-control border border-line bg-surface px-2 text-xs text-fg"
                      >
                        {(Object.keys(STATUS_VI) as Status[]).map((s) => (
                          <option key={s} value={s}>{STATUS_VI[s]}</option>
                        ))}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

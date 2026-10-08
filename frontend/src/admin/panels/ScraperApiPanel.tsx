import { useState, useEffect, useCallback, useRef } from 'react'
import {
  fetchScraperKeys,
  refreshScraperCredits,
  addScraperKey,
  removeScraperKey,
  removeAllScraperKeys,
  type ScraperKey,
} from '../api/scraperapi'
import { cn } from '../utils/cn'

const BTN =
  'px-3 py-1.5 text-xs rounded-control border border-line bg-surface font-medium text-fg hover:border-line-strong hover:bg-surface-2 disabled:opacity-50'
const BTN_DANGER =
  'px-3 py-1.5 text-xs rounded-control border border-line bg-surface font-medium text-danger hover:border-line-strong hover:bg-surface-2 disabled:opacity-50'
const TH = 'py-2 pr-3 text-left font-mono text-[10px] uppercase tracking-widest text-fg-muted'
const CONFIRM_MS = 4000

function statusLabel(k: ScraperKey): string {
  if (k.active) return 'Đang dùng'
  if (k.exhausted) return 'Hết lượt'
  return 'Chờ'
}

export function ScraperApiPanel() {
  const [keys, setKeys] = useState<ScraperKey[]>([])
  const [totalCredits, setTotalCredits] = useState(0)
  const [keyCount, setKeyCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState('')
  const [confirmId, setConfirmId] = useState<string | null>(null)
  const confirmTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const noticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [newKey, setNewKey] = useState('')
  const [adding, setAdding] = useState(false)
  const [addError, setAddError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    try {
      const data = await fetchScraperKeys()
      setKeys(data.keys ?? [])
      setTotalCredits(data.total_credits ?? 0)
      setKeyCount(data.key_count ?? 0)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Không tải được dữ liệu')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    return () => {
      if (confirmTimer.current) clearTimeout(confirmTimer.current)
      if (noticeTimer.current) clearTimeout(noticeTimer.current)
    }
  }, [load])

  function arm(id: string) {
    setConfirmId(id)
    if (confirmTimer.current) clearTimeout(confirmTimer.current)
    confirmTimer.current = setTimeout(() => setConfirmId(null), CONFIRM_MS)
  }

  function disarm() {
    if (confirmTimer.current) clearTimeout(confirmTimer.current)
    setConfirmId(null)
  }

  async function handleRefresh() {
    setBusy(true)
    setActionError('')
    try {
      const data = await refreshScraperCredits()
      setKeys(data.keys ?? [])
      setTotalCredits(data.total_credits ?? 0)
      setKeyCount((data.keys ?? []).length)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Không kiểm lại được')
    } finally {
      setBusy(false)
    }
  }

  async function handleRemove(index: number) {
    const id = `k${index}`
    if (confirmId !== id) { arm(id); return }
    disarm()
    setBusy(true)
    setActionError('')
    try {
      await removeScraperKey(index)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Không xoá được')
    } finally {
      // Indexes shift after a delete — always reload, never batch.
      await load()
      setBusy(false)
    }
  }

  async function handleRemoveAll() {
    if (confirmId !== 'all') { arm('all'); return }
    disarm()
    setBusy(true)
    setActionError('')
    try {
      await removeAllScraperKeys()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Không gỡ được')
    } finally {
      await load()
      setBusy(false)
    }
  }

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault()
    const key = newKey.trim()
    if (!key) return
    setAdding(true)
    setAddError('')
    try {
      const res = await addScraperKey(key)
      setNewKey('')
      setNotice(`Đã thêm ${res.key_prefix} — ${res.credits} credit`)
      if (noticeTimer.current) clearTimeout(noticeTimer.current)
      noticeTimer.current = setTimeout(() => setNotice(''), 5000)
      await load()
    } catch (err) {
      setAddError(err instanceof Error ? err.message : 'Không thêm được khoá')
    } finally {
      setAdding(false)
    }
  }

  return (
    <div className="rounded-card border border-line bg-surface shadow-card p-5">
      {/* wording: BA review */}
      <h3 className="mb-1 font-mono text-[10px] font-semibold uppercase tracking-widest text-fg-muted">ScraperAPI</h3>
      <p className="mb-4 text-[11px] text-fg-muted">
        Dịch vụ trả phí, chỉ là đường dự phòng cuối (Douyin; TikTok/Instagram/YouTube khi không có proxy). Không có khoá = không dùng ScraperAPI.
      </p>

      {loading ? (
        <p className="text-sm text-fg-muted animate-pulse">Đang tải…</p>
      ) : error ? (
        <div className="flex flex-wrap items-center gap-3">
          <p className="min-w-0 break-words text-sm text-danger">{error}</p>
          <button type="button" onClick={() => { setLoading(true); load() }} className={BTN}>Thử lại</button>
        </div>
      ) : (
        <>
          <p className={cn('mb-3 text-sm', keyCount === 0 ? 'text-fg-muted' : 'text-success')}>
            {keyCount === 0
              ? 'Đang TẮT — không có khoá nào.'
              : `Đang BẬT — ${keyCount} khoá · tổng ${totalCredits} credit`}
          </p>

          {keys.length > 0 && (
            <div className="mb-3 overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-line">
                    <th className={TH}>Khoá</th>
                    <th className={TH}>Credit</th>
                    <th className={TH}>Trạng thái</th>
                    <th className="py-2" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {keys.map(k => (
                    <tr key={k.key_hash || k.index}>
                      <td className="min-w-0 py-2 pr-3 font-mono text-fg-2">{k.key_prefix}</td>
                      <td className="py-2 pr-3 font-mono">
                        {k.credits === null
                          ? <span className="text-danger">Không kiểm được</span>
                          : <span className="text-fg-2">{k.credits}</span>}
                      </td>
                      <td className={cn('py-2 pr-3 whitespace-nowrap', k.active ? 'text-success' : k.exhausted ? 'text-warning' : 'text-fg-muted')}>
                        {statusLabel(k)}
                      </td>
                      <td className="py-2 text-right">
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => handleRemove(k.index)}
                          className={BTN_DANGER}
                        >
                          {confirmId === `k${k.index}` ? 'Chắc chắn?' : 'Xoá'}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="mb-4 flex flex-wrap items-center gap-2">
            <button type="button" onClick={handleRefresh} disabled={busy} className={BTN}>
              Kiểm lại credit
            </button>
            {keyCount > 0 && (
              <button type="button" onClick={handleRemoveAll} disabled={busy} className={BTN_DANGER}>
                {confirmId === 'all' ? 'Chắc chắn?' : 'Gỡ tất cả — tắt ScraperAPI'}
              </button>
            )}
          </div>
          {actionError && <p className="mb-3 break-words text-xs text-danger">{actionError}</p>}
        </>
      )}

      <form onSubmit={handleAdd} className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          <input
            type="password"
            autoComplete="off"
            placeholder="Dán khoá ScraperAPI"
            aria-label="Khoá ScraperAPI"
            value={newKey}
            onChange={e => setNewKey(e.target.value)}
            className="w-full rounded-card border border-line bg-surface shadow-card px-3 py-2 text-sm text-fg-2 placeholder:text-fg-muted outline-none focus:border-line-strong"
          />
        </div>
        <button
          type="submit"
          disabled={!newKey.trim() || adding}
          className="rounded-card bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent-hover disabled:opacity-50 transition-colors"
        >
          Thêm khoá
        </button>
      </form>
      {addError && <p className="mt-2 break-words text-xs text-danger">{addError}</p>}
      {notice && <p className="mt-2 break-words text-xs text-success">{notice}</p>}
    </div>
  )
}

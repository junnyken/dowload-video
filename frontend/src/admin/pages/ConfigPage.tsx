import { useEffect, useState, useRef } from 'react'
import { adminFetch, adminPost } from '../utils/adminFetch'
import { getAdminToken } from '../hooks/useAdminAuth'
import { API_BASE } from '../../lib/apiBase'

interface ConfigEntry {
  key: string
  value: string
  description: string
}

interface ConfigResponse {
  entries: ConfigEntry[]
}

const TRUNCATE_LEN = 60

function truncate(val: string) {
  if (val.length <= TRUNCATE_LEN) return val
  return val.slice(0, TRUNCATE_LEN) + '…'
}

// ── Apify token (China access layer, Douyin) ────────────────────────────────
// The backend never returns the token: only source, last 4 characters,
// account and usage. The input is cleared right after a save attempt.

interface ApifyTokenStatus {
  configured: boolean
  source: 'admin' | 'env' | 'none'
  last4: string | null
  set_at: string | null
  validated_at: string | null
  account: { username?: string | null; plan?: string | null } | null
  usage: { monthly_usage_usd?: number | null; max_monthly_usage_usd?: number | null } | null
  env_fallback_configured?: boolean
  note?: string
}

const APIFY_PATH = '/china-platforms/apify/token'

async function apifyCall<T>(path: string, method: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getAdminToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(`${API_BASE}/api/v1/admin${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    try {
      const data = await res.json()
      const d = data?.detail
      msg = typeof d === 'string' ? d : d?.message ?? msg
    } catch { /* ignore */ }
    throw new Error(msg)
  }
  return res.json() as Promise<T>
}

function fmtTime(iso: string | null) {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('vi-VN')
}

function fmtUsd(v: number | null | undefined) {
  return typeof v === 'number' ? `$${v.toFixed(2)}` : '—'
}

function ApifyTokenCard() {
  const [status, setStatus] = useState<ApifyTokenStatus | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [tokenInput, setTokenInput] = useState('')
  const [busy, setBusy] = useState<null | 'save' | 'test' | 'delete'>(null)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const load = async () => {
    try {
      setStatus(await apifyCall<ApifyTokenStatus>(APIFY_PATH, 'GET'))
      setLoadError(null)
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Không tải được trạng thái token')
    }
  }

  useEffect(() => {
    load()
  }, [])

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    const value = tokenInput.trim()
    if (!value) {
      setMsg({ ok: false, text: 'Hãy dán token Apify trước khi lưu.' })
      return
    }
    const reason = window.prompt('Lý do lưu token (bắt buộc):')
    if (!reason || reason.trim().length < 3) {
      setMsg({ ok: false, text: 'Cần nhập lý do (ít nhất 3 ký tự).' })
      return
    }
    setBusy('save')
    setMsg(null)
    try {
      const next = await apifyCall<ApifyTokenStatus>(APIFY_PATH, 'POST', { token: value, reason: reason.trim() })
      setStatus(next)
      setMsg({ ok: true, text: 'Đã kiểm tra và lưu token.' })
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : 'Lưu token thất bại' })
    } finally {
      setTokenInput('')
      setBusy(null)
    }
  }

  const retest = async () => {
    setBusy('test')
    setMsg(null)
    try {
      setStatus(await apifyCall<ApifyTokenStatus>(`${APIFY_PATH}/test`, 'POST'))
      setMsg({ ok: true, text: 'Token vẫn hợp lệ.' })
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : 'Kiểm tra thất bại' })
    } finally {
      setBusy(null)
    }
  }

  const remove = async () => {
    if (!window.confirm('Xoá token Apify đã lưu trong admin?')) return
    const reason = window.prompt('Lý do xoá token (bắt buộc):')
    if (!reason || reason.trim().length < 3) {
      setMsg({ ok: false, text: 'Cần nhập lý do (ít nhất 3 ký tự).' })
      return
    }
    setBusy('delete')
    setMsg(null)
    try {
      const next = await apifyCall<ApifyTokenStatus>(APIFY_PATH, 'DELETE', { reason: reason.trim() })
      setStatus(next)
      setMsg({ ok: true, text: next.note ?? 'Đã xoá token.' })
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : 'Xoá token thất bại' })
    } finally {
      setBusy(null)
    }
  }

  const sourceLabel = status?.source === 'admin' ? 'Admin' : status?.source === 'env' ? 'biến môi trường' : '—'
  const btn =
    'px-4 py-2 rounded-control border border-line bg-surface hover:bg-surface-2 disabled:opacity-40 disabled:cursor-not-allowed text-sm text-fg-2 transition-colors'

  return (
    <div className="rounded-card border border-line bg-surface shadow-card p-5">
      <h2 className="text-sm font-semibold text-fg-2 mb-4 uppercase tracking-wider">Apify (lấy video Douyin)</h2>

      {loadError ? (
        <div className="rounded-control bg-danger-soft border border-danger/30 px-4 py-3 text-danger text-sm mb-4">
          {loadError}
        </div>
      ) : !status ? (
        <div className="flex items-center gap-2 text-fg-muted text-sm mb-4">
          <span className="inline-block h-4 w-4 rounded-full border-2 border-line-strong border-t-transparent animate-spin" />
          Đang tải...
        </div>
      ) : (
        <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-sm mb-4">
          <div className="flex gap-2">
            <dt className="text-fg-muted">Trạng thái:</dt>
            <dd className={status.configured ? 'text-success' : 'text-fg-muted'}>
              {status.configured ? 'Đã cấu hình' : 'Chưa cấu hình'}
            </dd>
          </div>
          <div className="flex gap-2">
            <dt className="text-fg-muted">Nguồn:</dt>
            <dd className="text-fg-2">{sourceLabel}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="text-fg-muted">Token:</dt>
            <dd className="font-mono text-fg-2">{status.last4 ? `••••${status.last4}` : '—'}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="text-fg-muted">Tài khoản:</dt>
            <dd className="text-fg-2">
              {status.account?.username ?? '—'}
              {status.account?.plan ? ` (${status.account.plan})` : ''}
            </dd>
          </div>
          <div className="flex gap-2">
            <dt className="text-fg-muted">Lần kiểm tra gần nhất:</dt>
            <dd className="text-fg-2">{fmtTime(status.validated_at)}</dd>
          </div>
          {status.usage && (
            <div className="flex gap-2">
              <dt className="text-fg-muted">Mức dùng tháng / giới hạn:</dt>
              <dd className="font-mono text-fg-2">
                {fmtUsd(status.usage.monthly_usage_usd)} / {fmtUsd(status.usage.max_monthly_usage_usd)}
              </dd>
            </div>
          )}
        </dl>
      )}

      <form onSubmit={save} className="flex flex-col gap-3">
        <div className="flex flex-col sm:flex-row gap-3">
          <input
            type="password"
            autoComplete="off"
            value={tokenInput}
            onChange={(e) => setTokenInput(e.target.value)}
            placeholder="Dán token Apify mới"
            className="flex-1 bg-surface border border-line rounded-control px-3 py-2 text-sm text-fg font-mono outline-none focus:ring-1 focus:ring-line-strong transition-colors"
          />
          <button
            type="submit"
            disabled={busy !== null}
            className="px-5 py-2 rounded-control bg-accent hover:bg-accent-hover disabled:opacity-40 disabled:cursor-not-allowed text-accent-fg text-sm font-medium transition-colors"
          >
            {busy === 'save' ? 'Đang kiểm tra...' : 'Lưu token'}
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" onClick={retest} disabled={busy !== null || !status?.configured} className={btn}>
            {busy === 'test' ? 'Đang kiểm tra...' : 'Kiểm tra lại'}
          </button>
          <button
            type="button"
            onClick={remove}
            disabled={busy !== null || status?.source !== 'admin'}
            className="px-4 py-2 rounded-control text-sm text-fg-muted hover:text-danger hover:bg-danger-soft disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {busy === 'delete' ? 'Đang xoá...' : 'Xoá token'}
          </button>
        </div>
        {msg && <p className={`text-xs ${msg.ok ? 'text-success' : 'text-danger'}`}>{msg.text}</p>}
        <p className="text-xs text-fg-muted">
          Đặt giới hạn chi tiêu trong trang Billing của Apify trước khi lưu token.
        </p>
      </form>
    </div>
  )
}

export default function ConfigPage() {
  const [entries, setEntries] = useState<ConfigEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Inline edit state: key -> pending new value string
  const [editingKey, setEditingKey] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')
  const editInputRef = useRef<HTMLInputElement>(null)

  // Add form state
  const [addKey, setAddKey] = useState('')
  const [addValue, setAddValue] = useState('')
  const [addDesc, setAddDesc] = useState('')
  const [addError, setAddError] = useState<string | null>(null)
  const [addSaving, setAddSaving] = useState(false)

  // Per-row operation feedback
  const [rowMsg, setRowMsg] = useState<Record<string, string>>({})
  const [deletingKey, setDeletingKey] = useState<string | null>(null)

  const fetchConfig = async () => {
    try {
      const res = await adminFetch<ConfigResponse>('/config')
      setEntries(res.entries)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load config')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchConfig()
  }, [])

  useEffect(() => {
    if (editingKey && editInputRef.current) {
      editInputRef.current.focus()
      editInputRef.current.select()
    }
  }, [editingKey])

  const startEdit = (entry: ConfigEntry) => {
    setEditingKey(entry.key)
    setEditValue(entry.value)
  }

  const cancelEdit = () => {
    setEditingKey(null)
    setEditValue('')
  }

  const saveEdit = async (key: string) => {
    if (editValue === entries.find((e) => e.key === key)?.value) {
      cancelEdit()
      return
    }
    const entry = entries.find((e) => e.key === key)
    try {
      await adminPost(`/config/${encodeURIComponent(key)}`, {
        value: editValue,
        description: entry?.description ?? '',
      })
      setEntries((prev) =>
        prev.map((e) => (e.key === key ? { ...e, value: editValue } : e)),
      )
      setRowMsg((prev) => ({ ...prev, [key]: 'Saved' }))
      setTimeout(() => setRowMsg((prev) => { const n = { ...prev }; delete n[key]; return n }), 2000)
    } catch (err) {
      setRowMsg((prev) => ({
        ...prev,
        [key]: `Error: ${err instanceof Error ? err.message : 'Save failed'}`,
      }))
    }
    cancelEdit()
  }

  const handleDelete = async (key: string) => {
    if (!window.confirm(`Delete config key "${key}"?`)) return
    setDeletingKey(key)
    try {
      await adminFetch(`/config/${encodeURIComponent(key)}`, { method: 'DELETE' })
      setEntries((prev) => prev.filter((e) => e.key !== key))
    } catch (err) {
      setRowMsg((prev) => ({
        ...prev,
        [key]: `Error: ${err instanceof Error ? err.message : 'Delete failed'}`,
      }))
    } finally {
      setDeletingKey(null)
    }
  }

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!addKey.trim()) {
      setAddError('Key is required')
      return
    }
    setAddSaving(true)
    setAddError(null)
    try {
      await adminPost(`/config/${encodeURIComponent(addKey.trim())}`, {
        value: addValue,
        description: addDesc,
      })
      setEntries((prev) => {
        const existing = prev.findIndex((e) => e.key === addKey.trim())
        const next = { key: addKey.trim(), value: addValue, description: addDesc }
        if (existing >= 0) {
          const copy = [...prev]
          copy[existing] = next
          return copy
        }
        return [...prev, next]
      })
      setAddKey('')
      setAddValue('')
      setAddDesc('')
    } catch (err) {
      setAddError(err instanceof Error ? err.message : 'Failed to save')
    } finally {
      setAddSaving(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <h1 className="text-lg font-semibold tracking-tight text-fg">Config</h1>
        {!loading && (
          <span className="inline-flex min-w-[1.5rem] items-center justify-center rounded-md border border-line bg-surface-2 px-2 py-0.5 font-mono text-xs font-semibold text-fg-2">
            {entries.length}
          </span>
        )}
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-fg-muted text-sm py-8">
          <span className="inline-block h-4 w-4 rounded-full border-2 border-line-strong border-t-transparent animate-spin" />
          Loading config...
        </div>
      ) : error ? (
        <div className="rounded-control bg-danger-soft border border-danger/30 px-4 py-3 text-danger text-sm mb-4">
          {error}
        </div>
      ) : entries.length === 0 ? (
        <div className="text-center py-12 text-fg-muted text-sm mb-8">
          No config entries yet. Add one below.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-card border border-line mb-8">
          <table className="w-full text-sm">
            <thead className="bg-surface-2 text-fg-muted uppercase font-mono text-[10px] tracking-widest">
              <tr>
                <th className="px-4 py-3 text-left w-40">Key</th>
                <th className="px-4 py-3 text-left">Value</th>
                <th className="px-4 py-3 text-left">Description</th>
                <th className="px-4 py-3 text-center w-20"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {entries.map((entry) => (
                <tr key={entry.key} className="hover:bg-surface-2 transition-colors">
                  {/* Key */}
                  <td className="px-4 py-3 font-mono text-fg-2 align-top">
                    {entry.key}
                  </td>

                  {/* Value — inline editable */}
                  <td
                    className="px-4 py-3 align-top cursor-pointer"
                    title="Click to edit"
                    onClick={() => editingKey !== entry.key && startEdit(entry)}
                  >
                    {editingKey === entry.key ? (
                      <input
                        ref={editInputRef}
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        onBlur={() => saveEdit(entry.key)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') saveEdit(entry.key)
                          if (e.key === 'Escape') cancelEdit()
                        }}
                        className="w-full bg-surface border border-line rounded px-2 py-1 text-fg text-sm font-mono outline-none focus:ring-1 focus:ring-line-strong"
                        onClick={(e) => e.stopPropagation()}
                      />
                    ) : (
                      <span className="font-mono text-fg-2 group">
                        {truncate(entry.value) || <span className="text-fg-muted italic">empty</span>}
                        {rowMsg[entry.key] && (
                          <span className={`ml-2 text-xs ${rowMsg[entry.key].startsWith('Error') ? 'text-danger' : 'text-success'}`}>
                            {rowMsg[entry.key]}
                          </span>
                        )}
                      </span>
                    )}
                  </td>

                  {/* Description */}
                  <td className="px-4 py-3 text-fg-muted align-top text-xs leading-relaxed">
                    {entry.description || <span className="text-fg-muted italic">—</span>}
                  </td>

                  {/* Delete */}
                  <td className="px-4 py-3 text-center align-top">
                    <button
                      onClick={() => handleDelete(entry.key)}
                      disabled={deletingKey === entry.key}
                      className="text-fg-muted hover:text-danger disabled:opacity-40 transition-colors text-xs px-2 py-1 rounded hover:bg-danger-soft"
                      title="Delete"
                    >
                      {deletingKey === entry.key ? '...' : 'Delete'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <ApifyTokenCard />

      {/* Add form */}
      <div className="rounded-card border border-line bg-surface shadow-card p-5">
        <h2 className="text-sm font-semibold text-fg-2 mb-4 uppercase tracking-wider">Add / Update Entry</h2>
        <form onSubmit={handleAdd} className="flex flex-col gap-3">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className="block text-xs text-fg-muted mb-1">Key <span className="text-danger">*</span></label>
              <input
                value={addKey}
                onChange={(e) => setAddKey(e.target.value)}
                placeholder="config_key"
                className="w-full bg-surface border border-line focus:border-line rounded-control px-3 py-2 text-sm text-fg font-mono outline-none focus:ring-1 focus:ring-line-strong transition-colors"
              />
            </div>
            <div>
              <label className="block text-xs text-fg-muted mb-1">Value</label>
              <input
                value={addValue}
                onChange={(e) => setAddValue(e.target.value)}
                placeholder="value"
                className="w-full bg-surface border border-line focus:border-line rounded-control px-3 py-2 text-sm text-fg font-mono outline-none focus:ring-1 focus:ring-line-strong transition-colors"
              />
            </div>
            <div>
              <label className="block text-xs text-fg-muted mb-1">Description</label>
              <input
                value={addDesc}
                onChange={(e) => setAddDesc(e.target.value)}
                placeholder="Optional description"
                className="w-full bg-surface border border-line focus:border-line rounded-control px-3 py-2 text-sm text-fg outline-none focus:ring-1 focus:ring-line-strong transition-colors"
              />
            </div>
          </div>

          {addError && (
            <p className="text-danger text-xs">{addError}</p>
          )}

          <div className="flex items-center gap-3 pt-1">
            <button
              type="submit"
              disabled={addSaving}
              className="flex items-center gap-2 px-5 py-2 rounded-control bg-accent hover:bg-accent-hover disabled:opacity-40 disabled:cursor-not-allowed text-accent-fg text-sm font-medium transition-colors"
            >
              {addSaving ? (
                <>
                  <span className="inline-block h-3.5 w-3.5 rounded-full border-2 border-line-strong border-t-transparent animate-spin" />
                  Saving...
                </>
              ) : (
                'Save'
              )}
            </button>
            <span className="text-xs text-fg-muted">
              If key already exists, its value will be updated.
            </span>
          </div>
        </form>
      </div>
    </div>
  )
}

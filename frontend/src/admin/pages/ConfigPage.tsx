import { useEffect, useState, useRef } from 'react'
import { adminFetch, adminPost } from '../utils/adminFetch'

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
    <div className="min-h-screen bg-gray-950 text-gray-100 p-6">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <h1 className="text-2xl font-bold text-white">Config</h1>
        {!loading && (
          <span className="inline-flex items-center justify-center rounded-full bg-indigo-600 text-white text-xs font-semibold px-2.5 py-0.5 min-w-[1.5rem]">
            {entries.length}
          </span>
        )}
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-gray-400 text-sm py-8">
          <span className="inline-block h-4 w-4 rounded-full border-2 border-gray-400 border-t-transparent animate-spin" />
          Loading config...
        </div>
      ) : error ? (
        <div className="rounded-lg bg-red-900/40 border border-red-700 px-4 py-3 text-red-300 text-sm mb-4">
          {error}
        </div>
      ) : entries.length === 0 ? (
        <div className="text-center py-12 text-gray-500 text-sm mb-8">
          No config entries yet. Add one below.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-gray-800 mb-8">
          <table className="w-full text-sm">
            <thead className="bg-gray-900 text-gray-400 uppercase text-xs tracking-wider">
              <tr>
                <th className="px-4 py-3 text-left w-40">Key</th>
                <th className="px-4 py-3 text-left">Value</th>
                <th className="px-4 py-3 text-left">Description</th>
                <th className="px-4 py-3 text-center w-20"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {entries.map((entry) => (
                <tr key={entry.key} className="bg-gray-900/50 hover:bg-gray-800/40 transition-colors">
                  {/* Key */}
                  <td className="px-4 py-3 font-mono text-indigo-300 align-top">
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
                        className="w-full bg-gray-800 border border-indigo-500 rounded px-2 py-1 text-gray-100 text-sm font-mono outline-none focus:ring-1 focus:ring-indigo-400"
                        onClick={(e) => e.stopPropagation()}
                      />
                    ) : (
                      <span className="font-mono text-gray-300 group">
                        {truncate(entry.value) || <span className="text-gray-600 italic">empty</span>}
                        {rowMsg[entry.key] && (
                          <span className={`ml-2 text-xs ${rowMsg[entry.key].startsWith('Error') ? 'text-red-400' : 'text-emerald-400'}`}>
                            {rowMsg[entry.key]}
                          </span>
                        )}
                      </span>
                    )}
                  </td>

                  {/* Description */}
                  <td className="px-4 py-3 text-gray-400 align-top text-xs leading-relaxed">
                    {entry.description || <span className="text-gray-600 italic">—</span>}
                  </td>

                  {/* Delete */}
                  <td className="px-4 py-3 text-center align-top">
                    <button
                      onClick={() => handleDelete(entry.key)}
                      disabled={deletingKey === entry.key}
                      className="text-gray-600 hover:text-red-400 disabled:opacity-40 transition-colors text-xs px-2 py-1 rounded hover:bg-red-900/30"
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

      {/* Add form */}
      <div className="rounded-xl border border-gray-800 bg-gray-900/60 p-5">
        <h2 className="text-sm font-semibold text-gray-300 mb-4 uppercase tracking-wider">Add / Update Entry</h2>
        <form onSubmit={handleAdd} className="flex flex-col gap-3">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">Key <span className="text-red-400">*</span></label>
              <input
                value={addKey}
                onChange={(e) => setAddKey(e.target.value)}
                placeholder="config_key"
                className="w-full bg-gray-800 border border-gray-700 focus:border-indigo-500 rounded-lg px-3 py-2 text-sm text-gray-100 font-mono outline-none focus:ring-1 focus:ring-indigo-500 transition-colors"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Value</label>
              <input
                value={addValue}
                onChange={(e) => setAddValue(e.target.value)}
                placeholder="value"
                className="w-full bg-gray-800 border border-gray-700 focus:border-indigo-500 rounded-lg px-3 py-2 text-sm text-gray-100 font-mono outline-none focus:ring-1 focus:ring-indigo-500 transition-colors"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Description</label>
              <input
                value={addDesc}
                onChange={(e) => setAddDesc(e.target.value)}
                placeholder="Optional description"
                className="w-full bg-gray-800 border border-gray-700 focus:border-indigo-500 rounded-lg px-3 py-2 text-sm text-gray-100 outline-none focus:ring-1 focus:ring-indigo-500 transition-colors"
              />
            </div>
          </div>

          {addError && (
            <p className="text-red-400 text-xs">{addError}</p>
          )}

          <div className="flex items-center gap-3 pt-1">
            <button
              type="submit"
              disabled={addSaving}
              className="flex items-center gap-2 px-5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-medium transition-colors"
            >
              {addSaving ? (
                <>
                  <span className="inline-block h-3.5 w-3.5 rounded-full border-2 border-white border-t-transparent animate-spin" />
                  Saving...
                </>
              ) : (
                'Save'
              )}
            </button>
            <span className="text-xs text-gray-600">
              If key already exists, its value will be updated.
            </span>
          </div>
        </form>
      </div>
    </div>
  )
}

import { useEffect, useState, useCallback } from 'react'
import { adminFetch, adminPost } from '../utils/adminFetch'
import { cn } from '../utils/cn'

interface StatsResponse {
  total_presets: number
  total_prefs: number
  system_count: number
  default_count: number
  by_platform: Record<string, number>
  prefs_by_platform: Record<string, number>
  popular_settings_keys: [string, number][]
}

interface Preset {
  id: string
  user_id: string
  name: string
  platform: string | null
  settings: Record<string, unknown>
  is_default: boolean
  is_system: boolean
  sort_order: number
  created_at: string
}

interface SystemPresetsResponse {
  presets: Preset[]
}

const PLATFORM_ICONS: Record<string, string> = {
  tiktok: '♪',
  youtube: '▶',
  instagram: '◈',
  spotify: '●',
  twitter: '◆',
  facebook: '◉',
  soundcloud: '◫',
  threads: '◬',
  universal: '◭',
}

const SETTINGS_KEY_LABELS: Record<string, string> = {
  quality: 'Quality',
  format: 'Format',
  no_watermark: 'No Watermark',
  download_subs: 'Subtitles',
  cloud_save: 'Cloud Save',
  sub_to_cloud: 'Sub→Cloud',
  use_cookies: 'Use Cookies',
  zip: 'ZIP',
  output: 'Output',
  duration: 'Duration',
}

export default function PresetsPage() {
  const [stats, setStats] = useState<StatsResponse | null>(null)
  const [systemPresets, setSystemPresets] = useState<Preset[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<Record<string, boolean>>({})

  // Create form
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ name: '', platform: '', sort_order: '0' })
  const [creating, setCreating] = useState(false)
  const [createMsg, setCreateMsg] = useState('')

  const fetchAll = useCallback(async () => {
    setLoading(true)
    try {
      const [s, sp] = await Promise.all([
        adminFetch<StatsResponse>('/enterprise/presets/stats'),
        adminFetch<SystemPresetsResponse>('/enterprise/presets/system'),
      ])
      setStats(s)
      setSystemPresets(sp.presets)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchAll() }, [fetchAll])

  async function handleDelete(id: string) {
    if (!confirm('Delete this system preset?')) return
    setDeleting(d => ({ ...d, [id]: true }))
    try {
      await adminFetch(`/enterprise/presets/system/${id}`, { method: 'DELETE' })
      fetchAll()
    } catch (e) {
      alert((e as Error).message)
    } finally {
      setDeleting(d => ({ ...d, [id]: false }))
    }
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    if (!form.name) return
    setCreating(true)
    setCreateMsg('')
    try {
      await adminPost('/enterprise/presets/system', {
        name: form.name.trim(),
        platform: form.platform || null,
        sort_order: parseInt(form.sort_order) || 0,
        settings: {},
      })
      setCreateMsg('✓ System preset created')
      setForm({ name: '', platform: '', sort_order: '0' })
      setShowForm(false)
      fetchAll()
    } catch (err) {
      setCreateMsg(`✗ ${(err as Error).message}`)
    } finally {
      setCreating(false)
    }
  }

  const totalPresets = stats?.total_presets ?? 0

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-mono text-lg font-bold text-slate-100">Presets</h1>
          <p className="mt-0.5 text-xs text-slate-500">user_presets · user_platform_prefs (Phase 21)</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowForm(v => !v)}
            className="rounded bg-blue-700 px-3 py-1.5 text-xs text-white hover:bg-blue-600"
          >
            + System Preset
          </button>
          <button onClick={fetchAll} className="rounded bg-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-600">
            ↺
          </button>
        </div>
      </div>

      {/* Stats cards */}
      {stats && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            { label: 'Total Presets', value: stats.total_presets },
            { label: 'System Presets', value: stats.system_count },
            { label: 'Platform Prefs', value: stats.total_prefs },
            { label: 'Defaults Set', value: stats.default_count },
          ].map(c => (
            <div key={c.label} className="rounded-lg border border-slate-800 bg-slate-900 p-4">
              <p className="font-mono text-xs text-slate-500">{c.label}</p>
              <p className="mt-1 font-mono text-2xl font-bold text-slate-100">{c.value}</p>
            </div>
          ))}
        </div>
      )}

      {error && (
        <div className="rounded border border-red-800 bg-red-900/30 px-4 py-3 text-sm text-red-300">{error}</div>
      )}

      {/* Create form */}
      {showForm && (
        <div className="rounded-lg border border-blue-800 bg-blue-950/30 p-4">
          <h2 className="mb-3 font-mono text-xs font-semibold text-blue-300">New System Preset</h2>
          <form onSubmit={handleCreate} className="flex flex-wrap gap-2 items-end">
            <input
              type="text"
              placeholder="Preset name"
              value={form.name}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
              className="rounded border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs text-slate-300 placeholder-slate-600 w-48"
            />
            <select
              value={form.platform}
              onChange={e => setForm(f => ({ ...f, platform: e.target.value }))}
              className="rounded border border-slate-700 bg-slate-800 px-2 py-1.5 text-xs text-slate-300"
            >
              <option value="">Universal</option>
              {['youtube', 'tiktok', 'instagram', 'spotify', 'twitter', 'facebook', 'soundcloud', 'threads'].map(p => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
            <input
              type="number"
              placeholder="Order"
              value={form.sort_order}
              onChange={e => setForm(f => ({ ...f, sort_order: e.target.value }))}
              className="rounded border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs text-slate-300 w-20"
            />
            <button
              type="submit"
              disabled={creating || !form.name}
              className="rounded bg-blue-700 px-3 py-1.5 text-xs text-white hover:bg-blue-600 disabled:opacity-40"
            >
              {creating ? 'Creating…' : 'Create'}
            </button>
            <button type="button" onClick={() => setShowForm(false)} className="text-xs text-slate-500 hover:text-slate-300">
              Cancel
            </button>
          </form>
          {createMsg && (
            <p className={cn('mt-2 font-mono text-xs', createMsg.startsWith('✓') ? 'text-emerald-400' : 'text-red-400')}>
              {createMsg}
            </p>
          )}
        </div>
      )}

      {loading ? (
        <div className="py-12 text-center font-mono text-xs text-slate-600">Loading presets…</div>
      ) : (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          {/* Platform distribution */}
          <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
            <h2 className="mb-4 font-mono text-xs font-semibold uppercase text-slate-500">Presets by Platform</h2>
            {Object.keys(stats?.by_platform ?? {}).length === 0 ? (
              <p className="font-mono text-xs text-slate-600">No presets yet</p>
            ) : (
              <div className="space-y-2">
                {Object.entries(stats?.by_platform ?? {}).sort((a, b) => b[1] - a[1]).map(([pl, count]) => {
                  const pct = totalPresets > 0 ? (count / totalPresets) * 100 : 0
                  return (
                    <div key={pl}>
                      <div className="flex items-center justify-between font-mono text-xs mb-0.5">
                        <span className="flex items-center gap-1.5 text-slate-300">
                          <span className="text-slate-500">{PLATFORM_ICONS[pl] ?? '○'}</span>
                          {pl}
                        </span>
                        <span className="text-slate-500">{count}</span>
                      </div>
                      <div className="h-1 w-full rounded-full bg-slate-800">
                        <div className="h-1 rounded-full bg-blue-600" style={{ width: `${pct}%` }} />
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          {/* Popular settings keys */}
          <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
            <h2 className="mb-4 font-mono text-xs font-semibold uppercase text-slate-500">Popular Settings Keys</h2>
            {(stats?.popular_settings_keys ?? []).length === 0 ? (
              <p className="font-mono text-xs text-slate-600">No preset settings yet</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {(stats?.popular_settings_keys ?? []).map(([key, count]) => (
                  <div key={key} className="flex items-center gap-1.5 rounded bg-slate-800 px-2.5 py-1.5">
                    <span className="text-xs text-slate-300">{SETTINGS_KEY_LABELS[key] ?? key}</span>
                    <span className="font-mono text-[10px] text-slate-500">×{count}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Platform prefs distribution */}
            {Object.keys(stats?.prefs_by_platform ?? {}).length > 0 && (
              <div className="mt-4 pt-4 border-t border-slate-800">
                <h3 className="mb-2 font-mono text-[10px] font-semibold uppercase text-slate-600">Platform Prefs Learned</h3>
                <div className="flex flex-wrap gap-2">
                  {Object.entries(stats?.prefs_by_platform ?? {}).map(([pl, count]) => (
                    <span key={pl} className="flex items-center gap-1 rounded bg-slate-800 px-2 py-1 font-mono text-[10px]">
                      <span className="text-slate-500">{PLATFORM_ICONS[pl] ?? '○'}</span>
                      <span className="text-slate-300">{pl}</span>
                      <span className="text-slate-600">×{count}</span>
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* System presets management */}
          <div className="lg:col-span-2 rounded-lg border border-slate-800 bg-slate-900 p-4">
            <h2 className="mb-4 font-mono text-xs font-semibold uppercase text-slate-500">
              System Presets <span className="text-slate-600 normal-case">(visible to all users, cannot be deleted by users)</span>
            </h2>
            {systemPresets.length === 0 ? (
              <p className="font-mono text-xs text-slate-600">No system presets — create one above</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="border-b border-slate-800">
                      {['Name', 'Platform', 'Settings keys', 'Order', 'Created', ''].map(h => (
                        <th key={h} className="pb-2.5 pr-4 font-mono font-semibold text-slate-500">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {systemPresets.map(p => (
                      <tr key={p.id} className="border-b border-slate-800/40 hover:bg-slate-800/20">
                        <td className="py-2.5 pr-4 font-medium text-slate-200">{p.name}</td>
                        <td className="py-2.5 pr-4">
                          <span className="flex items-center gap-1 font-mono text-slate-400">
                            <span>{PLATFORM_ICONS[p.platform ?? 'universal'] ?? '○'}</span>
                            {p.platform ?? 'universal'}
                          </span>
                        </td>
                        <td className="py-2.5 pr-4">
                          <div className="flex flex-wrap gap-1">
                            {Object.keys(p.settings ?? {}).map(k => (
                              <span key={k} className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
                                {k}
                              </span>
                            ))}
                            {Object.keys(p.settings ?? {}).length === 0 && (
                              <span className="text-slate-700">—</span>
                            )}
                          </div>
                        </td>
                        <td className="py-2.5 pr-4 font-mono text-slate-500">{p.sort_order}</td>
                        <td className="py-2.5 pr-4 font-mono text-[10px] text-slate-600">
                          {new Date(p.created_at).toLocaleDateString('en-GB')}
                        </td>
                        <td className="py-2.5">
                          <button
                            disabled={deleting[p.id]}
                            onClick={() => handleDelete(p.id)}
                            className="rounded px-2 py-1 text-[10px] text-red-400 hover:bg-red-900/30 disabled:opacity-40"
                          >
                            {deleting[p.id] ? '…' : 'Delete'}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

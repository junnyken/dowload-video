import { useState, useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { CookiePoolPanel } from '../panels/cookies/CookiePoolPanel'
import {
  useAdminCookieList,
  useAdminCookieStatus,
  useDeleteCookie,
  useAddCookie,
  useCookieStorageStatus,
  useCookieRetestInfo,
} from '../hooks/useAdminCookiePool'
import { adminKeys } from '../lib/queryKeys'
import { CookieToolbar, TestAllConfirm, describeStorage, type BatchProgress } from '../panels/cookies/CookieToolbar'
import type { CookieItem, CookieAction, AddCookieFormData, LiveTest } from '../panels/cookies/cookie.types'
import {
  fetchCookieList, retestCookie, setCookieDisabled,
  type ExpiryCookieEntry, type RetestResponse,
} from '../api/cookies'

// ─── Platform display helpers ──────────────────────────────────────────────────

const PLATFORM_ICONS: Record<string, string> = {
  youtube:    'YT',
  tiktok:     'TK',
  facebook:   'FB',
  instagram:  'IG',
  twitter:    'TW',
  x:          'X',
  reddit:     'RD',
  bilibili:   'BL',
  threads:    'TH',
  soundcloud: 'SC',
  spotify:    'SP',
}

const DEFAULT_PLATFORMS = ['youtube', 'tiktok', 'facebook', 'instagram']

function platformLabel(p: string): string {
  const map: Record<string, string> = {
    youtube: 'YouTube', tiktok: 'TikTok', facebook: 'Facebook',
    instagram: 'Instagram', twitter: 'Twitter/X', x: 'X',
    reddit: 'Reddit', bilibili: 'Bilibili', threads: 'Threads',
    soundcloud: 'SoundCloud', spotify: 'Spotify',
  }
  return map[p] ?? p.charAt(0).toUpperCase() + p.slice(1)
}

// ─── Mapping helpers ───────────────────────────────────────────────────────────

function formatRelativeTime(unixSec: number): string {
  const diffSec = Math.max(0, Math.floor(Date.now() / 1000 - unixSec))
  if (diffSec < 60) return `${diffSec}s ago`
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`
  return `${Math.floor(diffSec / 86400)}d ago`
}

function storedLiveTest(entry: ExpiryCookieEntry): LiveTest | undefined {
  const st = entry.last_test_status
  if (st !== 'ok' && st !== 'rejected' && st !== 'inconclusive') return undefined
  return { state: st, message: entry.last_test_message, at: entry.last_test_at }
}

function mapExpiryCookieToItem(
  entry: ExpiryCookieEntry, platform: string, supported: boolean,
): CookieItem {
  // health_status is the pool's own verdict (it already accounts for a live
  // test that proved an expired-by-date cookie still works).
  let status: CookieItem['status'] = 'active'
  if (entry.health_status === 'hard') {
    status = 'hard_blocked'
  } else if (entry.health_status === 'soft' || entry.health_status === 'blocked') {
    status = 'soft_blocked'
  } else if (entry.health_status === 'disabled') {
    status = 'disabled'
  } else if (entry.health_status === 'expired') {
    status = 'expired'
  } else if (entry.cooldown_ttl_s != null && entry.cooldown_ttl_s > 0) {
    status = 'soft_blocked'
  }

  let expiryEstimate = 'Unknown'
  if (status === 'expired') {
    expiryEstimate = 'Expired'
  } else if (entry.expiry_status === 'session_only') {
    expiryEstimate = 'Session only'
  } else if (entry.days_left != null) {
    const d = entry.days_left
    if (d === 0) expiryEstimate = 'Today'
    else if (d === 1) expiryEstimate = '~1 day'
    else if (d < 7) expiryEstimate = `~${d} days`
    else if (d < 60) expiryEstimate = `~${Math.round(d / 7)} weeks`
    else expiryEstimate = `~${Math.round(d / 30)} months`
  }

  let healthScore = 80
  if (entry.health_status === 'hard') healthScore = 10
  else if (entry.health_status === 'soft' || entry.health_status === 'blocked') healthScore = 40
  else if (status === 'expired' || status === 'disabled') healthScore = 0
  else if (entry.expiry_status === 'critical') healthScore = 30
  else if (entry.expiry_status === 'expiring_soon') healthScore = 60
  else if (entry.expiry_status === 'healthy') healthScore = 90

  const label = entry.label || entry.account_hint || `${platform}#${entry.index}`

  return {
    id: `${platform}-${entry.hash}-${entry.index}`,
    platform,
    accountLabel: label,
    status,
    healthScore,
    lastSuccessAt: entry.last_used > 0 ? formatRelativeTime(entry.last_used) : 'never',
    lastFailAt: null,
    failCount: 0,
    cooldownRemainingSec: entry.cooldown_ttl_s ?? 0,
    expiryEstimate,
    hash: entry.hash,
    expiresAt: entry.expires_at,
    addedAt: entry.added_at,
    testSupported: supported,
    liveTest: storedLiveTest(entry),
  }
}

function toLiveTest(r: RetestResponse): LiveTest {
  const at = Math.floor(Date.now() / 1000)
  if (r.status === 'rate_limited') {
    const wait = r.retry_after_s ? ` (thử lại sau ~${r.retry_after_s}s)` : ''
    return { state: 'rate_limited', message: `${r.message}${wait}` }
  }
  if (r.status === 'ok' && r.cleared_expired) {
    return { state: 'ok', at, message: 'Nền tảng vẫn chấp nhận — đã gỡ dấu "hết hạn"' }
  }
  return { state: r.status === 'not_found' ? 'error' : r.status, message: r.message, at }
}

const sleep = (ms: number) => new Promise(res => setTimeout(res, ms))
const BATCH_DELAY_MS = 2000

// ─── Page component ────────────────────────────────────────────────────────────

export function CookiesPage() {
  const [activePlatform, setActivePlatform] = useState('youtube')
  const [localOverrides, setLocalOverrides] = useState<Record<string, Partial<CookieItem>>>({})

  // Fetch all platform statuses to build dynamic tabs
  const { data: statusData } = useAdminCookieStatus()

  // Build tab list: platforms that have cookies + always show DEFAULT_PLATFORMS
  const availablePlatforms: string[] = (() => {
    const fromStatus = statusData?.pools ? Object.keys(statusData.pools).filter(p => (statusData.pools[p]?.total ?? 0) > 0) : []
    const merged = Array.from(new Set([...DEFAULT_PLATFORMS, ...fromStatus]))
    return merged
  })()

  // If the saved activePlatform has cookies but wasn't in DEFAULT_PLATFORMS, it'll appear after load
  useEffect(() => {
    if (statusData?.pools && !availablePlatforms.includes(activePlatform)) {
      setActivePlatform('youtube')
    }
  }, [statusData])

  const qc = useQueryClient()
  const { data, isLoading, error, refetch, dataUpdatedAt, isFetching } = useAdminCookieList(activePlatform)
  const deleteMut = useDeleteCookie(activePlatform)
  const addMut    = useAddCookie()
  const storageQ  = useCookieStorageStatus()
  const retestInfo = useCookieRetestInfo()

  const supportedPlatforms = retestInfo.data?.supported_platforms
  const batchCap = retestInfo.data?.limits.batch_cap ?? 20
  const isSupported = (p: string) => (supportedPlatforms ? supportedPlatforms.includes(p) : true)

  // Live-test results for this session, keyed `${platform}:${hash}`.
  const [liveTests, setLiveTests] = useState<Record<string, LiveTest>>({})
  const [batch, setBatch] = useState<BatchProgress | null>(null)
  const [confirm, setConfirm] = useState<{ queue: { platform: string; hash: string; label: string }[]; skipped: number } | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const stopRef = useRef(false)

  const rawCookies: CookieItem[] = (data?.cookies ?? []).map(e =>
    mapExpiryCookieToItem(e, activePlatform, isSupported(activePlatform)),
  )

  const cookies: CookieItem[] = rawCookies.map(c => ({
    ...c,
    ...(localOverrides[c.id] ?? {}),
    liveTest: (c.hash && liveTests[`${c.platform}:${c.hash}`]) || c.liveTest,
  }))

  function setLive(platform: string, hash: string, t: LiveTest) {
    setLiveTests(prev => ({ ...prev, [`${platform}:${hash}`]: t }))
  }

  async function runRetest(platform: string, hash: string): Promise<LiveTest> {
    setLive(platform, hash, { state: 'running' })
    let t: LiveTest
    try {
      t = toLiveTest(await retestCookie(platform, hash))
    } catch (e) {
      t = { state: 'error', message: e instanceof Error ? e.message : 'Không gọi được máy chủ' }
    }
    setLive(platform, hash, t)
    qc.invalidateQueries({ queryKey: adminKeys.cookieList(platform) })
    qc.invalidateQueries({ queryKey: ['admin', 'cookies', 'status'] })
    return t
  }

  async function reloadAll() {
    await Promise.all([
      refetch(),
      storageQ.refetch(),
      qc.invalidateQueries({ queryKey: ['admin', 'cookies', 'status'] }),
    ])
  }

  // Build the queue across every platform that holds cookies, interleaved so
  // consecutive probes hit different platforms (the server also spaces them).
  async function prepareTestAll() {
    setNotice(null)
    const platforms = Object.entries(statusData?.pools ?? {})
      .filter(([, v]) => (v?.total ?? 0) > 0).map(([p]) => p).filter(isSupported)
    const lists = await Promise.all(platforms.map(async p => {
      try {
        const r = await qc.fetchQuery({
          queryKey: adminKeys.cookieList(p), queryFn: () => fetchCookieList(p), staleTime: 0,
        })
        return r.cookies
          .filter(c => c.health_status !== 'disabled')
          .map(c => ({ platform: p, hash: c.hash, label: c.label || c.account_hint || `${p}#${c.index}` }))
      } catch { return [] }
    }))
    const queue: { platform: string; hash: string; label: string }[] = []
    for (let i = 0; lists.some(l => i < l.length); i++) {
      for (const l of lists) if (i < l.length) queue.push(l[i])
    }
    if (queue.length === 0) {
      setNotice('Không có cookie nào để kiểm tra (cookie đang tắt hoặc nền tảng chưa hỗ trợ được bỏ qua).')
      return
    }
    setConfirm({ queue: queue.slice(0, batchCap), skipped: Math.max(0, queue.length - batchCap) })
  }

  async function runTestAll(queue: { platform: string; hash: string; label: string }[]) {
    setConfirm(null)
    stopRef.current = false
    queue.forEach(q => setLive(q.platform, q.hash, { state: 'queued' }))
    const tally = { ok: 0, rejected: 0, inconclusive: 0, other: 0 }
    let done = 0
    setBatch({ done, total: queue.length, running: true })
    for (let i = 0; i < queue.length; i++) {
      if (stopRef.current) {
        queue.slice(i).forEach(q => setLive(q.platform, q.hash, { state: 'skipped_disabled', message: 'Đã dừng trước khi tới lượt' }))
        break
      }
      const q = queue[i]
      setBatch({ done, total: queue.length, running: true, current: `${platformLabel(q.platform)} · ${q.label}` })
      let t = await runRetest(q.platform, q.hash)
      if (t.state === 'rate_limited') {            // wait the server's gap once, then retry
        await sleep(3500)
        t = await runRetest(q.platform, q.hash)
      }
      if (t.state === 'ok') tally.ok++
      else if (t.state === 'rejected') tally.rejected++
      else if (t.state === 'inconclusive') tally.inconclusive++
      else tally.other++
      done++
      setBatch({ done, total: queue.length, running: true })
      if (i < queue.length - 1) await sleep(BATCH_DELAY_MS)
    }
    const stopped = stopRef.current
    setBatch({
      done, total: queue.length, running: false,
      summary: `${stopped ? 'Đã dừng' : 'Xong'}: ${tally.ok} còn dùng được · ${tally.rejected} bị từ chối · ${tally.inconclusive} chưa kết luận` +
        (tally.other ? ` · ${tally.other} lỗi/bị bỏ qua` : ''),
    })
  }

  async function handleAction(id: string, action: CookieAction) {
    const item = cookies.find(c => c.id === id)
    if (!item) return

    const parts = id.split('-')
    const index = parseInt(parts[parts.length - 1], 10)

    switch (action) {
      case 'delete':
        deleteMut.mutate(index)
        break
      case 'test':
        if (item.hash) void runRetest(item.platform, item.hash)
        break
      case 'disable':
      case 'enable':
        if (item.hash) {
          try {
            await setCookieDisabled(item.platform, item.hash, action === 'disable')
            setNotice(action === 'disable' ? 'Đã tắt cookie.' : 'Đã bật lại cookie.')
          } catch (e) {
            setNotice(`Không lưu được thay đổi: ${e instanceof Error ? e.message : 'lỗi máy chủ'}`)
          }
          await qc.invalidateQueries({ queryKey: adminKeys.cookieList(item.platform) })
          await qc.invalidateQueries({ queryKey: ['admin', 'cookies', 'status'] })
        }
        break
      case 'reset_soft':
      case 'reset_hard':
        setLocalOverrides(prev => ({
          ...prev,
          [id]: { status: 'active' as const, cooldownRemainingSec: 0, failCount: 0 },
        }))
        break
      default:
        break
    }
  }

  async function handleAdd(formData: AddCookieFormData) {
    const target = formData.platform || activePlatform
    addMut.mutate(
      { platform: target, rawCookie: formData.rawCookie, label: formData.accountLabel },
      {
        onSuccess: () => {
          setActivePlatform(target)
        },
      },
    )
  }

  const errorMsg = error instanceof Error ? error.message : error ? 'Failed to fetch cookies' : undefined

  const summary = {
    active:       cookies.filter(c => c.status === 'active').length,
    cooldown:     cookies.filter(c => c.status === 'soft_blocked' || c.status === 'hard_blocked').length,
    disabled:     cookies.filter(c => c.status === 'disabled' || c.status === 'expired').length,
    expiringSoon: cookies.filter(c => c.expiryEstimate.includes('days') || c.expiryEstimate.includes('week')).length,
  }

  // Pool counts per tab from status data
  const poolCounts = statusData?.pools ?? {}

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-fg">Cookie Pool</h1>
        <p className="mt-0.5 text-xs text-fg-muted">
          {summary.active} active · {summary.cooldown} in cooldown · {summary.disabled} disabled
          {summary.expiringSoon > 0 && (
            <span className="ml-2 font-medium text-warning">
              {summary.expiringSoon} expiring soon
            </span>
          )}
        </p>
      </div>

      {/* Dynamic platform tabs */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs text-fg-muted">Platform:</span>
        <div className="flex flex-wrap gap-1.5">
          {availablePlatforms.map(p => {
            const count = poolCounts[p]?.total ?? 0
            const isActive = activePlatform === p
            return (
              <button
                key={p}
                onClick={() => { setActivePlatform(p); setLocalOverrides({}) }}
                className={[
                  'inline-flex items-center gap-1 rounded-full border px-3 py-0.5 text-[11px] font-medium transition-colors',
                  isActive
                    ? 'border-line bg-surface-2 text-fg-2'
                    : 'border-line text-fg-muted hover:border-line-strong hover:text-fg-2',
                ].join(' ')}
              >
                <span className="font-mono text-[10px] opacity-60">{PLATFORM_ICONS[p] ?? p.slice(0,2).toUpperCase()}</span>
                {platformLabel(p)}
                {count > 0 && (
                  <span className={[
                    'ml-0.5 rounded-full px-1 py-0 font-mono text-[10px]',
                    isActive ? 'bg-surface-2 text-fg-2' : 'bg-surface text-fg-muted',
                  ].join(' ')}>
                    {count}
                  </span>
                )}
              </button>
            )
          })}
        </div>
      </div>

      <CookieToolbar
        updatedAtMs={dataUpdatedAt}
        refreshing={isFetching}
        onReload={reloadAll}
        onTestAll={prepareTestAll}
        onStop={() => { stopRef.current = true }}
        batch={batch}
        storage={describeStorage(storageQ.data, storageQ.isError)}
      />
      {notice && (
        <p className="rounded-control border border-line bg-surface-2 px-3 py-2 text-xs text-fg-2" role="status">
          {notice}
        </p>
      )}
      {confirm && (
        <TestAllConfirm
          count={confirm.queue.length}
          skippedByCap={confirm.skipped}
          cap={batchCap}
          onConfirm={() => runTestAll(confirm.queue)}
          onCancel={() => setConfirm(null)}
        />
      )}

      <CookiePoolPanel
        cookies={cookies}
        loading={isLoading}
        error={errorMsg}
        onRetry={() => refetch()}
        onAction={handleAction}
        onAdd={handleAdd}
      />
    </div>
  )
}

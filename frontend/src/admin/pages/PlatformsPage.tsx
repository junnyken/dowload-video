import { useState } from 'react'
import { PlatformHealthTable } from '../panels/platforms/PlatformHealthTable'
import { PlatformDetailDrawer } from '../panels/platforms/PlatformDetailDrawer'
import {
  useAdminPlatformHealth,
  useAdminPlatformDetail,
  useAdminCircuitAction,
} from '../hooks/useAdminPlatformHealth'
import type { PlatformHealthRow } from '../panels/platforms/platform.types'

export function PlatformsPage() {
  const [selected, setSelected] = useState<PlatformHealthRow | null>(null)

  const {
    data,
    isLoading,
    isError,
    error,
    refetch,
    dataUpdatedAt,
  } = useAdminPlatformHealth(30_000)

  const { data: detail, isLoading: detailLoading } = useAdminPlatformDetail(
    selected?.platform ?? null,
  )

  const circuitAction = useAdminCircuitAction()

  async function handleAction(platform: string, action: string) {
    const apiAction = ['force_open', 'force_close', 'reset'].includes(action) ? action : null
    if (!apiAction) return
    circuitAction.mutate({ platform, action: apiAction })
  }

  const rows: PlatformHealthRow[] = (data?.platforms ?? []).map(p => ({
    platform:       p.platform,
    status:         p.status,
    circuitState:   p.circuitState,
    lastSuccessAt:  p.lastSuccessAt,
    failRate1h:     p.failRate1h,
    cookieRequired: p.cookieRequired,
    proxyRequired:  p.proxyRequired,
    totalJobs1h:    p.totalJobs1h,
    activeJobs:     p.activeJobs,
  }))

  if (isLoading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="text-sm text-slate-500 animate-pulse">Loading platform health…</div>
      </div>
    )
  }

  if (isError) {
    const msg = error instanceof Error ? error.message : 'Failed to load platform health'
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3">
        <p className="text-sm text-red-400">{msg}</p>
        <button
          onClick={() => refetch()}
          className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
        >
          Retry
        </button>
      </div>
    )
  }

  const updatedStr = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleTimeString()
    : null

  return (
    <>
      {updatedStr && (
        <div className="mb-2 flex items-center justify-between px-1">
          <span className="text-[10px] text-slate-600">
            Updated {updatedStr} · auto-refresh 30s
          </span>
          <button onClick={() => refetch()} className="text-[10px] text-slate-500 hover:text-slate-300">
            ↺ Refresh
          </button>
        </div>
      )}

      <PlatformHealthTable
        rows={rows}
        onRowClick={setSelected}
        onAction={handleAction}
        selectedPlatform={selected?.platform ?? null}
      />

      <PlatformDetailDrawer
        detail={detailLoading ? null : (detail ?? null)}
        onClose={() => setSelected(null)}
        onAction={handleAction}
      />
    </>
  )
}

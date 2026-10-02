import { useState } from 'react'
import { PageHeader } from '../shared/PageHeader'
import { Button } from '../shared/Button'
import { Card } from '../shared/Card'
import { ErrorState } from '../shared/ErrorState'
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
        <div className="text-sm text-fg-muted animate-pulse">Loading platform health…</div>
      </div>
    )
  }

  if (isError) {
    const msg = error instanceof Error ? error.message : 'Failed to load platform health'
    return (
      <Card className="mx-auto mt-10 max-w-xl" padding="md">
        <ErrorState message={msg} onRetry={() => refetch()} />
      </Card>
    )
  }

  const healthyCount = rows.filter(r => r.status === 'healthy').length

  const updatedStr = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleTimeString()
    : null

  return (
    <>
      <PageHeader
        className="mb-4"
        title="Platform Health"
        description={`${healthyCount} / ${rows.length} operational · circuit breakers + cookie pool status`}
        actions={
          <>
            {updatedStr && (
              <span className="font-mono text-[11px] text-fg-muted">
                Updated {updatedStr} · auto-refresh 30s
              </span>
            )}
            <Button onClick={() => refetch()}>↺ Refresh</Button>
          </>
        }
      />

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

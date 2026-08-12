import { HealthStatCard, type HealthStatCardProps } from './HealthStatCard'
import { CardSkeleton } from '../shared/LoadingSkeleton'

export type HealthPanelEntry = HealthStatCardProps & { id: string }

interface HealthPanelGridProps {
  panels: HealthPanelEntry[]
  loading?: boolean
}

export function HealthPanelGrid({ panels, loading = false }: HealthPanelGridProps) {
  if (loading) {
    return (
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[1, 2, 3, 4].map(i => <CardSkeleton key={i} />)}
      </div>
    )
  }

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {panels.map(({ id, ...props }) => (
        <HealthStatCard key={id} {...props} />
      ))}
    </div>
  )
}

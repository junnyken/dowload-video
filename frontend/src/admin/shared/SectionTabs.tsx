import type { ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Button } from './Button'

export interface SectionTab {
  id: string
  label: string
  content: ReactNode
}

/**
 * Tab strip for pages that fold a second view in (selected via ?tab=<id>, so
 * redirects from old URLs and browser back/forward keep working). Only the
 * active tab is mounted, so an inactive tab does not poll its endpoints.
 */
export function SectionTabs({ tabs }: { tabs: SectionTab[] }) {
  const [params, setParams] = useSearchParams()
  const active = tabs.find(t => t.id === params.get('tab')) ?? tabs[0]

  return (
    <div className="flex flex-col gap-4">
      <div className="flex gap-1" role="tablist">
        {tabs.map(t => (
          <Button
            key={t.id}
            size="sm"
            variant={active.id === t.id ? 'primary' : 'ghost'}
            role="tab"
            aria-selected={active.id === t.id}
            onClick={() => setParams(t.id === tabs[0].id ? {} : { tab: t.id }, { replace: true })}
          >
            {t.label}
          </Button>
        ))}
      </div>
      <div role="tabpanel">{active.content}</div>
    </div>
  )
}

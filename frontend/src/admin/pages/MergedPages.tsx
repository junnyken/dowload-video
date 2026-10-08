import { SectionTabs } from '../shared/SectionTabs'
import { PlatformsPage } from './PlatformsPage'
import ProbesPage from './ProbesPage'
import { QueuePage } from './QueuePage'
import { JobsPage } from './JobsPage'
import AutomationHistoryPage from './AutomationHistoryPage'
import PlaybooksPage from './PlaybooksPage'

// Merged sidebar entries (task #6137). The old routes (/probes, /jobs,
// /playbooks) redirect here with the matching ?tab= so old links still open.

export function PlatformsHubPage() {
  return (
    <SectionTabs tabs={[
      { id: 'health', label: 'Circuit breaker', content: <PlatformsPage /> },
      // wording: BA review
      { id: 'probes', label: 'Sức khoẻ nền tảng', content: <ProbesPage /> },
    ]} />
  )
}

export function QueueHubPage() {
  return (
    <SectionTabs tabs={[
      { id: 'queue', label: 'Queue', content: <QueuePage /> },
      // wording: BA review
      { id: 'jobs', label: 'Jobs đang chạy', content: <JobsPage /> },
    ]} />
  )
}

export function AutomationHubPage() {
  return (
    <SectionTabs tabs={[
      { id: 'history', label: 'Automation', content: <AutomationHistoryPage /> },
      { id: 'playbooks', label: 'Playbooks', content: <PlaybooksPage /> },
    ]} />
  )
}

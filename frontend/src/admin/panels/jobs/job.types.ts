export const PHASE_ORDER = [
  'queued',
  'resolving',
  'metadata',
  'auth',
  'download',
  'post-process',
  'done',
] as const

export type PhaseName   = (typeof PHASE_ORDER)[number]
export type PhaseStatus = 'pending' | 'running' | 'success' | 'failed' | 'skipped'
export type JobStatus   = 'queued' | 'running' | 'done' | 'failed' | 'cancelled' | 'stuck'

export interface JobTracePhase {
  phase:        PhaseName
  status:       PhaseStatus
  startedAt:    string | null
  endedAt:      string | null
  durationMs:   number | null
  proxyUsed:    string | null
  cookieUsed:   string | null
  errorMessage: string | null
}

export interface JobSummary {
  totalPhases:     number
  completedPhases: number
  failedPhases:    number
  totalDurationMs: number | null
  lastError:       string | null
}

export interface JobInspectorData {
  jobId:         string
  batchId:       string | null
  platform:      string
  url:           string
  userIp:        string
  currentStatus: JobStatus
  createdAt:     string
  updatedAt:     string
  traces:        JobTracePhase[]
  summary:       JobSummary
  recentErrors:  string[]
}

import { adminFetch } from '../utils/adminFetch'

export interface ActiveJob {
  id: string
  batch_id: string | null
  original_url: string
  status: 'processing' | 'pending'
  created_at: string
  platform?: string
}

export interface ActiveJobsResponse {
  success: boolean
  processing: ActiveJob[]
  pending: ActiveJob[]
  processing_count: number
  pending_count: number
}

export function fetchActiveJobs(): Promise<ActiveJobsResponse> {
  return adminFetch<ActiveJobsResponse>('/active-jobs')
}

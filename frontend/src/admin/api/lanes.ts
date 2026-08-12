import { adminFetch } from '../utils/adminFetch'
import type { LaneSnapshotResponse, LaneObservation } from '../types/lane.types'

export function fetchLaneSnapshot(): Promise<LaneSnapshotResponse> {
  return adminFetch<LaneSnapshotResponse>('/platforms/lane-snapshot')
}

export function fetchLaneDetail(platform: string): Promise<{ success: boolean } & LaneObservation> {
  return adminFetch(`/platforms/${platform}/lane-detail`)
}

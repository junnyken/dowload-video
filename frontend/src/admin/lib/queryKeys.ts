export const adminKeys = {
  all: ['admin'] as const,

  system: () => [...adminKeys.all, 'system'] as const,
  systemStatus: () => [...adminKeys.system(), 'status'] as const,

  platforms: () => [...adminKeys.all, 'platforms'] as const,
  platformHealth: () => [...adminKeys.platforms(), 'health'] as const,
  platformDetail: (platform: string) => [...adminKeys.platforms(), 'detail', platform] as const,

  cookies: () => [...adminKeys.all, 'cookies'] as const,
  cookieList: (platform: string) => [...adminKeys.cookies(), 'list', platform] as const,
  cookieStatus: () => [...adminKeys.cookies(), 'status'] as const,

  jobs: () => [...adminKeys.all, 'jobs'] as const,
  activeJobs: () => [...adminKeys.jobs(), 'active'] as const,

  failures: () => [...adminKeys.all, 'failures'] as const,

  // Phase 27A — lane observability
  lanes: () => [...adminKeys.platforms(), 'lanes'] as const,
  laneSnapshot: () => [...adminKeys.lanes(), 'snapshot'] as const,
  laneDetail: (platform: string) => [...adminKeys.lanes(), 'detail', platform] as const,
} as const

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { adminKeys } from '../lib/queryKeys'
import {
  fetchCookieList,
  fetchCookieStatus,
  deleteCookie,
  addCookie,
  triggerCookieHealthCheck,
  type CookieListResponse,
  type CookieStatusResponse,
} from '../api/cookies'

export function useAdminCookieStatus() {
  return useQuery<CookieStatusResponse>({
    queryKey:        ['admin', 'cookies', 'status'],
    queryFn:         fetchCookieStatus,
    refetchInterval: 30_000,
  })
}

export function useAdminCookieList(platform: string) {
  return useQuery<CookieListResponse>({
    queryKey:        adminKeys.cookieList(platform),
    queryFn:         () => fetchCookieList(platform),
    refetchInterval: 60_000,
  })
}

export function useDeleteCookie(platform: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (index: number) => deleteCookie(platform, index),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.cookieList(platform) }),
  })
}

export function useAddCookie() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      platform,
      rawCookie,
      label,
    }: {
      platform: string
      rawCookie: string
      label?: string
    }) => addCookie(platform, rawCookie, label),
    onSuccess: (_data, { platform }) =>
      qc.invalidateQueries({ queryKey: adminKeys.cookieList(platform) }),
  })
}

export function useTriggerCookieHealthCheck(platform: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: triggerCookieHealthCheck,
    onSuccess:  () => qc.invalidateQueries({ queryKey: adminKeys.cookieList(platform) }),
  })
}

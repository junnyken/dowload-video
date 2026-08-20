import { getAdminToken } from '../hooks/useAdminAuth'
import { API_BASE } from '../../lib/apiBase'

/**
 * Authenticated fetch wrapper for admin API endpoints.
 * Automatically injects Authorization: Bearer <token> header.
 * Throws on non-2xx responses with the error detail from the API body.
 */
export async function adminFetch<T = unknown>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const token = getAdminToken()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> | undefined),
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const res = await fetch(`${API_BASE}/api/v1/admin${path}`, { ...options, headers })
  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    try {
      const body = await res.json()
      msg = body?.detail ?? msg
    } catch { /* ignore */ }
    throw new Error(msg)
  }
  return res.json() as Promise<T>
}

export async function adminPost<T = unknown>(path: string, body?: unknown): Promise<T> {
  return adminFetch<T>(path, {
    method: 'POST',
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
}

/**
 * Same admin session, different router prefix.
 *
 * The intelligence endpoints live under /api/v1/intelligence rather than
 * /api/v1/admin but are gated by the same verify_admin check, so they need the
 * Bearer session token and NOT the /admin path prefix that adminFetch adds.
 */
export async function intelFetch<T = unknown>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const token = getAdminToken()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> | undefined),
  }
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(`${API_BASE}/api/v1/intelligence${path}`, { ...options, headers })
  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    try {
      const body = await res.json()
      msg = body?.detail ?? msg
    } catch { /* ignore */ }
    throw new Error(msg)
  }
  return res.json() as Promise<T>
}

export async function intelPost<T = unknown>(path: string, body?: unknown): Promise<T> {
  return intelFetch<T>(path, {
    method: 'POST',
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
}

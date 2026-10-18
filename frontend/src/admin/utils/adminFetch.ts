import { getAdminToken } from '../hooks/useAdminAuth'
import { API_BASE } from '../../lib/apiBase'

/** FastAPI `detail` is a string, or a pydantic list of {msg}: flatten to one message. */
export function formatDetail(detail: unknown, fallback: string): string {
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail)) {
    const msgs = detail
      .map(d => (d && typeof d === 'object' && 'msg' in d ? String((d as { msg: unknown }).msg) : typeof d === 'string' ? d : ''))
      .filter(Boolean)
    if (msgs.length) return msgs.join('; ')
  }
  if (detail && typeof detail === 'object' && 'message' in detail) return String((detail as { message: unknown }).message)
  return fallback
}

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
      msg = formatDetail(body?.detail, msg)
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

export async function adminPatch<T = unknown>(path: string, body?: unknown): Promise<T> {
  return adminFetch<T>(path, {
    method: 'PATCH',
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
}

export async function adminDelete<T = unknown>(path: string): Promise<T> {
  return adminFetch<T>(path, { method: 'DELETE' })
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

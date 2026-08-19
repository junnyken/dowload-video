import { useEffect, useState, useCallback } from 'react'
import type { AdminUser, AdminRole } from '../types/admin.types'
import { API_BASE } from '../../lib/apiBase'

const STORAGE_KEY = 'vg_admin_session'

// ── Module-level singleton ─────────────────────────────────────────────────────
// Without this each useAdminAuth() call gets isolated React state.
// logout() in AdminSidebar would NOT update AdminShell's isAuthenticated check.

type Subscriber = () => void
const _subs = new Set<Subscriber>()

let _user: AdminUser | null = (() => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const u = JSON.parse(raw) as AdminUser
    if (new Date(u.expiresAt) < new Date()) {
      localStorage.removeItem(STORAGE_KEY)
      return null
    }
    return u
  } catch {
    return null
  }
})()

function _notify() {
  _subs.forEach(fn => fn())
}

// ── Exported helper — used by adminFetch ───────────────────────────────────────
export function getAdminToken(): string | null {
  return _user?.sessionToken ?? null
}

export function useAdminAuth() {
  const [, rerender] = useState(0)

  useEffect(() => {
    const tick = () => rerender(n => n + 1)
    _subs.add(tick)
    return () => { _subs.delete(tick) }
  }, [])

  /**
   * Call the real backend POST /api/admin/login.
   * Returns null on success, error string on failure.
   */
  const login = useCallback(async (email: string, password: string): Promise<string | null> => {
    if (!email || !password) return 'Email and password are required.'
    try {
      const res = await fetch(`${API_BASE}/api/v1/admin/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      })
      if (res.status === 429) {
        const body = await res.json().catch(() => ({}))
        const wait = body?.detail?.retry_after_seconds ?? 900
        return `Too many attempts. Try again in ${Math.ceil(wait / 60)}m.`
      }
      if (res.status === 401) {
        const body = await res.json().catch(() => ({}))
        const left = body?.detail?.attempts_left ?? 0
        return `Wrong password.${left > 0 ? ` ${left} attempt(s) left.` : ''}`
      }
      if (!res.ok) return `Login failed (${res.status}). Check ADMIN_PASSWORD env.`

      const data = await res.json()
      const token: string = data.session_token
      const expiresAt = new Date(Date.now() + (data.expires_in ?? 28800) * 1000).toISOString()

      const user: AdminUser = {
        email,
        role: 'admin' as AdminRole,
        expiresAt,
        sessionToken: token,
      }
      localStorage.setItem(STORAGE_KEY, JSON.stringify(user))
      _user = user
      _notify()
      return null // success
    } catch (e) {
      return `Network error: ${e instanceof Error ? e.message : 'unknown'}`
    }
  }, [])

  const logout = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY)
    _user = null
    _notify()
  }, [])

  const hasRole = useCallback((min: AdminRole): boolean => {
    if (!_user) return false
    const levels: AdminRole[] = ['viewer', 'operator', 'admin', 'superadmin']
    return levels.indexOf(_user.role) >= levels.indexOf(min)
  }, [])

  return { user: _user, login, logout, hasRole, isAuthenticated: !!_user }
}

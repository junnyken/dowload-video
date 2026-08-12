import { useState, type FormEvent } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { useAdminAuth } from '../hooks/useAdminAuth'

export function AdminLoginPage() {
  const navigate = useNavigate()
  const { login, isAuthenticated } = useAdminAuth()

  if (isAuthenticated) {
    return <Navigate to="/vid-admin" replace />
  }
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError('')
    if (!email || !password) {
      setError('Email and password are required.')
      return
    }
    setLoading(true)
    const err = await login(email, password)
    setLoading(false)
    if (!err) {
      navigate('/vid-admin', { replace: true })
    } else {
      setError(err ?? 'Login failed.')
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-950 px-4">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="mb-8 text-center">
          <div className="mb-3 flex justify-center">
            <div className="flex h-10 w-10 items-center justify-center rounded-2xl border border-slate-700 bg-slate-900 text-xl">
              ▼
            </div>
          </div>
          <h1 className="font-mono text-sm font-bold tracking-tight text-slate-100">
            VidGrab <span className="text-slate-500">Admin</span>
          </h1>
          <p className="mt-1 text-xs text-slate-600">Control Plane · Operator Access</p>
        </div>

        {/* Form card */}
        <div className="rounded-2xl border border-slate-800 bg-slate-900 p-6">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="mb-1.5 block font-mono text-[10px] font-medium uppercase tracking-wider text-slate-500">
                Email
              </label>
              <input
                type="email"
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="admin@matbao.com"
                className="w-full rounded-xl border border-slate-700 bg-slate-800 px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 outline-none transition-colors focus:border-slate-500"
              />
            </div>

            <div>
              <label className="mb-1.5 block font-mono text-[10px] font-medium uppercase tracking-wider text-slate-500">
                Password
              </label>
              <input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                className="w-full rounded-xl border border-slate-700 bg-slate-800 px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 outline-none transition-colors focus:border-slate-500"
              />
            </div>

            {error && (
              <div className="rounded-xl border border-red-900 bg-red-950/50 px-3 py-2.5">
                <p className="text-xs text-red-400">{error}</p>
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full rounded-xl border border-slate-600 bg-slate-700 py-2.5 text-sm font-medium text-slate-100 transition-colors hover:border-slate-500 hover:bg-slate-600 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loading ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
        </div>

        <p className="mt-4 text-center text-[11px] text-slate-700">
          Session expires after 8 hours · IP-restricted in production
        </p>
      </div>
    </div>
  )
}

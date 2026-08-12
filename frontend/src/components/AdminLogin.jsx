import { useState } from 'react';
import { Shield, Key, ArrowRight, Lock, Loader2 } from 'lucide-react';

const API_BASE = import.meta.env.VITE_API_URL || '';

export default function AdminLogin({ onLogin }) {
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [lockedOut, setLockedOut] = useState(false);
  const [lockoutSeconds, setLockoutSeconds] = useState(0);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (lockedOut || loading) return;
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`${API_BASE}/api/v1/admin/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      const data = await res.json();
      if (res.status === 429 && data.detail?.error === 'locked_out') {
        setLockedOut(true);
        setLockoutSeconds(data.detail.retry_after_seconds || 900);
        setError(`Quá nhiều lần sai — thử lại sau ${Math.ceil((data.detail.retry_after_seconds || 900) / 60)} phút.`);
        return;
      }
      if (res.status === 401) {
        const left = data.detail?.attempts_left;
        setError(left != null ? `Sai mật khẩu — còn ${left} lần thử.` : 'Mật khẩu không chính xác!');
        setTimeout(() => setError(''), 3000);
        return;
      }
      if (!res.ok) {
        setError('Đăng nhập thất bại. Thử lại sau.');
        return;
      }
      // Token stored in React state (memory) — passed up to parent
      onLogin(data.session_token);
    } catch {
      setError('Không kết nối được máy chủ.');
      setTimeout(() => setError(''), 3000);
    } finally {
      setLoading(false);
      setPassword('');
    }
  };

  return (
    <div className="min-h-[80vh] flex items-center justify-center">
      <div className="bg-surface-card border border-border p-8 rounded-2xl shadow-xl w-full max-w-md animate-in fade-in zoom-in duration-300">
        <div className="flex flex-col items-center mb-8">
          <div className="w-16 h-16 bg-error/10 text-error flex items-center justify-center rounded-2xl mb-4">
            <Shield className="w-8 h-8" />
          </div>
          <h2 className="text-2xl font-bold text-text-primary text-center">Admin Control Panel</h2>
          <p className="text-text-muted text-sm mt-2 text-center">Restricted Access Area</p>
        </div>

        {lockedOut ? (
          <div className="flex flex-col items-center gap-3 py-4">
            <Lock className="w-10 h-10 text-error" />
            <p className="text-error text-sm font-semibold text-center">{error}</p>
            <p className="text-text-muted text-xs text-center">Tài khoản tạm khóa do nhập sai quá nhiều lần.</p>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1">Access Key</label>
              <div className="relative">
                <Key className="w-5 h-5 text-text-muted absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className={`w-full bg-surface border ${error ? 'border-error' : 'border-border'} rounded-xl py-3 pl-10 pr-4 text-text-primary focus:outline-none focus:border-primary transition-colors`}
                  placeholder="Nhập mật khẩu quản trị..."
                  autoFocus
                  disabled={loading}
                />
              </div>
              {error && <p className="text-error text-xs mt-1 animate-pulse">{error}</p>}
            </div>

            <button
              type="submit"
              disabled={loading || !password}
              className="w-full bg-primary hover:bg-primary-hover text-surface font-bold py-3 px-4 rounded-xl flex items-center justify-center gap-2 transition-all disabled:opacity-60"
            >
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <ArrowRight className="w-4 h-4" />}
              {loading ? 'Đang đăng nhập...' : 'Đăng nhập'}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

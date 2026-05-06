import { useState } from 'react';
import { Shield, Key, ArrowRight } from 'lucide-react';

export default function AdminLogin({ onLogin }) {
  const [password, setPassword] = useState('');
  const [error, setError] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    // Verify password against backend — fails fast if wrong
    try {
      const res = await fetch(`${import.meta.env.VITE_API_URL || ''}/api/v1/admin/stats`, {
        headers: { 'X-Admin-Token': password },
      });
      if (res.status === 401) {
        setError(true);
        setTimeout(() => setError(false), 2000);
        return;
      }
      sessionStorage.setItem('admin_token', password);
      onLogin();
    } catch {
      setError(true);
      setTimeout(() => setError(false), 2000);
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
              />
            </div>
            {error && <p className="text-error text-xs mt-1 animate-pulse">Mật khẩu không chính xác!</p>}
          </div>

          <button
            type="submit"
            className="w-full bg-primary hover:bg-primary-hover text-surface font-bold py-3 px-4 rounded-xl flex items-center justify-center gap-2 transition-all"
          >
            Đăng nhập <ArrowRight className="w-4 h-4" />
          </button>
        </form>
      </div>
    </div>
  );
}

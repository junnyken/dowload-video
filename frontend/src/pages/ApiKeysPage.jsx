import { useState, useEffect, useCallback } from 'react';
import { Key, Plus, Trash2, Pencil, Copy, Check, X, Eye, EyeOff } from 'lucide-react';
import { useAuth } from '../context/AuthContext';

const MAX_KEYS = 3;

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <button
      onClick={copy}
      className="p-1.5 rounded text-zinc-400 hover:text-white hover:bg-zinc-700 transition-colors"
      title="Copy"
    >
      {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
    </button>
  );
}

function CreateModal({ onClose, onCreated, apiBase, token }) {
  const [label, setLabel]       = useState('My API Key');
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState('');
  const [newKey, setNewKey]     = useState(null);
  const [revealed, setRevealed] = useState(false);

  const submit = async () => {
    setError('');
    setLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/v1/api-keys`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ label: label.trim() || 'My API Key' }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data?.detail?.user_message || data?.detail || `HTTP ${res.status}`);
        return;
      }
      setNewKey(data);
      onCreated();
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
      <div className="w-full max-w-md bg-zinc-900 border border-zinc-700 rounded-2xl shadow-2xl p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-white font-semibold text-sm">Tạo API Key mới</h3>
          <button onClick={onClose} className="p-1 rounded text-zinc-400 hover:text-white hover:bg-zinc-800">
            <X className="w-4 h-4" />
          </button>
        </div>

        {!newKey ? (
          <>
            <label className="block text-xs text-zinc-400 mb-1">Nhãn</label>
            <input
              type="text"
              value={label}
              onChange={e => setLabel(e.target.value)}
              maxLength={64}
              className="w-full px-3 py-2 rounded-lg bg-zinc-800 border border-zinc-700 text-white text-sm focus:outline-none focus:border-violet-500 mb-4"
              placeholder="My API Key"
              autoFocus
            />

            {error && <p className="text-xs text-red-400 mb-3">{error}</p>}

            <div className="flex gap-2">
              <button
                onClick={submit}
                disabled={loading}
                className="flex-1 py-2 rounded-lg bg-violet-600 hover:bg-violet-500 text-white text-sm font-semibold disabled:opacity-60 transition-colors"
              >
                {loading ? 'Đang tạo...' : 'Tạo Key'}
              </button>
              <button onClick={onClose} className="px-4 py-2 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800 text-sm transition-colors">
                Huỷ
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="mb-3 p-3 rounded-lg bg-amber-950/40 border border-amber-700/50">
              <p className="text-xs text-amber-400 font-semibold mb-1">⚠️ Lưu key ngay — sẽ không hiển thị lại!</p>
            </div>

            <label className="block text-xs text-zinc-400 mb-1">API Key</label>
            <div className="flex items-center gap-2 mb-4">
              <code className="flex-1 px-3 py-2 rounded-lg bg-zinc-800 border border-zinc-700 text-emerald-400 text-xs font-mono break-all">
                {revealed ? newKey.api_key : '•'.repeat(Math.min(newKey.api_key.length, 40))}
              </code>
              <button onClick={() => setRevealed(v => !v)} className="p-1.5 rounded text-zinc-400 hover:text-white hover:bg-zinc-700">
                {revealed ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
              </button>
              <CopyButton text={newKey.api_key} />
            </div>

            <button
              onClick={onClose}
              className="w-full py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-white text-sm font-semibold transition-colors"
            >
              Đã lưu, đóng
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function EditLabelModal({ keyId, currentLabel, onClose, onSaved, apiBase, token }) {
  const [label, setLabel]     = useState(currentLabel);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState('');

  const submit = async () => {
    setError('');
    setLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/v1/api-keys/${keyId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ label }),
      });
      if (!res.ok) {
        const data = await res.json();
        setError(data?.detail || `HTTP ${res.status}`);
        return;
      }
      onSaved();
      onClose();
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
      <div className="w-full max-w-sm bg-zinc-900 border border-zinc-700 rounded-2xl shadow-2xl p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-white font-semibold text-sm">Đổi tên Key</h3>
          <button onClick={onClose} className="p-1 rounded text-zinc-400 hover:text-white hover:bg-zinc-800">
            <X className="w-4 h-4" />
          </button>
        </div>
        <input
          type="text" value={label} onChange={e => setLabel(e.target.value)} maxLength={64}
          className="w-full px-3 py-2 rounded-lg bg-zinc-800 border border-zinc-700 text-white text-sm focus:outline-none focus:border-violet-500 mb-4"
          autoFocus
        />
        {error && <p className="text-xs text-red-400 mb-3">{error}</p>}
        <div className="flex gap-2">
          <button onClick={submit} disabled={loading}
            className="flex-1 py-2 rounded-lg bg-violet-600 hover:bg-violet-500 text-white text-sm font-semibold disabled:opacity-60 transition-colors">
            {loading ? 'Đang lưu...' : 'Lưu'}
          </button>
          <button onClick={onClose} className="px-4 py-2 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800 text-sm transition-colors">Huỷ</button>
        </div>
      </div>
    </div>
  );
}

export default function ApiKeysPage() {
  const { session } = useAuth();
  const token       = session?.access_token || localStorage.getItem('vg_token') || '';
  const apiBase     = localStorage.getItem('vg_api_base') || '';

  const [keys, setKeys]           = useState([]);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [editKey, setEditKey]     = useState(null);
  const [revoking, setRevoking]   = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`${apiBase}/api/v1/api-keys`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.status === 403) {
        setError('API key management chỉ dành cho Pro.');
        setKeys([]);
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setKeys(data.keys || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [apiBase, token]);

  useEffect(() => { load(); }, [load]);

  const revoke = async (keyId) => {
    if (!window.confirm('Xác nhận thu hồi key này? Hành động không thể hoàn tác.')) return;
    setRevoking(keyId);
    try {
      const res = await fetch(`${apiBase}/api/v1/api-keys/${keyId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await load();
    } catch (e) {
      alert(`Lỗi: ${e.message}`);
    } finally {
      setRevoking(null);
    }
  };

  const activeKeys = keys.filter(k => k.is_active);

  return (
    <div className="max-w-2xl mx-auto px-4 md:px-8 py-8 md:py-12">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-violet-500 to-indigo-500 flex items-center justify-center">
            <Key className="w-4 h-4 text-white" />
          </div>
          <div>
            <h1 className="text-white font-bold text-lg">API Keys</h1>
            <p className="text-zinc-400 text-xs">Tối đa {MAX_KEYS} key. Quota chia sẻ với tài khoản web.</p>
          </div>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          disabled={activeKeys.length >= MAX_KEYS || loading || !!error}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-violet-600 hover:bg-violet-500 text-white text-xs font-semibold disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          <Plus className="w-3.5 h-3.5" />
          Tạo Key
        </button>
      </div>

      {/* Docs link */}
      <div className="mb-6 p-3 rounded-xl bg-zinc-800/50 border border-zinc-700/50 flex items-center justify-between">
        <div>
          <p className="text-white text-xs font-semibold">Xác thực bằng header</p>
          <code className="text-zinc-400 text-xs">X-API-Key: vidgrab_xxxxxxxx...</code>
        </div>
        <a
          href="/api-docs"
          className="text-violet-400 hover:text-violet-300 text-xs underline underline-offset-2"
        >
          Xem docs →
        </a>
      </div>

      {/* Content */}
      {loading && (
        <div className="flex justify-center py-12">
          <div className="w-6 h-6 border-2 border-violet-500/30 border-t-violet-500 rounded-full animate-spin" />
        </div>
      )}

      {!loading && error && (
        <div className="p-4 rounded-xl bg-red-950/30 border border-red-700/40 text-center">
          <p className="text-red-400 text-sm">{error}</p>
          {error.includes('Pro') && (
            <a href="/upgrade" className="inline-block mt-2 text-xs text-violet-400 hover:text-violet-300 underline">
              Nâng cấp Pro →
            </a>
          )}
        </div>
      )}

      {!loading && !error && keys.length === 0 && (
        <div className="text-center py-12">
          <Key className="w-10 h-10 text-zinc-600 mx-auto mb-3" />
          <p className="text-zinc-400 text-sm">Chưa có API key nào.</p>
          <p className="text-zinc-500 text-xs mt-1">Nhấn "Tạo Key" để bắt đầu.</p>
        </div>
      )}

      {!loading && !error && keys.length > 0 && (
        <div className="space-y-3">
          {keys.map(k => (
            <div
              key={k.id}
              className={`p-4 rounded-xl border transition-colors ${
                k.is_active
                  ? 'bg-zinc-900 border-zinc-700'
                  : 'bg-zinc-900/40 border-zinc-800 opacity-60'
              }`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className="text-white text-sm font-medium truncate">{k.label}</span>
                    {!k.is_active && (
                      <span className="shrink-0 text-xs px-1.5 py-0.5 rounded bg-zinc-700 text-zinc-400">Thu hồi</span>
                    )}
                  </div>
                  <code className="text-xs text-zinc-400 font-mono">{k.key_prefix}…</code>
                  <div className="flex items-center gap-3 mt-1.5 text-xs text-zinc-500">
                    <span>Tổng: {k.requests_total?.toLocaleString() || 0} req</span>
                    <span>·</span>
                    <span>Tạo: {k.created_at?.slice(0, 10)}</span>
                    {k.last_used_at && (
                      <>
                        <span>·</span>
                        <span>Dùng lần cuối: {k.last_used_at.slice(0, 10)}</span>
                      </>
                    )}
                  </div>
                </div>

                {k.is_active && (
                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      onClick={() => setEditKey(k)}
                      className="p-1.5 rounded text-zinc-400 hover:text-white hover:bg-zinc-700 transition-colors"
                      title="Đổi tên"
                    >
                      <Pencil className="w-3.5 h-3.5" />
                    </button>
                    <button
                      onClick={() => revoke(k.id)}
                      disabled={revoking === k.id}
                      className="p-1.5 rounded text-zinc-400 hover:text-red-400 hover:bg-zinc-700 transition-colors disabled:opacity-50"
                      title="Thu hồi"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Modals */}
      {showCreate && (
        <CreateModal
          onClose={() => setShowCreate(false)}
          onCreated={load}
          apiBase={apiBase}
          token={token}
        />
      )}

      {editKey && (
        <EditLabelModal
          keyId={editKey.id}
          currentLabel={editKey.label}
          onClose={() => setEditKey(null)}
          onSaved={load}
          apiBase={apiBase}
          token={token}
        />
      )}
    </div>
  );
}

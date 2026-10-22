import { useState, useEffect, useCallback } from 'react';
import { Key, Plus, Trash2, Pencil, Copy, Check, X, Eye, EyeOff } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { API_BASE } from '../lib/apiBase';
import { useTranslation } from 'react-i18next';
import { useLang, useLocalePath } from '../i18n';

const MAX_KEYS = 3;

function CopyButton({ text }) {
  const { t } = useTranslation('apiKeys');
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <button
      onClick={copy}
      className="p-1.5 rounded text-fg-muted hover:text-fg hover:bg-surface-2 transition-colors"
      title={t('copy')}
    >
      {copied ? <Check className="w-3.5 h-3.5 text-success" /> : <Copy className="w-3.5 h-3.5" />}
    </button>
  );
}

function CreateModal({ onClose, onCreated, apiBase, token }) {
  const { t } = useTranslation('apiKeys');
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
      <div className="w-full max-w-md bg-canvas border border-line rounded-2xl shadow-2xl p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-fg font-semibold text-sm">{t('create.title')}</h3>
          <button onClick={onClose} className="p-1 rounded text-fg-muted hover:text-fg hover:bg-surface">
            <X className="w-4 h-4" />
          </button>
        </div>

        {!newKey ? (
          <>
            <label className="block text-xs text-fg-muted mb-1">{t('create.label')}</label>
            <input
              type="text"
              value={label}
              onChange={e => setLabel(e.target.value)}
              maxLength={64}
              className="w-full px-3 py-2 rounded-lg bg-surface border border-line text-fg text-sm focus:outline-none focus:border-line mb-4"
              placeholder="My API Key"
              autoFocus
            />

            {error && <p className="text-xs text-danger mb-3">{error}</p>}

            <div className="flex gap-2">
              <button
                onClick={submit}
                disabled={loading}
                className="flex-1 py-2 rounded-lg bg-accent hover:bg-accent-hover text-accent-fg text-sm font-semibold disabled:opacity-60 transition-colors"
              >
                {loading ? t('create.creating') : t('create.submit')}
              </button>
              <button onClick={onClose} className="px-4 py-2 rounded-lg text-fg-muted hover:text-fg hover:bg-surface text-sm transition-colors">
                {t('cancel')}
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="mb-3 p-3 rounded-lg bg-accent-soft border border-accent/50">
              <p className="text-xs text-accent-text font-semibold mb-1">{t('create.saveWarning')}</p>
            </div>

            <label className="block text-xs text-fg-muted mb-1">API Key</label>
            <div className="flex items-center gap-2 mb-4">
              <code className="flex-1 px-3 py-2 rounded-lg bg-surface border border-line text-success text-xs font-mono break-all">
                {revealed ? newKey.api_key : '•'.repeat(Math.min(newKey.api_key.length, 40))}
              </code>
              <button onClick={() => setRevealed(v => !v)} className="p-1.5 rounded text-fg-muted hover:text-fg hover:bg-surface-2">
                {revealed ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
              </button>
              <CopyButton text={newKey.api_key} />
            </div>

            <button
              onClick={onClose}
              className="w-full py-2 rounded-lg bg-surface hover:bg-surface-2 text-fg text-sm font-semibold transition-colors"
            >
              {t('create.savedClose')}
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function EditLabelModal({ keyId, currentLabel, onClose, onSaved, apiBase, token }) {
  const { t } = useTranslation('apiKeys');
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
      <div className="w-full max-w-sm bg-canvas border border-line rounded-2xl shadow-2xl p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-fg font-semibold text-sm">{t('edit.title')}</h3>
          <button onClick={onClose} className="p-1 rounded text-fg-muted hover:text-fg hover:bg-surface">
            <X className="w-4 h-4" />
          </button>
        </div>
        <input
          type="text" value={label} onChange={e => setLabel(e.target.value)} maxLength={64}
          className="w-full px-3 py-2 rounded-lg bg-surface border border-line text-fg text-sm focus:outline-none focus:border-line mb-4"
          autoFocus
        />
        {error && <p className="text-xs text-danger mb-3">{error}</p>}
        <div className="flex gap-2">
          <button onClick={submit} disabled={loading}
            className="flex-1 py-2 rounded-lg bg-accent hover:bg-accent-hover text-accent-fg text-sm font-semibold disabled:opacity-60 transition-colors">
            {loading ? t('edit.saving') : t('edit.save')}
          </button>
          <button onClick={onClose} className="px-4 py-2 rounded-lg text-fg-muted hover:text-fg hover:bg-surface text-sm transition-colors">{t('cancel')}</button>
        </div>
      </div>
    </div>
  );
}

export default function ApiKeysPage() {
  const { t } = useTranslation('apiKeys');
  const lang = useLang();
  const lp = useLocalePath();
  const dateLocale = lang === 'en' ? 'en-US' : 'vi-VN';
  const { session } = useAuth();
  const token       = session?.access_token || localStorage.getItem('vg_token') || '';
  const apiBase     = API_BASE;

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
        setError(t('proOnly'));
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
  }, [apiBase, token, t]);

  useEffect(() => { load(); }, [load]);

  // This month's API calls (owner 14/10, GET /api/v1/api-keys/usage).
  const [usage, setUsage] = useState(null);
  useEffect(() => {
    if (!token) return undefined;
    let alive = true;
    fetch(`${apiBase}/api/v1/api-keys/usage`, { headers: { Authorization: `Bearer ${token}` } })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (alive) setUsage(d); })
      .catch(() => {});
    return () => { alive = false; };
  }, [apiBase, token, keys]);

  const revoke = async (keyId) => {
    if (!window.confirm(t('revoke.confirm'))) return;
    setRevoking(keyId);
    try {
      const res = await fetch(`${apiBase}/api/v1/api-keys/${keyId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await load();
    } catch (e) {
      alert(t('revoke.error', { message: e.message }));
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
          <div className="w-9 h-9 rounded-xl bg-accent flex items-center justify-center">
            <Key className="w-4 h-4 text-accent-fg" />
          </div>
          <div>
            <h1 className="text-fg font-bold text-lg">API Keys</h1>
            {/* wording: BA review */}
            <p className="text-fg-muted text-xs">{t('subtitle', { max: MAX_KEYS })}</p>
          </div>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          disabled={activeKeys.length >= MAX_KEYS || loading || !!error}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-accent hover:bg-accent-hover text-accent-fg text-xs font-semibold disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          <Plus className="w-3.5 h-3.5" />
          {t('create.submit')}
        </button>
      </div>

      {/* Monthly API quota — wording: BA review */}
      {usage && usage.limit !== 0 && (
        <div className="mb-4 p-3 rounded-xl bg-surface border border-line">
          <div className="flex items-center justify-between text-xs">
            <span className="text-fg font-semibold">{t('usage.title', { month: usage.month })}</span>
            <span className="text-fg-2 tabular-nums">
              {usage.limit === -1
                ? t('usage.unlimited', { used: usage.used ?? '—' })
                : `${usage.used ?? '—'} / ${Number(usage.limit).toLocaleString(dateLocale)}`}
            </span>
          </div>
          {usage.limit > 0 && typeof usage.used === 'number' && (
            <div className="mt-2 h-1.5 rounded-full bg-surface-2 overflow-hidden" aria-hidden="true">
              <div className="h-full bg-accent" style={{ width: `${Math.min(100, (usage.used / usage.limit) * 100)}%` }} />
            </div>
          )}
          <p className="mt-1.5 text-fg-muted text-[11px]">
            {t('usage.reset', { date: new Date(usage.reset_at).toLocaleDateString(dateLocale) })}
          </p>
        </div>
      )}

      {/* Docs link */}
      <div className="mb-6 p-3 rounded-xl bg-surface border border-line flex items-center justify-between">
        <div>
          <p className="text-fg text-xs font-semibold">{t('auth.title')}</p>
          <code className="text-fg-muted text-xs">X-API-Key: vidgrab_xxxxxxxx...</code>
          <p className="text-fg-muted text-[11px]">{t('auth.or')}</p>
        </div>
        <a
          href={lp('/api-docs')}
          className="text-fg-2 hover:text-fg-2 text-xs underline underline-offset-2"
        >
          {t('docs')}
        </a>
      </div>

      {/* Content */}
      {loading && (
        <div className="flex justify-center py-12">
          <div className="w-6 h-6 border-2 border-line border-t-line rounded-full animate-spin" />
        </div>
      )}

      {!loading && error && (
        <div className="p-4 rounded-xl bg-danger-soft border border-danger/40 text-center">
          <p className="text-danger text-sm">{error}</p>
          {error.includes('Pro') && (
            <a href={lp('/upgrade')} className="inline-block mt-2 text-xs text-fg-2 hover:text-fg-2 underline">
              {t('upgrade')}
            </a>
          )}
        </div>
      )}

      {!loading && !error && keys.length === 0 && (
        <div className="text-center py-12">
          <Key className="w-10 h-10 text-fg-muted mx-auto mb-3" />
          <p className="text-fg-muted text-sm">{t('empty.title')}</p>
          <p className="text-fg-muted text-xs mt-1">{t('empty.hint')}</p>
        </div>
      )}

      {!loading && !error && keys.length > 0 && (
        <div className="space-y-3">
          {keys.map(k => (
            <div
              key={k.id}
              className={`p-4 rounded-xl border transition-colors ${
                k.is_active
                  ? 'bg-canvas border-line'
                  : 'bg-surface-2 border-line opacity-60'
              }`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className="text-fg text-sm font-medium truncate">{k.label}</span>
                    {!k.is_active && (
                      <span className="shrink-0 text-xs px-1.5 py-0.5 rounded bg-surface-2 text-fg-muted">{t('revoke.badge')}</span>
                    )}
                  </div>
                  <code className="text-xs text-fg-muted font-mono">{k.key_prefix}…</code>
                  <div className="flex items-center gap-3 mt-1.5 text-xs text-fg-muted">
                    <span>{t('item.total', { n: k.requests_total?.toLocaleString() || 0 })}</span>
                    <span>·</span>
                    <span>{t('item.created', { date: k.created_at?.slice(0, 10) })}</span>
                    {k.last_used_at && (
                      <>
                        <span>·</span>
                        <span>{t('item.lastUsed', { date: k.last_used_at.slice(0, 10) })}</span>
                      </>
                    )}
                  </div>
                </div>

                {k.is_active && (
                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      onClick={() => setEditKey(k)}
                      className="p-1.5 rounded text-fg-muted hover:text-fg hover:bg-surface-2 transition-colors"
                      title={t('item.rename')}
                    >
                      <Pencil className="w-3.5 h-3.5" />
                    </button>
                    <button
                      onClick={() => revoke(k.id)}
                      disabled={revoking === k.id}
                      className="p-1.5 rounded text-fg-muted hover:text-danger hover:bg-surface-2 transition-colors disabled:opacity-50"
                      title={t('revoke.title')}
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

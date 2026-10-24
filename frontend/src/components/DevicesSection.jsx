import { useState, useEffect, useCallback } from 'react';
import { MonitorSmartphone, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../context/AuthContext';
import { API_BASE } from '../lib/apiBase';

const KIND_ORDER = ['web', 'extension', 'app'];

function formatWhen(epoch, t, lang) {
  if (!epoch) return '—';
  const diff = Math.max(0, Math.floor(Date.now() / 1000 - epoch));
  const d = new Date(epoch * 1000);
  if (diff < 60) return t('devices.justNow');
  if (diff < 7 * 86400) {
    try {
      const rtf = new Intl.RelativeTimeFormat(lang, { numeric: 'auto' });
      if (diff < 3600) return rtf.format(-Math.floor(diff / 60), 'minute');
      if (diff < 86400) return rtf.format(-Math.floor(diff / 3600), 'hour');
      return rtf.format(-Math.floor(diff / 86400), 'day');
    } catch { /* fall through */ }
  }
  return d.toLocaleString(lang);
}

// wording: BA review
export default function DevicesSection() {
  const { t, i18n } = useTranslation('settings');
  const { isAuthenticated, withAuth, signOut } = useAuth();
  const [data, setData] = useState(null);
  const [status, setStatus] = useState('loading'); // loading | ok | error | unavailable
  const [busyId, setBusyId] = useState(null);
  const [actionError, setActionError] = useState(false);

  const [reloadKey, setReloadKey] = useState(0);
  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  useEffect(() => {
    if (!isAuthenticated) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`${API_BASE}/api/v1/devices`, withAuth({}));
        if (cancelled) return;
        if (r.status === 503) { setStatus('unavailable'); return; }
        if (!r.ok) throw new Error(String(r.status));
        const body = await r.json();
        if (cancelled) return;
        setData(body);
        setStatus('ok');
      } catch {
        if (!cancelled) setStatus('error');
      }
    })();
    return () => { cancelled = true; };
  }, [isAuthenticated, withAuth, reloadKey]);

  const retry = () => { setStatus('loading'); reload(); };

  if (!isAuthenticated) return null;

  const handleSignOut = async (dev) => {
    const msg = dev.current ? t('devices.confirmCurrent') : t('devices.confirm');
    if (!window.confirm(msg)) return;
    setBusyId(dev.id);
    setActionError(false);
    try {
      const r = await fetch(`${API_BASE}/api/v1/devices/${encodeURIComponent(dev.id)}/signout`, withAuth({ method: 'POST' }));
      if (!r.ok && r.status !== 404) throw new Error(String(r.status));
      const body = r.ok ? await r.json().catch(() => ({})) : {};
      if (body.current || (r.ok && dev.current)) {
        await signOut();
        return;
      }
      reload();
    } catch {
      setActionError(true);
    }
    setBusyId(null);
  };

  const devices = data?.devices || [];
  const max = data?.max_devices ?? 0;
  const lang = i18n.language;

  return (
    <div className="p-6 rounded-2xl bg-surface border border-line shadow-lg">
      <div className="flex items-start gap-3 mb-4">
        <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-accent flex-shrink-0">
          <MonitorSmartphone className="w-4 h-4 text-accent-fg" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
            <h3 className="text-base font-semibold text-fg">{t('devices.title')}</h3>
            {status === 'ok' && max > 0 && (
              <span className="text-xs font-semibold text-fg-2">{t('devices.count', { n: devices.length, max })}</span>
            )}
          </div>
          <p className="text-xs text-fg-muted mt-0.5">{t('devices.desc', { max: max || '—' })}</p>
        </div>
      </div>

      {status === 'loading' && !data && (
        <div className="flex items-center gap-2 text-xs text-fg-muted" role="status">
          <Loader2 className="w-3.5 h-3.5 animate-spin" /> {t('devices.loading')}
        </div>
      )}

      {(status === 'error' || status === 'unavailable') && (
        <div className="flex flex-wrap items-center gap-3" role="alert">
          <p className="text-xs text-error">{status === 'unavailable' ? t('devices.unavailable') : t('devices.error')}</p>
          <button
            onClick={retry}
            className="px-3 py-1.5 rounded-lg bg-surface-2 border border-line text-fg-2 text-xs font-semibold hover:bg-line transition-colors cursor-pointer"
          >
            {t('devices.retry')}
          </button>
        </div>
      )}

      {status === 'ok' && devices.length === 0 && (
        <p className="text-xs text-fg-muted">{t('devices.empty')}</p>
      )}

      {status === 'ok' && devices.length > 0 && (
        <ul className="space-y-2">
          {devices.map((dev) => {
            const kinds = KIND_ORDER.filter((k) => (dev.kinds || []).includes(k));
            return (
              <li key={dev.id} className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3 px-3 py-2.5 rounded-xl bg-surface border border-line/60">
                <div className="flex-1 min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-sm font-medium text-fg break-words">{dev.label || t('devices.unknown')}</span>
                    {dev.current && (
                      <span className="px-2 py-0.5 rounded-full bg-accent/10 text-accent-text border border-accent/30 text-[11px] font-semibold">
                        {t('devices.current')}
                      </span>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5 mt-1">
                    {kinds.map((k) => (
                      <span key={k} className="px-2 py-0.5 rounded-md bg-surface-2 border border-line text-fg-2 text-[11px]">
                        {t(`devices.kind.${k}`)}
                      </span>
                    ))}
                  </div>
                  <p className="text-[11px] text-fg-muted mt-1">
                    {t('devices.lastSeen', { when: formatWhen(dev.last_seen, t, lang) })}
                  </p>
                </div>
                <button
                  onClick={() => handleSignOut(dev)}
                  disabled={busyId !== null}
                  className="self-start sm:self-center px-3 py-1.5 rounded-lg bg-error/10 text-error border border-error/20 text-xs font-semibold hover:bg-error/20 transition-colors cursor-pointer disabled:opacity-50"
                >
                  {busyId === dev.id ? t('devices.signingOut') : t('devices.signOut')}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {actionError && <p className="mt-3 text-xs text-error" role="alert">{t('devices.actionError')}</p>}
    </div>
  );
}

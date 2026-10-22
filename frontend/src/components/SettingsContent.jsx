import { useState, useEffect } from 'react';
import { trackEvent, EVENT } from '../utils/trackEvent';
import {
  Settings, Server, Palette, Bell, BellOff, Shield,
  Clock, Database, FileDown, Key, Info, ExternalLink,
  CheckCircle2, XCircle, AlertCircle,
} from 'lucide-react';
import { Trans, useTranslation } from 'react-i18next';
import i18n, { useLocalePath } from '../i18n';
import { usePlans } from '../lib/usePlans';
import { getPermission, requestPermission, isSupported } from '../utils/notifications.js';

const API_BASE = import.meta.env.VITE_API_URL || '';

// wording: BA review
// Pin numbers come from /api/v1/plans; export ranges mirror GET /user/history/export
// (backend app/api/user.py: 30 days non-Pro, 365 days Pro).
function buildRetentionRules(free, pro) {
  const tr = (key, opts) => i18n.t(key, { ns: 'settings', ...opts });
  const pinNote = (p) => (p ? tr('data.rules.pinned.pinNote', { pins: p.pins, days: p.pin_days }) : null);
  return [
    { label: tr('data.rules.temp.label'), value: tr('data.rules.temp.value'), note: tr('data.rules.temp.note') },
    {
      label: tr('data.rules.pinned.label'),
      value: free ? tr('data.rules.pinned.days', { days: free.pin_days }) : '—',
      note: free && pro
        ? tr('data.rules.pinned.noteBoth', { free: pinNote(free), pro: pinNote(pro) })
        : tr('data.rules.pinned.noteDefault'),
    },
    { label: tr('data.rules.exportFree.label'), value: tr('data.rules.exportFree.value'), note: tr('data.rules.exportFree.note') },
    { label: tr('data.rules.exportPro.label'), value: tr('data.rules.exportPro.value'), note: tr('data.rules.exportPro.note') },
    { label: tr('data.rules.meta.label'), value: tr('data.rules.meta.value'), note: tr('data.rules.meta.note') },
  ];
}

export default function SettingsContent() {
  const { t } = useTranslation('settings');
  const lp = useLocalePath();
  const { free: freePlan, pro: proPlan } = usePlans();
  const [appVersion, setAppVersion] = useState(null);
  const [apiOk, setApiOk] = useState(null);
  const [notifPerm, setNotifPerm] = useState(getPermission());
  const [apiKey, setApiKey] = useState(null);
  const [apiKeyLoading, setApiKeyLoading] = useState(false);

  useEffect(() => {
    fetch(`${API_BASE}/health`)
      .then(r => r.json())
      .then(d => { setApiOk(true); setAppVersion(d.app_version || '—'); })
      .catch(() => setApiOk(false));
  }, []);

  useEffect(() => {
    fetch(`${API_BASE}/api/v1/user/api-key/status`, { credentials: 'include' })
      .then(r => r.json())
      .then(d => { if (d.has_key) setApiKey('••••••••' + (d.key_suffix || '')); })
      .catch(() => {});
  }, []);

  const handleNotifToggle = async () => {
    if (!isSupported()) return;
    if (notifPerm === 'granted') {
      setNotifPerm('denied-by-user');
      return;
    }
    const perm = await requestPermission();
    setNotifPerm(perm);
  };

  const handleGenerateKey = async () => {
    setApiKeyLoading(true);
    try {
      const r = await fetch(`${API_BASE}/api/v1/user/api-key/generate`, {
        method: 'POST', credentials: 'include',
      });
      const d = await r.json();
      if (d.api_key) {
        setApiKey(d.api_key);
        trackEvent(EVENT.API_KEY_CREATED, {});
      }
    } catch { /* ignore */ }
    setApiKeyLoading(false);
  };

  const handleRevokeKey = async () => {
    if (!window.confirm(t('access.confirmRevoke'))) return;
    await fetch(`${API_BASE}/api/v1/user/api-key/revoke`, { method: 'DELETE', credentials: 'include' });
    setApiKey(null);
  };

  const notifLabel = () => {
    if (!isSupported()) return t('notif.unsupported');
    if (notifPerm === 'granted') return t('notif.on');
    if (notifPerm === 'denied') return t('notif.denied');
    return t('notif.off');
  };

  const notifEnabled = notifPerm === 'granted';

  return (
    <div className="space-y-6">
      {/* ── Header ────────────────────────────────────────── */}
      <div>
        <div className="flex items-center gap-3 mb-1">
          <Settings className="w-5 h-5 text-accent-text" />
          <h2 className="text-2xl font-bold text-fg tracking-tight">{t('title')}</h2>
        </div>
        <p className="text-sm text-fg-muted ml-8">{t('subtitle')}</p>
      </div>

      <div className="space-y-4">

        {/* ── API Connection ─────────────────────────────── */}
        <div className="p-6 rounded-2xl bg-surface border border-line shadow-lg">
          <div className="flex items-center gap-3 mb-4">
            <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-success">
              <Server className="w-4 h-4 text-success-fg" />
            </div>
            <div className="flex-1">
              <h3 className="text-base font-semibold text-fg">API Connection</h3>
              <p className="text-xs text-fg-muted">{t('api.desc')}</p>
            </div>
            <div className="flex items-center gap-2">
              {apiOk === null && <div className="w-2 h-2 rounded-full bg-fg-muted animate-pulse" />}
              {apiOk === true && <CheckCircle2 className="w-4 h-4 text-success" />}
              {apiOk === false && <XCircle className="w-4 h-4 text-error" />}
              <span className={`text-xs font-medium ${apiOk === true ? 'text-success' : apiOk === false ? 'text-error' : 'text-fg-muted'}`}>
                {apiOk === null ? t('api.checking') : apiOk ? t('api.ok') : t('api.fail')}
              </span>
            </div>
          </div>
          {appVersion && (
            <div className="flex items-center gap-2 px-3 py-2 rounded-xl bg-surface border border-line">
              <Info className="w-3.5 h-3.5 text-fg-muted flex-shrink-0" />
              <span className="text-xs text-fg-muted">{t('api.backendVersion')} <span className="text-fg font-mono font-semibold">{appVersion}</span></span>
            </div>
          )}
        </div>

        {/* ── Notifications ──────────────────────────────── */}
        <div className="p-6 rounded-2xl bg-surface border border-line shadow-lg">
          <div className="flex items-center gap-3 mb-4">
            <div className={`flex items-center justify-center w-9 h-9 rounded-lg ${notifEnabled ? 'bg-accent text-accent-fg' : 'bg-surface-2 text-fg-2'}`}>
              {notifEnabled ? <Bell className="w-4 h-4" /> : <BellOff className="w-4 h-4" />}
            </div>
            <div className="flex-1">
              <h3 className="text-base font-semibold text-fg">{t('notif.title')}</h3>
              <p className="text-xs text-fg-muted">{t('notif.desc')}</p>
            </div>
            <button
              onClick={handleNotifToggle}
              disabled={!isSupported() || notifPerm === 'denied'}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed ${notifEnabled ? 'bg-accent' : 'bg-line-strong'}`}
            >
              <span className={`inline-block h-4 w-4 transform rounded-full bg-surface shadow transition-transform ${notifEnabled ? 'translate-x-6' : 'translate-x-1'}`} />
            </button>
          </div>
          <div className="space-y-2 text-xs text-fg-muted">
            <p>{t('notif.status')} <span className={`font-semibold ${notifEnabled ? 'text-success' : 'text-fg-2'}`}>{notifLabel()}</span></p>
            <p className="flex items-start gap-1.5">
              <AlertCircle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
              {t('notif.telegram')}
            </p>
            {notifPerm === 'denied' && (
              <p className="text-accent-text">{t('notif.reenable')}</p>
            )}
          </div>
        </div>

        {/* ── Data & Retention ───────────────────────────── */}
        <div className="p-6 rounded-2xl bg-surface border border-line shadow-lg">
          <div className="flex items-center gap-3 mb-4">
            <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-accent">
              <Clock className="w-4 h-4 text-accent-fg" />
            </div>
            <div>
              <h3 className="text-base font-semibold text-fg">{t('data.title')}</h3>
              <p className="text-xs text-fg-muted">{t('data.desc')}</p>
            </div>
          </div>
          <div className="space-y-2">
            {buildRetentionRules(freePlan, proPlan).map((rule, i) => (
              <div key={i} className="flex items-start gap-3 px-3 py-2.5 rounded-xl bg-surface border border-line/60">
                <Database className="w-3.5 h-3.5 text-fg-muted flex-shrink-0 mt-0.5" />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-medium text-fg-2">{rule.label}</span>
                    <span className="text-xs font-bold text-fg flex-shrink-0">{rule.value}</span>
                  </div>
                  <p className="text-[11px] text-fg-muted mt-0.5">{rule.note}</p>
                </div>
              </div>
            ))}
          </div>
          <div className="mt-3 flex items-center gap-2">
            <FileDown className="w-3.5 h-3.5 text-accent-text flex-shrink-0" />
            <span className="text-xs text-fg-muted">
              <Trans t={t} i18nKey="data.exportHint" components={{ b: <strong /> }} />
            </span>
          </div>
        </div>

        {/* ── API Access ─────────────────────────────────── */}
        <div className="p-6 rounded-2xl bg-surface border border-line shadow-lg">
          <div className="flex items-center gap-3 mb-4">
            <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-accent">
              <Key className="w-4 h-4 text-accent-fg" />
            </div>
            <div>
              <h3 className="text-base font-semibold text-fg">API Access</h3>
              <p className="text-xs text-fg-muted">{t('access.desc')}</p>
            </div>
            <a
              href={lp('/api-docs')}
              className="ml-auto inline-flex items-center gap-1 px-2.5 py-1 rounded-lg border border-line text-fg-2 text-xs hover:bg-surface-2 transition-colors cursor-pointer"
            >
              <ExternalLink className="w-3 h-3" /> API Docs
            </a>
          </div>
          {apiKey ? (
            <div className="space-y-2">
              <div className="flex items-center gap-2 px-3 py-2 rounded-xl bg-surface border border-line font-mono text-xs text-fg">
                <Shield className="w-3.5 h-3.5 text-fg-muted flex-shrink-0" />
                <span className="flex-1 truncate">{apiKey}</span>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={handleGenerateKey}
                  disabled={apiKeyLoading}
                  className="px-3 py-1.5 rounded-lg bg-accent/10 text-accent-text border border-accent/30 text-xs font-semibold hover:bg-accent/20 transition-colors cursor-pointer disabled:opacity-50"
                >
                  {t('access.regenerate')}
                </button>
                <button
                  onClick={handleRevokeKey}
                  className="px-3 py-1.5 rounded-lg bg-error/10 text-error border border-error/20 text-xs font-semibold hover:bg-error/20 transition-colors cursor-pointer"
                >
                  {t('access.revoke')}
                </button>
              </div>
              <p className="text-[11px] text-fg-muted"><Trans t={t} i18nKey="access.header" values={{ header: 'Authorization: Bearer <key>' }} components={{ c: <code className="bg-surface px-1 rounded" /> }} /></p>
            </div>
          ) : (
            <div className="space-y-2">
              <p className="text-xs text-fg-muted">{t('access.none')}</p>
              <button
                onClick={handleGenerateKey}
                disabled={apiKeyLoading}
                className="px-4 py-2 rounded-xl bg-surface-2 border border-line text-fg-2 text-xs font-semibold hover:bg-line transition-colors cursor-pointer disabled:opacity-50"
              >
                {apiKeyLoading ? t('access.creating') : t('access.create')}
              </button>
              <p className="text-[11px] text-fg-muted">{t('access.needLogin')}</p>
            </div>
          )}
        </div>

        {/* ── App Info ───────────────────────────────────── */}
        <div className="p-6 rounded-2xl bg-surface border border-line shadow-lg">
          <div className="flex items-center gap-3 mb-4">
            <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-accent">
              <Palette className="w-4 h-4 text-accent-fg" />
            </div>
            <div>
              <h3 className="text-base font-semibold text-fg">{t('info.title')}</h3>
              <p className="text-xs text-fg-muted">{t('info.desc')}</p>
            </div>
          </div>
          <div className="space-y-2 text-xs">
            <div className="flex items-center justify-between px-3 py-2 rounded-xl bg-surface border border-line/60">
              <span className="text-fg-muted">Frontend</span>
              <span className="font-mono font-semibold text-fg">v1.6.0</span>
            </div>
            <div className="flex items-center justify-between px-3 py-2 rounded-xl bg-surface border border-line/60">
              <span className="text-fg-muted">Backend</span>
              <span className="font-mono font-semibold text-fg">{appVersion || '—'}</span>
            </div>
            <div className="flex items-center justify-between px-3 py-2 rounded-xl bg-surface border border-line/60">
              <span className="text-fg-muted">{t('info.compat')}</span>
              <span className={`font-semibold ${appVersion ? 'text-success' : 'text-fg-muted'}`}>
                {appVersion ? t('info.compatOk') : t('info.compatUnknown')}
              </span>
            </div>
            <div className="flex items-center justify-between px-3 py-2 rounded-xl bg-surface border border-line/60">
              <span className="text-fg-muted">{t('info.support')}</span>
              <a
                href="https://t.me/vidgrab_support"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-accent-text hover:text-primary-light transition-colors"
              >
                Telegram <ExternalLink className="w-3 h-3" />
              </a>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}

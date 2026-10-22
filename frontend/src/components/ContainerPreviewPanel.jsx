/**
 * ContainerPreviewPanel — Phase 25 PR3 (hardened)
 *
 * Handles all terminal states from DiscoveryJobSnapshot:
 *   success  → normal browse + queue
 *   partial  → browse with "partial results" banner
 *   expired  → "Khám phá lại" CTA, no browse
 *   failed   → error message
 *
 * Capability gating (cookie_required / proxy_required):
 *   → shows gating banner explaining what's needed instead of cryptic error
 *
 * Section expand errors are isolated per-section (one section failing
 * does not crash the panel).
 *
 * Queue success shows dedupe summary inline before closing.
 */
import { useState, useCallback, useMemo, useEffect } from 'react';
import { useTranslation, Trans } from 'react-i18next';
import {
  X, CheckSquare, Square, Music, Video, Image, ChevronDown, ChevronRight,
  Download, Filter, RefreshCw, FileDown, AlertTriangle, Loader2, Info,
  Play, Lock, Shield, RotateCcw, CheckCircle,
} from 'lucide-react';

const API = `${import.meta.env.VITE_API_URL || ''}/api/v1`;

const QUALITY_OPTIONS = [
  { value: 'mp3_128',    label: 'MP3 128kbps' },
  { value: 'mp3_320',    label: 'MP3 320kbps' },
  { value: 'video_720',  label: 'Video 720p' },
  { value: 'video_1080', label: 'Video 1080p' },
  { value: 'video',      labelKey: 'quality.best' },
];

const MEDIA_TYPE_ICONS = { video: Video, audio: Music, image: Image, mixed: Play };

function fmtDuration(ms) {
  if (!ms) return '';
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const sec = String(s % 60).padStart(2, '0');
  return `${m}:${sec}`;
}

function fmtViews(n) {
  if (!n) return '';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

// ── Capability gating banners ─────────────────────────────────────────────────

function CapabilityGateBanner({ gate, platform }) {
  const { t } = useTranslation('containerPreview');
  const isCookie         = gate?.required === 'cookie';
  const isProxy          = gate?.required === 'proxy';
  const isProxyBad       = gate?.required === 'proxy_unhealthy';
  const isSingleDisabled = gate?.required === 'single_disabled';
  // Phase 26-B cookie states
  const isCookieUnavailable = gate?.required === 'cookie_unavailable';
  const isCookieExpired     = gate?.required === 'cookie_expired';
  const isCookieBlocked     = gate?.required === 'cookie_blocked';
  const isCookiePartial     = gate?.required === 'cookie_partial';

  const platformLabel = platform
    ? platform.charAt(0).toUpperCase() + platform.slice(1)
    : t('gate.platformFallback');

  if (isCookieUnavailable) {
    return (
      <div className="mx-4 mt-4 p-3 bg-surface border border-line rounded-lg">
        <div className="flex items-start gap-2">
          <Lock size={15} className="text-fg-muted shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-medium text-fg-2">{t('gate.cookieUnavailable.title')}</p>
            <p className="text-xs text-fg-muted mt-1">
              {t('gate.cookieUnavailable.body', { platform: platformLabel })}
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (isCookieExpired) {
    return (
      <div className="mx-4 mt-4 p-3 bg-accent-soft border border-accent/50 rounded-lg">
        <div className="flex items-start gap-2">
          <Lock size={15} className="text-accent-text shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-medium text-warning">{t('gate.cookieExpired.title')}</p>
            <p className="text-xs text-accent-text mt-1">
              {t('gate.cookieExpired.body', { platform: platformLabel })}
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (isCookieBlocked) {
    return (
      <div className="mx-4 mt-4 p-3 bg-accent-soft border border-accent/50 rounded-lg">
        <div className="flex items-start gap-2">
          <Shield size={15} className="text-accent-text shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-medium text-accent-text">{t('gate.cookieBlocked.title')}</p>
            <p className="text-xs text-accent-text mt-1">
              {t('gate.cookieBlocked.body', { platform: platformLabel })}
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (isCookiePartial) {
    return (
      <div className="mx-4 mt-4 p-3 bg-surface-2 border border-line rounded-lg">
        <div className="flex items-start gap-2">
          <Info size={15} className="text-fg-2 shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-medium text-fg-2">{t('gate.cookiePartial.title')}</p>
            <p className="text-xs text-fg-2 mt-1">
              {t('gate.cookiePartial.body')}
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (isSingleDisabled) {
    return (
      <div className="mx-4 mt-4 p-3 bg-surface border border-line rounded-lg">
        <div className="flex items-start gap-2">
          <Info size={15} className="text-fg-muted shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-medium text-fg-2">
              {t('gate.singleDisabled.title')}
            </p>
            <p className="text-xs text-fg-muted mt-1">
              <Trans
                t={t}
                i18nKey="gate.singleDisabled.body"
                components={{ strong: <strong className="text-fg-2" /> }}
              />
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (isProxyBad) {
    return (
      <div className="mx-4 mt-4 p-3 bg-danger-soft border border-danger/50 rounded-lg">
        <div className="flex items-start gap-2">
          <Shield size={15} className="text-danger shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-medium text-danger">
              {t('gate.proxyBad.title')}
            </p>
            <p className="text-xs text-danger mt-1">
              {t('gate.proxyBad.body')}
            </p>
          </div>
        </div>
      </div>
    );
  }

  const Icon  = isCookie ? Lock : Shield;
  const color = 'amber';
  return (
    <div className={`mx-4 mt-4 p-3 bg-${color}-900/20 border border-${color}-700/50 rounded-lg`}>
      <div className="flex items-start gap-2">
        <Icon size={15} className={`text-${color}-400 shrink-0 mt-0.5`} />
        <div>
          <p className="text-sm font-medium text-accent-text">
            {isCookie ? t('gate.generic.cookieTitle') : t('gate.generic.proxyTitle')}
          </p>
          <p className="text-xs text-accent-text mt-1">
            {isCookie
              ? t('gate.generic.cookieBody', { platform: platform ? platform.charAt(0).toUpperCase() + platform.slice(1) : 'Platform' })
              : t('gate.generic.proxyBody')}
          </p>
        </div>
      </div>
    </div>
  );
}

// ── Expired state ─────────────────────────────────────────────────────────────

function ExpiredState({ onRefresh }) {
  const { t } = useTranslation('containerPreview');
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-4">
      <div className="w-12 h-12 rounded-full bg-surface flex items-center justify-center">
        <RotateCcw size={22} className="text-fg-muted" />
      </div>
      <div className="text-center">
        <p className="text-sm font-medium text-fg-2">{t('expired.title')}</p>
        <p className="text-xs text-fg-muted mt-1">{t('expired.body')}</p>
      </div>
      {onRefresh && (
        <button
          onClick={onRefresh}
          className="flex items-center gap-2 px-4 py-2 bg-accent hover:bg-accent-hover text-accent-fg text-sm rounded-lg transition-colors"
        >
          <RotateCcw size={14} />
          {t('expired.rediscover')}
        </button>
      )}
    </div>
  );
}

// ── Queue success summary ─────────────────────────────────────────────────────

function QueueSuccessBanner({ summary, batchId, onClose }) {
  const { t } = useTranslation('containerPreview');
  return (
    <div className="mx-4 my-3 p-3 bg-success-soft border border-success/50 rounded-lg">
      <div className="flex items-start gap-2">
        <CheckCircle size={15} className="text-success shrink-0 mt-0.5" />
        <div className="flex-1">
          <p className="text-sm font-medium text-success">{t('queueSuccess.title')}</p>
          {summary && (
            <p className="text-xs text-success mt-0.5">
              {summary.accepted_count > 0 && <span>{t('queueSuccess.accepted', { count: summary.accepted_count })} </span>}
              {summary.dropped_count > 0 && <span>{t('queueSuccess.dropped', { count: summary.dropped_count })} </span>}
            </p>
          )}
          {batchId && (
            <p className="text-xs text-success mt-0.5 font-mono">batch: {batchId.slice(0, 8)}…</p>
          )}
        </div>
        <button onClick={onClose} className="text-success hover:text-success">
          <X size={13} />
        </button>
      </div>
    </div>
  );
}

// ── Item row ─────────────────────────────────────────────────────────────────

function ItemRow({ item, checked, onToggle }) {
  const Icon = MEDIA_TYPE_ICONS[item.media_type] || Play;
  return (
    <label className="flex items-center gap-3 px-4 py-2 hover:bg-surface cursor-pointer group">
      <button
        type="button"
        onClick={(e) => { e.preventDefault(); onToggle(item.id || item.url); }}
        className="shrink-0 text-fg-muted hover:text-fg transition-colors"
      >
        {checked ? <CheckSquare size={16} className="text-fg-2" /> : <Square size={16} />}
      </button>

      {item.thumbnail ? (
        <img
          src={item.thumbnail}
          alt=""
          className="w-10 h-10 rounded object-cover shrink-0 bg-surface-2"
          loading="lazy"
        />
      ) : (
        <div className="w-10 h-10 rounded bg-surface-2 flex items-center justify-center shrink-0">
          <Icon size={16} className="text-fg-muted" />
        </div>
      )}

      <div className="flex-1 min-w-0">
        <p className="text-sm text-fg truncate leading-tight">{item.title || '—'}</p>
        <p className="text-xs text-fg-muted truncate">{item.author || ''}</p>
      </div>

      <div className="flex items-center gap-3 text-xs text-fg-muted shrink-0">
        {item.views > 0 && <span>{fmtViews(item.views)}</span>}
        {item.duration_ms > 0 && <span>{fmtDuration(item.duration_ms)}</span>}
        <span className={`px-1.5 py-0.5 rounded text-xs ${
          item.media_type === 'video' ? 'bg-surface-2 text-fg-2'
          : item.media_type === 'audio' ? 'bg-success-soft text-success'
          : 'bg-accent-soft text-accent-text'
        }`}>
          {item.media_type}
        </span>
        {item.extras?.is_pinned && <span className="text-fg-2 text-xs">📌</span>}
      </div>
    </label>
  );
}

// ── Section block (isolated error handling) ───────────────────────────────────

function SectionBlock({ section, selected, onToggleItem, onSelectSection, containerId, onExpanded }) {
  const { t } = useTranslation('containerPreview');
  const [open, setOpen]         = useState(section.items_loaded);
  const [expanding, setExpanding] = useState(false);
  const [expandError, setExpandError] = useState(null);   // section-local error

  const handleToggle = async () => {
    if (!open && !section.items_loaded) {
      setExpanding(true);
      setExpandError(null);
      try {
        const res = await fetch(`${API}/container/${containerId}/expand`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ section: section.key }),
        });
        if (res.ok) {
          const data = await res.json();
          onExpanded?.(section.key, data.items || []);
        } else {
          const body = await res.json().catch(() => ({}));
          // Isolated failure — only this section errors, panel stays usable
          setExpandError(body.detail || t('section.httpError', { status: res.status }));
        }
      } catch (e) {
        setExpandError(e.message || t('section.expandFailed'));
      } finally {
        setExpanding(false);
      }
    }
    setOpen(v => !v);
  };

  const sectionSelected = section.items.length > 0 &&
    section.items.every(i => selected.has(i.id || i.url));

  return (
    <div className="border-b border-line">
      <div
        className="flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-surface select-none"
        onClick={handleToggle}
      >
        <div className="flex items-center gap-2">
          {expanding
            ? <Loader2 size={14} className="animate-spin text-fg-2" />
            : open ? <ChevronDown size={14} className="text-fg-muted" />
                   : <ChevronRight size={14} className="text-fg-muted" />
          }
          <span className="text-sm font-medium text-fg">{section.label}</span>
          {!section.items_loaded && !expandError && (
            <span className="text-xs text-fg-muted ml-1">{t('section.notLoaded')}</span>
          )}
          {expandError && (
            <span className="text-xs text-danger ml-1">{t('section.expandError')}</span>
          )}
        </div>
        {open && section.items.length > 0 && (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onSelectSection(section); }}
            className={`text-xs px-2 py-1 rounded transition-colors ${
              sectionSelected
                ? 'bg-accent text-accent-fg'
                : 'bg-surface-2 text-fg-2 hover:bg-line'
            }`}
          >
            {sectionSelected ? t('section.deselect') : t('section.select')}
          </button>
        )}
      </div>

      {/* Section-local expand error — does NOT crash parent panel */}
      {open && expandError && (
        <div className="mx-4 mb-2 px-3 py-2 bg-danger-soft border border-danger/40 rounded text-xs text-danger flex items-center gap-2">
          <AlertTriangle size={12} className="shrink-0" />
          <span>{expandError}</span>
          <button
            className="ml-auto text-danger hover:text-danger underline"
            onClick={(e) => { e.stopPropagation(); setExpandError(null); setOpen(false); }}
          >
            {t('retry')}
          </button>
        </div>
      )}

      {open && section.items_loaded && section.items.length > 0 && (
        <div>
          {section.items.map((item) => (
            <ItemRow
              key={item.id || item.url}
              item={item}
              checked={selected.has(item.id || item.url)}
              onToggle={onToggleItem}
            />
          ))}
          {section.has_more && (
            <p className="text-xs text-fg-muted px-4 py-2 italic">
              {t('section.hasMore')}
            </p>
          )}
        </div>
      )}

      {open && section.items_loaded && section.items.length === 0 && (
        <p className="text-xs text-fg-muted px-4 py-3 italic">{t('section.empty')}</p>
      )}

      {open && !section.items_loaded && !expandError && section.children?.length > 0 && (
        <div className="px-4 py-2 space-y-1">
          {section.children.map(child => (
            <div key={child.id} className="flex items-center gap-2 text-sm text-fg-2 py-1">
              {child.thumbnail && (
                <img src={child.thumbnail} alt="" className="w-8 h-8 rounded object-cover bg-surface-2" />
              )}
              <span className="flex-1 truncate">{child.label}</span>
              {child.item_count > 0 && (
                <span className="text-xs text-fg-muted">{t('section.tracks', { count: child.item_count })}</span>
              )}
            </div>
          ))}
          <p className="text-xs text-fg-muted italic mt-2">
            {t('section.expandHint')}
          </p>
        </div>
      )}
    </div>
  );
}

// ── Filter toolbar ────────────────────────────────────────────────────────────

function FilterToolbar({ filter, onChange }) {
  const { t } = useTranslation('containerPreview');
  return (
    <div className="flex flex-wrap items-center gap-2 px-4 py-2 border-b border-line bg-surface-2">
      <Filter size={14} className="text-fg-muted shrink-0" />
      <select
        value={filter.mediaType}
        onChange={e => onChange({ ...filter, mediaType: e.target.value })}
        className="text-xs bg-surface border border-line rounded px-2 py-1 text-fg-2"
      >
        <option value="all">{t('filter.allTypes')}</option>
        <option value="video">Video</option>
        <option value="audio">{t('filter.audio')}</option>
        <option value="image">{t('filter.image')}</option>
      </select>
      <select
        value={filter.sortBy}
        onChange={e => onChange({ ...filter, sortBy: e.target.value })}
        className="text-xs bg-surface border border-line rounded px-2 py-1 text-fg-2"
      >
        <option value="default">{t('filter.sortDefault')}</option>
        <option value="views">{t('filter.sortViews')}</option>
        <option value="duration_asc">{t('filter.sortDurationAsc')}</option>
        <option value="duration_desc">{t('filter.sortDurationDesc')}</option>
      </select>
    </div>
  );
}

// ── Main panel ────────────────────────────────────────────────────────────────

export default function ContainerPreviewPanel({
  container,
  jobSnapshot,
  isLoading,
  error,
  capabilityGate,
  onClose,
  onQueued,
  onRefresh,
}) {
  const { t } = useTranslation('containerPreview');
  const [selected, setSelected]       = useState(new Set());
  const [quality, setQuality]         = useState('mp3_320');
  const [isQueuing, setIsQueuing]     = useState(false);
  const [queueError, setQueueError]   = useState(null);
  const [queueSuccess, setQueueSuccess] = useState(null);   // { batchId, summary }
  const [sections, setSections]       = useState([]);
  const [filter, setFilter]           = useState({ mediaType: 'all', sortBy: 'default' });

  // Sync sections when container or jobSnapshot updates
  useEffect(() => {
    if (container?.sections?.length) {
      setSections(container.sections);
    } else if (jobSnapshot?.sections?.length) {
      setSections(jobSnapshot.sections);
    }
  }, [container?.sections, jobSnapshot?.sections]);

  // Derive status from jobSnapshot (PR2) or container (PR1 fallback)
  const status = jobSnapshot?.status ?? container?.status ?? 'discovering';
  const isExpired = status === 'expired';
  const isPartial = status === 'partial' || container?.status === 'partial';
  const isFailed  = status === 'failed'  || container?.status === 'failed';

  const allItems = useMemo(() => {
    return sections.flatMap(s => s.items_loaded ? (s.items || []) : []);
  }, [sections]);

  const visibleItems = useMemo(() => {
    let items = allItems;
    if (filter.mediaType !== 'all') items = items.filter(i => i.media_type === filter.mediaType);
    if (filter.sortBy === 'views')          items = [...items].sort((a, b) => (b.views || 0) - (a.views || 0));
    if (filter.sortBy === 'duration_asc')   items = [...items].sort((a, b) => (a.duration_ms || 0) - (b.duration_ms || 0));
    if (filter.sortBy === 'duration_desc')  items = [...items].sort((a, b) => (b.duration_ms || 0) - (a.duration_ms || 0));
    return items;
  }, [allItems, filter]);

  const toggleItem    = useCallback((key) => {
    setSelected(prev => { const n = new Set(prev); n.has(key) ? n.delete(key) : n.add(key); return n; });
  }, []);
  const selectAll     = () => setSelected(new Set(visibleItems.map(i => i.id || i.url)));
  const clearAll      = () => setSelected(new Set());
  const selectLatestN = (n) => setSelected(new Set(allItems.slice(0, n).map(i => i.id || i.url)));

  const selectSection = (section) => {
    const keys = (section.items || []).map(i => i.id || i.url);
    setSelected(prev => {
      const next = new Set(prev);
      const allChecked = keys.every(k => next.has(k));
      if (allChecked) keys.forEach(k => next.delete(k));
      else keys.forEach(k => next.add(k));
      return next;
    });
  };

  const handleExpanded = (sectionKey, newItems) => {
    setSections(prev => prev.map(s =>
      s.key === sectionKey
        ? { ...s, items: newItems, items_loaded: true, item_count: newItems.length }
        : s
    ));
  };

  const handleQueue = async () => {
    const containerId = container?.container_id || jobSnapshot?.container_id;
    if (!containerId || selected.size === 0) return;
    setIsQueuing(true);
    setQueueError(null);
    setQueueSuccess(null);

    try {
      const res = await fetch(`${API}/container/${containerId}/queue`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          item_ids: Array.from(selected),
          queue_mode: 'selected',
          apply_dedupe: true,
          quality,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

      // Show inline dedupe summary before closing
      setQueueSuccess({ batchId: data.batch_id, summary: data.dedupe_summary });
      onQueued?.(data.batch_id, data);
      // Auto-close after 2s so user can read summary
      setTimeout(() => onClose?.(), 2000);
    } catch (e) {
      setQueueError(e.message);
    } finally {
      setIsQueuing(false);
    }
  };

  const handleDownloadManifest = () => {
    const containerId = container?.container_id || jobSnapshot?.container_id;
    if (!containerId) return;
    window.open(`${API}/container/${containerId}/manifest`, '_blank');
  };

  // ── Progress bar (during discovery) ────────────────────────────────────────
  const progress = jobSnapshot?.progress_pct ?? 0;
  const showProgress = isLoading && progress > 0;

  // ── Status badge ────────────────────────────────────────────────────────────
  const statusBadge = {
    ready:       { label: t('status.ready'),      cls: 'bg-success-soft text-success' },
    success:     { label: t('status.ready'),      cls: 'bg-success-soft text-success' },
    partial:     { label: t('status.partial'),       cls: 'bg-accent-soft text-accent-text' },
    discovering: { label: t('status.discovering'),   cls: 'bg-surface-2 text-fg-2 animate-pulse' },
    resolving:   { label: t('status.resolving'), cls: 'bg-surface-2 text-fg-2 animate-pulse' },
    queued:      { label: t('status.queued'), cls: 'bg-surface-2 text-fg-muted animate-pulse' },
    failed:      { label: t('status.failed'),       cls: 'bg-danger-soft text-danger' },
    expired:     { label: t('status.expired'),        cls: 'bg-surface-2 text-fg-muted' },
    pending:     { label: t('status.pending'),       cls: 'bg-surface-2 text-fg-muted' },
  }[status] || { label: status, cls: 'bg-surface-2 text-fg-muted' };

  const containerTitle = container?.title || jobSnapshot?.summary?.title || '';
  const platform       = container?.platform || jobSnapshot?.platform || '';
  const containerType  = container?.container_type || jobSnapshot?.source_type || '';
  const itemCount      = container?.item_count || jobSnapshot?.summary?.item_count || 0;
  const containerId    = container?.container_id || jobSnapshot?.container_id;

  // Phase 26-A: YouTube queue policy — downloads disabled until Oracle-IP issue resolved
  // Phase 26-B: cookie-gated platforms blocked when cookie unavailable/expired/blocked
  const cookieGateBlocked = ['cookie_unavailable', 'cookie_expired', 'cookie_blocked'].includes(
    capabilityGate?.required
  );
  const queueDisabledByPolicy = platform === 'youtube' || cookieGateBlocked;
  const queueTooltip = platform === 'youtube'
    ? t('queue.youtubeDisabled')
    : cookieGateBlocked
      ? t('queue.cookieInvalid')
      : '';

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="w-full sm:max-w-2xl max-h-[92vh] bg-canvas rounded-t-2xl sm:rounded-xl flex flex-col shadow-2xl border border-line overflow-hidden">

        {/* Header */}
        <div className="flex items-start gap-3 p-4 border-b border-line shrink-0">
          {container?.avatar && (
            <img src={container.avatar} alt="" className="w-10 h-10 rounded-full object-cover bg-surface-2 shrink-0" />
          )}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-base font-semibold text-fg truncate">
                {containerTitle || (isLoading ? t('status.discovering') : 'Container')}
              </h2>
              <span className={`text-xs px-1.5 py-0.5 rounded ${statusBadge.cls}`}>
                {statusBadge.label}
              </span>
            </div>
            <p className="text-xs text-fg-muted mt-0.5">
              {platform && <span className="capitalize">{platform}</span>}
              {containerType && <span> · {containerType}</span>}
              {itemCount > 0 && <span> · {t('itemCount', { count: itemCount })}</span>}
            </p>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            {onRefresh && !isLoading && (
              <button onClick={onRefresh} className="p-1.5 text-fg-muted hover:text-fg rounded-lg hover:bg-surface transition-colors" title="Refresh">
                <RefreshCw size={15} />
              </button>
            )}
            <button onClick={onClose} className="p-1.5 text-fg-muted hover:text-fg rounded-lg hover:bg-surface transition-colors">
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Progress bar */}
        {showProgress && (
          <div className="h-1 bg-surface shrink-0">
            <div
              className="h-full bg-accent transition-all duration-500"
              style={{ width: `${progress}%` }}
            />
          </div>
        )}

        {/* Loading spinner (before any sections) */}
        {isLoading && sections.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 gap-3">
            <Loader2 size={32} className="animate-spin text-fg-2" />
            <p className="text-sm text-fg-muted">
              {jobSnapshot?.message || t('scanning')}
            </p>
          </div>
        )}

        {/* Expired state */}
        {isExpired && !isLoading && (
          <ExpiredState onRefresh={onRefresh} />
        )}

        {/* Capability gating banner */}
        {capabilityGate && (
          <CapabilityGateBanner gate={capabilityGate} platform={platform} />
        )}

        {/* Error state */}
        {(error || isFailed) && !capabilityGate && !isExpired && (
          <div className="m-4 p-3 bg-danger-soft border border-danger/30 rounded-lg flex items-start gap-2">
            <AlertTriangle size={16} className="text-danger shrink-0 mt-0.5" />
            <div>
              <p className="text-sm text-danger">
                {container?.error_message || jobSnapshot?.error?.message || error || t('discoveryFailed')}
              </p>
              {onRefresh && (
                <button onClick={onRefresh} className="text-xs text-danger underline mt-1">{t('retry')}</button>
              )}
            </div>
          </div>
        )}

        {/* Partial results banner */}
        {isPartial && !isLoading && sections.length > 0 && (
          <div className="mx-4 mt-3 p-2.5 bg-accent-soft border border-accent/50 rounded-lg flex items-start gap-2">
            <Info size={13} className="text-accent-text shrink-0 mt-0.5" />
            <p className="text-xs text-accent-text">
              {t('partialBanner')}
            </p>
          </div>
        )}

        {/* Container warning */}
        {container?.warning && !isFailed && !isPartial && (
          <div className="mx-4 mt-3 mb-0 p-2.5 bg-accent-soft border border-accent/50 rounded-lg flex items-start gap-2">
            <Info size={13} className="text-accent-text shrink-0 mt-0.5" />
            <p className="text-xs text-warning">{container.warning}</p>
          </div>
        )}

        {/* Queue success banner */}
        {queueSuccess && (
          <QueueSuccessBanner
            summary={queueSuccess.summary}
            batchId={queueSuccess.batchId}
            onClose={() => setQueueSuccess(null)}
          />
        )}

        {/* Toolbar + item list (only when there's something to show) */}
        {!isExpired && !capabilityGate && sections.length > 0 && (
          <>
            {allItems.length > 0 && (
              <>
                <FilterToolbar filter={filter} onChange={setFilter} />
                <div className="flex flex-wrap items-center gap-2 px-4 py-2 border-b border-line shrink-0">
                  <button onClick={selectAll} className="text-xs px-2 py-1 bg-surface-2 hover:bg-line text-fg-2 rounded transition-colors">
                    {t('toolbar.selectAll', { count: visibleItems.length })}
                  </button>
                  <button onClick={() => selectLatestN(20)} className="text-xs px-2 py-1 bg-surface-2 hover:bg-line text-fg-2 rounded transition-colors">
                    {t('toolbar.latest', { count: 20 })}
                  </button>
                  <button onClick={() => selectLatestN(50)} className="text-xs px-2 py-1 bg-surface-2 hover:bg-line text-fg-2 rounded transition-colors">
                    {t('toolbar.latest', { count: 50 })}
                  </button>
                  <button onClick={clearAll} className="text-xs px-2 py-1 bg-surface-2 hover:bg-line text-fg-2 rounded transition-colors">
                    {t('toolbar.clear')}
                  </button>
                  <div className="flex-1" />
                  <select
                    value={quality}
                    onChange={e => setQuality(e.target.value)}
                    className="text-xs bg-surface border border-line rounded px-2 py-1 text-fg-2"
                  >
                    {QUALITY_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.labelKey ? t(o.labelKey) : o.label}</option>)}
                  </select>
                </div>
              </>
            )}

            <div className="flex-1 overflow-y-auto">
              {sections.map(section => (
                <SectionBlock
                  key={section.key}
                  section={section}
                  selected={selected}
                  onToggleItem={toggleItem}
                  onSelectSection={selectSection}
                  containerId={containerId}
                  onExpanded={handleExpanded}
                />
              ))}

              {!isLoading && (status === 'ready' || status === 'success') && allItems.length === 0 && (
                <div className="flex flex-col items-center justify-center py-12 gap-2 text-fg-muted">
                  <p className="text-sm">{t('noItems')}</p>
                </div>
              )}
            </div>

            {/* Footer */}
            <div className="border-t border-line px-4 py-3 flex items-center gap-3 shrink-0 bg-canvas">
              <button
                onClick={handleDownloadManifest}
                className="p-2 text-fg-muted hover:text-fg-2 rounded-lg hover:bg-surface transition-colors"
                title={t('exportCsv')}
              >
                <FileDown size={16} />
              </button>

              <div className="flex-1 text-sm text-fg-muted">
                {selected.size > 0 ? (
                  <span><Trans t={t} i18nKey="selectedCount" count={selected.size} components={{ b: <span className="text-fg font-medium" /> }} /></span>
                ) : (
                  <span className="text-fg-muted">{t('noneSelected')}</span>
                )}
              </div>

              {queueError && (
                <p className="text-xs text-danger shrink-0 max-w-[200px] truncate">{queueError}</p>
              )}

              <div className="relative group/queue">
                <button
                  onClick={handleQueue}
                  disabled={selected.size === 0 || isQueuing || !containerId || queueDisabledByPolicy}
                  className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                    selected.size > 0 && !isQueuing && !queueDisabledByPolicy
                      ? 'bg-accent hover:bg-accent-hover text-accent-fg'
                      : 'bg-surface-2 text-fg-muted cursor-not-allowed'
                  }`}
                >
                  {isQueuing ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
                  {isQueuing ? t('queue.queuing') : t('queue.button', { n: selected.size || '' })}
                </button>
                {queueDisabledByPolicy && (
                  <div className="absolute bottom-full right-0 mb-2 w-60 px-3 py-2 bg-surface border border-line rounded-lg text-xs text-fg-2 opacity-0 group-hover/queue:opacity-100 transition-opacity pointer-events-none z-10">
                    {queueTooltip}
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

import { useState, useEffect, useCallback, useRef } from 'react';
import { useAuth } from '../context/AuthContext';
import { useTranslation } from 'react-i18next';
import { useLang } from '../i18n';
import {
  Archive, Star, Trash2, Download, Search, Filter, X, Plus,
  Folder, Clock, RefreshCw, Pin, PinOff,
  MoreHorizontal, ExternalLink, FileJson, FileText, AlertCircle,
  CheckCircle, Lock,
} from 'lucide-react';

// ── Retention badge config ─────────────────────────────────────────
const RETENTION_BADGE = {
  temporary:            { labelKey: 'retention.temporary', cls: 'bg-success-soft text-success border-success/30' },
  pinned:               { labelKey: 'retention.pinned', cls: 'bg-accent-soft text-accent-text border-accent/30' },
  expired_metadata_only:{ labelKey: 'retention.expired', cls: 'bg-line text-fg-muted border-line-strong' },
};

function RetentionBadge({ status, pinExpiry }) {
  const { t } = useTranslation('archive');
  const lang = useLang();
  const cfg = RETENTION_BADGE[status] || RETENTION_BADGE.temporary;
  const expText = status === 'pinned' && pinExpiry
    ? t('retention.until', { date: new Date(pinExpiry).toLocaleDateString(lang === 'en' ? 'en-US' : 'vi-VN') })
    : '';
  return (
    <span className={`inline-flex items-center gap-1 text-[9px] font-bold px-1.5 py-0.5 rounded border ${cfg.cls}`}>
      {status === 'pinned' && <Pin className="w-2.5 h-2.5" />}
      {status === 'expired_metadata_only' && <AlertCircle className="w-2.5 h-2.5" />}
      {status === 'temporary' && <CheckCircle className="w-2.5 h-2.5" />}
      {t(cfg.labelKey)}{expText}
    </span>
  );
}

function DeleteModal({ item, onClose, onDeleted }) {
  const { t } = useTranslation('archive');
  const { session } = useAuth();
  const [mode, setMode]     = useState('archive_only');
  const [loading, setLoading] = useState(false);

  const MODES = [
    {
      id: 'file_only',
      title: t('delete.fileOnly.title'),
      desc: t('delete.fileOnly.desc'),
      danger: false,
    },
    {
      id: 'archive_only',
      title: t('delete.archiveOnly.title'),
      desc: t('delete.archiveOnly.desc'),
      danger: false,
    },
    {
      id: 'everything',
      title: t('delete.everything.title'),
      desc: t('delete.everything.desc'),
      danger: true,
    },
  ];

  async function handleDelete() {
    setLoading(true);
    try {
      const r = await fetch(`${API}/api/v1/archive/${item.id}?mode=${mode}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${session?.access_token}` },
      });
      if (r.ok || r.status === 204) {
        onDeleted(item.id, mode);
        onClose();
      }
    } finally { setLoading(false); }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-surface border border-line rounded-2xl w-full max-w-md p-5" onClick={e => e.stopPropagation()}>
        <h3 className="font-bold text-fg mb-1">{t('delete.title', { title: item.title?.slice(0,40) })}</h3>
        <p className="text-xs text-fg-muted mb-4">{t('delete.scope')}</p>
        <div className="space-y-2 mb-5">
          {MODES.map(m => (
            <label key={m.id} className={`flex gap-3 p-3 rounded-xl border cursor-pointer transition-all ${mode === m.id ? (m.danger ? 'border-danger/50 bg-danger-soft' : 'border-accent/40 bg-accent-soft') : 'border-line hover:border-line-strong'}`}>
              <input type="radio" name="delete-mode" value={m.id} checked={mode === m.id} onChange={() => setMode(m.id)} className="mt-0.5 accent-accent" />
              <div>
                <p className={`text-sm font-medium ${m.danger ? 'text-danger' : 'text-fg'}`}>{m.title}</p>
                <p className="text-xs text-fg-muted mt-0.5">{m.desc}</p>
              </div>
            </label>
          ))}
        </div>
        <div className="flex gap-2">
          <button onClick={onClose} className="flex-1 py-2 rounded-xl border border-line text-sm text-fg-muted hover:text-fg cursor-pointer">{t('delete.cancel')}</button>
          <button onClick={handleDelete} disabled={loading}
            className={`flex-1 py-2 rounded-xl text-sm font-bold cursor-pointer disabled:opacity-50 transition-colors ${mode === 'everything' ? 'bg-danger hover:opacity-90 text-danger-fg' : 'bg-accent text-danger-fg'}`}>
            {loading ? t('delete.deleting') : t('delete.confirm')}
          </button>
        </div>
      </div>
    </div>
  );
}

const API = import.meta.env.VITE_API_URL || '';

const PLATFORM_COLORS = {
  youtube:   'bg-danger-soft text-danger border-danger/30',
  tiktok:    'bg-surface-2 text-fg-2 border-line',
  facebook:  'bg-surface-2 text-fg-2 border-line',
  instagram: 'bg-surface-2 text-fg-2 border-line',
  twitter:   'bg-surface-2 text-fg-2 border-line',
  reddit:    'bg-accent-soft text-accent-text border-accent/30',
  other:     'bg-line-strong text-fg-muted border-line-strong',
};

function fmtDuration(s) {
  if (!s) return null;
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
  return `${m}:${String(sec).padStart(2,'0')}`;
}

function fmtDate(iso, lang) {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString(lang === 'en' ? 'en-US' : 'vi-VN', { day:'2-digit', month:'2-digit', year:'numeric' });
}

function CollectionModal({ onClose, onCreated }) {
  const { t } = useTranslation('archive');
  const { session } = useAuth();
  const [name, setName] = useState('');
  const [color, setColor] = useState('var(--vg-accent)');
  const [icon, setIcon] = useState('📁');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const EMOJIS = ['📁','🎬','🎵','🏆','❤️','⭐','🔥','📚','🎭','🌍'];
  const COLORS = ['var(--vg-accent)','var(--vg-success)','var(--vg-accent)','var(--vg-danger)','var(--vg-accent)','var(--vg-accent)','var(--vg-accent)','var(--vg-success)'];

  async function handleCreate() {
    if (!name.trim()) return setError(t('collection.emptyName'));
    setLoading(true); setError('');
    try {
      const r = await fetch(`${API}/api/v1/archive/collections`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${session?.access_token}` },
        body: JSON.stringify({ name: name.trim(), color, icon }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail?.message || d.detail || t('collection.errorFallback'));
      onCreated(d);
      onClose();
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-surface border border-line rounded-2xl w-full max-w-md p-6" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-5">
          <h3 className="text-lg font-bold text-fg">{t('collection.title')}</h3>
          <button onClick={onClose} className="text-fg-muted hover:text-fg cursor-pointer"><X className="w-5 h-5" /></button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="text-xs text-fg-muted mb-1 block">{t('collection.nameLabel')}</label>
            <input
              value={name} onChange={e => setName(e.target.value)}
              placeholder={t('collection.namePlaceholder')}
              className="w-full bg-surface border border-line rounded-lg px-3 py-2 text-sm text-fg focus:outline-none focus:border-accent/50"
            />
          </div>

          <div>
            <label className="text-xs text-fg-muted mb-2 block">{t('collection.iconLabel')}</label>
            <div className="flex gap-2 flex-wrap">
              {EMOJIS.map(e => (
                <button key={e} onClick={() => setIcon(e)}
                  className={`w-9 h-9 rounded-lg text-lg flex items-center justify-center cursor-pointer transition-all ${icon === e ? 'bg-accent-soft ring-1 ring-accent' : 'bg-surface hover:bg-surface-2'}`}
                >{e}</button>
              ))}
            </div>
          </div>

          <div>
            <label className="text-xs text-fg-muted mb-2 block">{t('collection.colorLabel')}</label>
            <div className="flex gap-2">
              {COLORS.map(c => (
                <button key={c} onClick={() => setColor(c)}
                  style={{ backgroundColor: c }}
                  className={`w-7 h-7 rounded-full cursor-pointer transition-all ${color === c ? 'ring-2 ring-line-strong ring-offset-2 ring-offset-surface' : 'opacity-70 hover:opacity-100'}`}
                />
              ))}
            </div>
          </div>

          {error && <p className="text-danger text-xs">{error}</p>}

          <button onClick={handleCreate} disabled={loading}
            className="w-full py-2 rounded-xl bg-accent text-accent-fg font-bold text-sm hover:opacity-90 transition disabled:opacity-50 cursor-pointer">
            {loading ? t('collection.creating') : t('collection.create')}
          </button>
        </div>
      </div>
    </div>
  );
}

function ArchiveCard({ item, collections, onStar, onDelete, onRedownload, onTagFilter, onAddToCollection, onPin, onUnpin }) {
  const { t } = useTranslation('archive');
  const lang = useLang();
  const [menu, setMenu] = useState(false);
  const platform = item.platform || 'other';
  const badgeCls = PLATFORM_COLORS[platform] || PLATFORM_COLORS.other;
  const duration = fmtDuration(item.duration_seconds);
  const myCollections = (collections || []).filter(c => (item.collection_ids || []).includes(c.id));
  const retStatus = item.file_retention_status || 'temporary';
  const isExpired = retStatus === 'expired_metadata_only';

  return (
    <div className="bg-surface border border-line rounded-xl overflow-hidden group hover:border-line-strong transition-all">
      {/* Thumbnail */}
      <div className="relative aspect-video bg-surface overflow-hidden">
        {item.thumbnail_url ? (
          <img src={item.thumbnail_url} alt={item.title} className="w-full h-full object-cover" loading="lazy" />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <Archive className="w-8 h-8 text-fg-muted" />
          </div>
        )}
        {duration && (
          <span className="absolute bottom-1.5 right-1.5 bg-black/80 text-white text-[10px] font-mono px-1.5 py-0.5 rounded">
            {duration}
          </span>
        )}
        <span className={`absolute top-1.5 left-1.5 text-[10px] font-bold px-1.5 py-0.5 rounded border ${badgeCls}`}>
          {platform.toUpperCase()}
        </span>
        {/* Expired overlay */}
        {isExpired && (
          <div className="absolute inset-0 bg-black/50 flex items-center justify-center">
            <span className="text-[10px] font-bold text-fg-muted bg-black/70 px-2 py-1 rounded">{t('retention.expired')}</span>
          </div>
        )}
      </div>

      {/* Content */}
      <div className="p-3 space-y-2">
        <div className="flex items-start justify-between gap-2">
          <p className="text-sm font-medium text-fg leading-snug line-clamp-2 flex-1">{item.title}</p>
          <div className="flex items-center gap-1 shrink-0">
            <button onClick={() => onStar(item.id, !item.is_starred)}
              className={`p-1 rounded cursor-pointer transition-colors ${item.is_starred ? 'text-accent-text' : 'text-fg-muted hover:text-accent-text'}`}>
              <Star className="w-4 h-4" fill={item.is_starred ? 'currentColor' : 'none'} />
            </button>
            <div className="relative">
              <button onClick={() => setMenu(m => !m)}
                className="p-1 text-fg-muted hover:text-fg-2 cursor-pointer"><MoreHorizontal className="w-4 h-4" /></button>
              {menu && (
                <div className="absolute right-0 top-6 z-20 bg-canvas border border-line rounded-lg py-1 w-48 shadow-xl">
                  <button onClick={() => { onRedownload(item.id); setMenu(false); }}
                    className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-fg-2 hover:bg-surface-2 cursor-pointer">
                    <Download className="w-3.5 h-3.5" /> {isExpired ? t('card.redownloadRehydrate') : t('card.redownload')}
                  </button>
                  <button onClick={() => { onAddToCollection(item.id); setMenu(false); }}
                    className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-fg-2 hover:bg-surface-2 cursor-pointer">
                    <Folder className="w-3.5 h-3.5" /> {t('card.addToCollection')}
                  </button>
                  {!isExpired && retStatus !== 'pinned' && (
                    <button onClick={() => { onPin(item.file_job_id); setMenu(false); }}
                      className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-accent-text hover:bg-accent-soft cursor-pointer">
                      <Pin className="w-3.5 h-3.5" /> {t('card.pin')}
                    </button>
                  )}
                  {retStatus === 'pinned' && (
                    <button onClick={() => { onUnpin(item.file_job_id); setMenu(false); }}
                      className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-fg-2 hover:bg-surface-2 cursor-pointer">
                      <PinOff className="w-3.5 h-3.5" /> {t('card.unpin')}
                    </button>
                  )}
                  <a href={item.original_url} target="_blank" rel="noreferrer"
                    className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-fg-2 hover:bg-surface-2 cursor-pointer">
                    <ExternalLink className="w-3.5 h-3.5" /> {t('card.openOriginal')}
                  </a>
                  <hr className="border-line my-1" />
                  <button onClick={() => { onDelete(item); setMenu(false); }}
                    className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-danger hover:bg-danger-soft cursor-pointer">
                    <Trash2 className="w-3.5 h-3.5" /> {t('card.delete')}
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>

        {item.creator_name && (
          <p className="text-xs text-fg-muted truncate">
            {item.creator_handle ? `@${item.creator_handle}` : item.creator_name}
          </p>
        )}

        {/* Hashtags */}
        {(item.hashtags || []).length > 0 && (
          <div className="flex gap-1 flex-wrap">
            {item.hashtags.slice(0,3).map(tag => (
              <button key={tag} onClick={() => onTagFilter(tag)}
                className="text-[10px] px-1.5 py-0.5 rounded bg-surface-2 text-fg-muted hover:bg-line hover:text-fg-2 cursor-pointer transition-colors">
                #{tag}
              </button>
            ))}
          </div>
        )}

        {/* User tags */}
        {(item.tags_user || []).length > 0 && (
          <div className="flex gap-1 flex-wrap">
            {item.tags_user.slice(0,3).map(tag => (
              <span key={tag} className="text-[10px] px-1.5 py-0.5 rounded bg-accent-soft text-accent-text border border-accent/20">{tag}</span>
            ))}
          </div>
        )}

        {/* Collections */}
        {myCollections.length > 0 && (
          <div className="flex gap-1 flex-wrap">
            {myCollections.map(c => (
              <span key={c.id} style={{ color: c.color || 'var(--vg-fg-muted)', borderColor: (c.color || 'var(--vg-fg-muted)') + '40' }}
                className="text-[10px] px-1.5 py-0.5 rounded border">
                {c.icon || '📁'} {c.name}
              </span>
            ))}
          </div>
        )}

        <div className="flex items-center justify-between">
          <p className="text-[10px] text-fg-muted">{fmtDate(item.archived_at, lang)}</p>
          <RetentionBadge status={retStatus} pinExpiry={item.pin_expires_at} />
        </div>

        {/* Re-download CTA for expired files */}
        {isExpired && (
          <button onClick={() => onRedownload(item.id)}
            className="w-full py-1.5 rounded-lg border border-accent/30 text-accent-text text-xs font-bold hover:bg-accent-soft cursor-pointer transition-colors flex items-center justify-center gap-1.5">
            <RefreshCw className="w-3 h-3" /> {t('card.redownloadCta')}
          </button>
        )}
      </div>
    </div>
  );
}

export default function ArchivePage({ onNavigate }) {
  const { t } = useTranslation('archive');
  const { session } = useAuth();
  const [items, setItems]           = useState([]);
  const [total, setTotal]           = useState(0);
  const [page, setPage]             = useState(1);
  const [loading, setLoading]       = useState(false);
  const [collections, setCollections] = useState([]);
  const [activeView, setActiveView] = useState('all');
  const [searchQ, setSearchQ]       = useState('');
  const [platform, setPlatform]     = useState('');
  const [sortBy, setSortBy]         = useState('archived_at');
  const [fileStatus, setFileStatus] = useState('');   // '' | available | pinned | expired
  const [showFilters, setShowFilters] = useState(false);
  const [showCollModal, setShowCollModal] = useState(false);
  const [addToColl, setAddToColl]   = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null); // item object for delete modal
  const [redownloadMsg, setRedownloadMsg] = useState('');
  const [pinMsg, setPinMsg]         = useState('');
  // List could not be loaded (offline / server error): say so instead of an empty archive.
  const [loadError, setLoadError]   = useState(false);
  const searchTimer = useRef(null);

  const headers = { Authorization: `Bearer ${session?.access_token}` };

  async function fetchCollections() {
    try {
      const r = await fetch(`${API}/api/v1/archive/collections`, { headers });
      if (r.ok) { const d = await r.json(); setCollections(d.collections || []); }
    } catch { /* sidebar only; the list banner reports the outage */ }
  }

  // Network failure in an action: tell the user instead of an uncaught error.
  function networkFailed() {
    setPinMsg(t('page.networkError'));
    setTimeout(() => setPinMsg(''), 5000);
  }

  const fetchItems = useCallback(async (p = 1) => {
    setLoading(true);
    try {
      if (searchQ.length >= 2) {
        const r = await fetch(`${API}/api/v1/archive/search?q=${encodeURIComponent(searchQ)}&limit=40`, { headers });
        if (r.ok) { const d = await r.json(); setItems(d.items); setTotal(d.total); }
        setLoadError(!r.ok);
        return;
      }

      const params = new URLSearchParams({ page: p, limit: 24, sort: sortBy });
      if (platform) params.set('platform', platform);
      if (fileStatus) params.set('file_status', fileStatus);
      if (activeView === 'starred') params.set('is_starred', 'true');
      if (activeView === 'today') {
        const today = new Date(); today.setHours(0,0,0,0);
        params.set('date_from', today.toISOString());
      }
      if (activeView && !['all','starred','today'].includes(activeView)) {
        params.set('collection_id', activeView);
      }

      const r = await fetch(`${API}/api/v1/archive?${params}`, { headers });
      if (r.ok) {
        const d = await r.json();
        setItems(p === 1 ? d.items : prev => [...prev, ...d.items]);
        setTotal(d.total);
        setPage(p);
      }
      setLoadError(!r.ok);
    } catch {
      setLoadError(true);
    } finally { setLoading(false); }
  }, [searchQ, platform, sortBy, activeView]);

  useEffect(() => { fetchCollections(); }, []);
  useEffect(() => {
    clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => { fetchItems(1); }, 300);
  }, [fetchItems, fileStatus]);

  async function handleStar(id, starred) {
    try {
      await fetch(`${API}/api/v1/archive/${id}`, {
        method: 'PATCH',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_starred: starred }),
      });
      setItems(prev => prev.map(i => i.id === id ? { ...i, is_starred: starred } : i));
    } catch { networkFailed(); }
  }

  function handleDelete(item) {
    setDeleteTarget(item);
  }

  function handleDeleted(id, mode) {
    if (mode === 'file_only') {
      // Keep in list but update retention status
      setItems(prev => prev.map(i => i.id === id
        ? { ...i, file_retention_status: 'expired_metadata_only' }
        : i
      ));
    } else {
      setItems(prev => prev.filter(i => i.id !== id));
      setTotal(n => n - 1);
    }
  }

  async function handlePin(jobId) {
    if (!jobId) return;
    try {
      const r = await fetch(`${API}/api/v1/storage/pin/${jobId}`, { method: 'POST', headers });
      const d = await r.json();
      if (r.ok) {
        setPinMsg(d.message || t('page.pinned'));
        setTimeout(() => setPinMsg(''), 4000);
        fetchItems(1);
      } else {
        setPinMsg(d.detail?.message || d.detail || t('page.pinFailed'));
        setTimeout(() => setPinMsg(''), 5000);
      }
    } catch { networkFailed(); }
  }

  async function handleUnpin(jobId) {
    if (!jobId) return;
    try {
      const r = await fetch(`${API}/api/v1/storage/pin/${jobId}`, { method: 'DELETE', headers });
      const d = await r.json();
      setPinMsg(d.message || t('page.unpinned'));
      setTimeout(() => setPinMsg(''), 4000);
      fetchItems(1);
    } catch { networkFailed(); }
  }

  async function handleRedownload(id) {
    try {
      const r = await fetch(`${API}/api/v1/archive/${id}/re-download`, { method: 'POST', headers });
      const d = await r.json();
      setRedownloadMsg(d.message || t('page.redownloadCreated'));
      setTimeout(() => setRedownloadMsg(''), 4000);
    } catch { networkFailed(); }
  }

  async function handleAddToCollection(itemId, collId) {
    try {
      await fetch(`${API}/api/v1/archive/collections/${collId}/add?item_id=${itemId}`, {
        method: 'POST', headers,
      });
      fetchItems(1);
      setAddToColl(null);
    } catch { networkFailed(); }
  }

  async function handleDeleteCollection(collId) {
    if (!confirm(t('page.confirmDeleteCollection'))) return;
    try {
      await fetch(`${API}/api/v1/archive/collections/${collId}`, { method: 'DELETE', headers });
      fetchCollections();
      if (activeView === collId) setActiveView('all');
    } catch { networkFailed(); }
  }

  async function handleExport(format) {
    try {
      const r = await fetch(`${API}/api/v1/archive/export?format=${format}`, { headers });
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `vidgrab-archive.${format}`; a.click();
      URL.revokeObjectURL(url);
    } catch { networkFailed(); }
  }

  const LIMIT = 24;
  const hasMore = items.length < total;

  const activeColl = collections.find(c => c.id === activeView);
  const viewLabel = activeView === 'all' ? t('page.viewAllCount', { total })
    : activeView === 'starred' ? t('page.viewStarred')
    : activeView === 'today' ? t('page.viewToday')
    : activeColl ? `${activeColl.icon || '📁'} ${activeColl.name}` : t('page.viewArchive');

  return (
    <div className="flex min-h-[calc(100vh-4rem)] bg-canvas">
      {/* ── Sidebar ──────────────────────────────────────── */}
      <aside className="w-56 shrink-0 border-r border-line p-4 flex flex-col gap-1">
        <p className="text-[10px] font-bold text-fg-muted uppercase tracking-wider mb-2">{t('page.views')}</p>

        {[
          { id: 'all',     label: t('page.sideAll'), icon: <Archive className="w-4 h-4" /> },
          { id: 'starred', label: t('page.sideStarred'), icon: <Star className="w-4 h-4" /> },
          { id: 'today',   label: t('page.sideToday'), icon: <Clock className="w-4 h-4" /> },
        ].map(v => (
          <button key={v.id} onClick={() => { setActiveView(v.id); setPage(1); }}
            className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm cursor-pointer transition-colors text-left ${activeView === v.id ? 'bg-accent-soft text-accent-text' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}`}>
            {v.icon} {v.label}
          </button>
        ))}

        <div className="mt-4 flex items-center justify-between">
          <p className="text-[10px] font-bold text-fg-muted uppercase tracking-wider">{t('page.collections')}</p>
          <button onClick={() => setShowCollModal(true)}
            className="text-fg-muted hover:text-accent-text cursor-pointer transition-colors">
            <Plus className="w-3.5 h-3.5" />
          </button>
        </div>

        {collections.length === 0 && (
          <p className="text-xs text-fg-muted px-3 mt-1">{t('page.noCollections')}</p>
        )}

        {collections.map(c => (
          <button key={c.id} onClick={() => { setActiveView(c.id); setPage(1); }}
            className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm cursor-pointer transition-colors text-left group ${activeView === c.id ? 'bg-surface-2 text-fg' : 'text-fg-muted hover:bg-line hover:text-fg'}`}>
            <span style={{ color: c.color || 'var(--vg-fg-muted)' }}>{c.icon || '📁'}</span>
            <span className="flex-1 truncate">{c.name}</span>
            <span className="text-[10px] text-fg-muted">{c.item_count || 0}</span>
            <button onClick={e => { e.stopPropagation(); handleDeleteCollection(c.id); }}
              className="hidden group-hover:flex text-fg-muted hover:text-danger cursor-pointer">
              <X className="w-3 h-3" />
            </button>
          </button>
        ))}

        {/* Export */}
        <div className="mt-auto pt-4 border-t border-line space-y-1">
          <p className="text-[10px] font-bold text-fg-muted uppercase tracking-wider mb-2">{t('page.export')}</p>
          <button onClick={() => handleExport('json')}
            className="flex items-center gap-2 w-full px-3 py-2 rounded-lg text-xs text-fg-muted hover:bg-surface-2 hover:text-fg cursor-pointer">
            <FileJson className="w-4 h-4" /> JSON
          </button>
          <button onClick={() => handleExport('csv')}
            className="flex items-center gap-2 w-full px-3 py-2 rounded-lg text-xs text-fg-muted hover:bg-surface-2 hover:text-fg cursor-pointer">
            <FileText className="w-4 h-4" /> CSV
          </button>
        </div>
      </aside>

      {/* ── Main Area ─────────────────────────────────────── */}
      <main className="flex-1 p-6 overflow-y-auto">
        {/* Header */}
        <div className="flex flex-col sm:flex-row gap-3 mb-5">
          <div className="flex-1">
            <h2 className="text-xl font-bold text-fg mb-1">{viewLabel}</h2>
          </div>

          {/* Search */}
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-fg-muted" />
            <input
              value={searchQ} onChange={e => setSearchQ(e.target.value)}
              placeholder={t('page.searchPlaceholder')}
              className="w-64 bg-surface border border-line rounded-lg pl-9 pr-3 py-2 text-sm text-fg focus:outline-none focus:border-accent/50"
            />
            {searchQ && <button onClick={() => setSearchQ('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-fg-muted hover:text-fg cursor-pointer"><X className="w-3.5 h-3.5" /></button>}
          </div>

          <button onClick={() => setShowFilters(f => !f)}
            className={`flex items-center gap-1.5 px-3 py-2 rounded-lg border text-sm cursor-pointer transition-colors ${showFilters ? 'border-accent/50 text-accent-text bg-accent-soft' : 'border-line text-fg-muted hover:text-fg'}`}>
            <Filter className="w-4 h-4" /> {t('page.filter')}
          </button>
        </div>

        {/* File status pills — always visible */}
        <div className="flex gap-2 mb-3 flex-wrap">
          {[
            { val: '',          label: t('page.fileAll') },
            { val: 'available', label: t('page.fileAvailable') },
            { val: 'pinned',    label: t('page.filePinned') },
            { val: 'expired',   label: t('page.fileExpired') },
          ].map(opt => (
            <button key={opt.val} onClick={() => { setFileStatus(opt.val); setPage(1); }}
              className={`px-3 py-1 rounded-full text-xs font-medium cursor-pointer transition-colors border ${fileStatus === opt.val ? 'bg-accent-soft border-accent/50 text-accent-text' : 'border-line text-fg-muted hover:text-fg hover:border-line-strong'}`}>
              {opt.label}
            </button>
          ))}
        </div>

        {/* Filter bar */}
        {showFilters && (
          <div className="flex gap-3 mb-4 flex-wrap">
            <select value={platform} onChange={e => { setPlatform(e.target.value); setPage(1); }}
              className="bg-surface border border-line rounded-lg px-3 py-2 text-sm text-fg-2 focus:outline-none cursor-pointer">
              <option value="">{t('page.allPlatforms')}</option>
              {['youtube','tiktok','facebook','instagram','twitter','reddit'].map(p => (
                <option key={p} value={p}>{p.charAt(0).toUpperCase()+p.slice(1)}</option>
              ))}
            </select>
            <select value={sortBy} onChange={e => { setSortBy(e.target.value); setPage(1); }}
              className="bg-surface border border-line rounded-lg px-3 py-2 text-sm text-fg-2 focus:outline-none cursor-pointer">
              <option value="archived_at">{t('page.sortRecent')}</option>
              <option value="upload_date">{t('page.sortUpload')}</option>
              <option value="duration">{t('page.sortDuration')}</option>
              <option value="most_viewed">{t('page.sortViews')}</option>
              <option value="title">{t('page.sortTitle')}</option>
            </select>
          </div>
        )}

        {redownloadMsg && (
          <div className="mb-4 px-4 py-2 bg-success-soft border border-success/30 rounded-lg text-success text-sm">
            {redownloadMsg}
          </div>
        )}
        {pinMsg && (
          <div className="mb-4 px-4 py-2 bg-accent-soft border border-accent/30 rounded-lg text-accent-text text-sm">
            {pinMsg}
          </div>
        )}

        {loadError && (
          <div role="alert" className="mb-4 flex items-center justify-between gap-3 px-4 py-3 bg-danger-soft border border-danger/40 rounded-lg text-danger text-sm">
            <span>{t('page.loadError')}</span>
            <button onClick={() => fetchItems(1)} className="shrink-0 px-3 py-1 rounded-lg border border-danger/40 hover:bg-danger/10 transition cursor-pointer">{t('page.retry')}</button>
          </div>
        )}

        {/* Grid */}
        {loading && items.length === 0 ? (
          <div className="flex items-center justify-center h-48 text-fg-muted text-sm">{t('page.loading')}</div>
        ) : items.length === 0 && !loadError ? (
          <div className="flex flex-col items-center justify-center h-48 gap-3 text-fg-muted">
            <Archive className="w-12 h-12 opacity-30" />
            <p className="text-sm">{t('page.empty')}</p>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5 gap-4">
              {items.map(item => (
                <ArchiveCard
                  key={item.id}
                  item={item}
                  collections={collections}
                  onStar={handleStar}
                  onDelete={handleDelete}
                  onRedownload={handleRedownload}
                  onTagFilter={tag => setSearchQ(tag)}
                  onAddToCollection={id => setAddToColl(id)}
                  onPin={handlePin}
                  onUnpin={handleUnpin}
                />
              ))}
            </div>

            {hasMore && (
              <div className="mt-6 flex justify-center">
                <button onClick={() => fetchItems(page + 1)} disabled={loading}
                  className="px-6 py-2 rounded-xl border border-line text-sm text-fg-muted hover:text-fg hover:border-line-strong transition cursor-pointer disabled:opacity-50">
                  {loading ? t('page.loading') : t('page.loadMore', { remaining: total - items.length })}
                </button>
              </div>
            )}
          </>
        )}
      </main>

      {/* ── Add to Collection modal ──────────────────────── */}
      {addToColl && (
        <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={() => setAddToColl(null)}>
          <div className="bg-surface border border-line rounded-2xl w-full max-w-sm p-5" onClick={e => e.stopPropagation()}>
            <h3 className="font-bold text-fg mb-3">{t('page.pickCollection')}</h3>
            {collections.length === 0 ? (
              <p className="text-sm text-fg-muted">{t('page.noCollectionsCreateFirst')}</p>
            ) : (
              <div className="space-y-1">
                {collections.map(c => (
                  <button key={c.id} onClick={() => handleAddToCollection(addToColl, c.id)}
                    className="flex items-center gap-3 w-full px-3 py-2 rounded-lg hover:bg-surface-2 cursor-pointer text-sm text-fg-2 text-left">
                    <span style={{ color: c.color || 'var(--vg-fg-muted)' }}>{c.icon || '📁'}</span>
                    {c.name}
                    <span className="ml-auto text-xs text-fg-muted">{t('page.itemCount', { count: c.item_count || 0 })}</span>
                  </button>
                ))}
              </div>
            )}
            <button onClick={() => setAddToColl(null)} className="mt-3 text-xs text-fg-muted hover:text-fg-2 cursor-pointer">{t('delete.cancel')}</button>
          </div>
        </div>
      )}

      {/* ── Delete modal ─────────────────────────────────── */}
      {deleteTarget && (
        <DeleteModal
          item={deleteTarget}
          onClose={() => setDeleteTarget(null)}
          onDeleted={handleDeleted}
        />
      )}

      {/* ── Create Collection modal ───────────────────────── */}
      {showCollModal && (
        <CollectionModal
          onClose={() => setShowCollModal(false)}
          onCreated={c => { setCollections(prev => [...prev, c]); }}
        />
      )}
    </div>
  );
}

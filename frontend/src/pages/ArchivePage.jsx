import { useState, useEffect, useCallback, useRef } from 'react';
import { useAuth } from '../context/AuthContext';
import {
  Archive, Star, Trash2, Download, Search, Filter, X, Plus,
  Folder, Clock, RefreshCw, Pin, PinOff,
  MoreHorizontal, ExternalLink, FileJson, FileText, AlertCircle,
  CheckCircle, Lock,
} from 'lucide-react';

// ── Retention badge config ─────────────────────────────────────────
const RETENTION_BADGE = {
  temporary:            { label: 'Khả dụng', cls: 'bg-green-500/20 text-green-400 border-green-500/30' },
  pinned:               { label: 'Đã ghim', cls: 'bg-[#FBBF24]/20 text-[#FBBF24] border-[#FBBF24]/30' },
  expired_metadata_only:{ label: 'File hết hạn', cls: 'bg-slate-600/40 text-slate-500 border-slate-600/30' },
};

function RetentionBadge({ status, pinExpiry }) {
  const cfg = RETENTION_BADGE[status] || RETENTION_BADGE.temporary;
  const expText = status === 'pinned' && pinExpiry
    ? ` đến ${new Date(pinExpiry).toLocaleDateString('vi-VN')}`
    : '';
  return (
    <span className={`inline-flex items-center gap-1 text-[9px] font-bold px-1.5 py-0.5 rounded border ${cfg.cls}`}>
      {status === 'pinned' && <Pin className="w-2.5 h-2.5" />}
      {status === 'expired_metadata_only' && <AlertCircle className="w-2.5 h-2.5" />}
      {status === 'temporary' && <CheckCircle className="w-2.5 h-2.5" />}
      {cfg.label}{expText}
    </span>
  );
}

function DeleteModal({ item, onClose, onDeleted }) {
  const { session } = useAuth();
  const [mode, setMode]     = useState('archive_only');
  const [loading, setLoading] = useState(false);

  const MODES = [
    {
      id: 'file_only',
      title: 'Xoá file khỏi máy chủ',
      desc: 'Giữ lại mục archive, ghi chú, và collection. Bạn vẫn có thể tải lại sau.',
      danger: false,
    },
    {
      id: 'archive_only',
      title: 'Xoá khỏi archive',
      desc: 'Xoá mục khỏi thư viện. File trên server (nếu còn) sẽ tự hết hạn theo lịch thông thường.',
      danger: false,
    },
    {
      id: 'everything',
      title: 'Xoá toàn bộ',
      desc: 'Xoá mục archive, ghi chú, collection mapping, và xoá file khỏi disk ngay lập tức.',
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
      <div className="bg-[#0d2320] border border-slate-700/50 rounded-2xl w-full max-w-md p-5" onClick={e => e.stopPropagation()}>
        <h3 className="font-bold text-white mb-1">Xoá "{item.title?.slice(0,40)}"</h3>
        <p className="text-xs text-slate-400 mb-4">Chọn phạm vi xoá:</p>
        <div className="space-y-2 mb-5">
          {MODES.map(m => (
            <label key={m.id} className={`flex gap-3 p-3 rounded-xl border cursor-pointer transition-all ${mode === m.id ? (m.danger ? 'border-red-500/50 bg-red-900/20' : 'border-[#FBBF24]/40 bg-[#FBBF24]/5') : 'border-slate-700/30 hover:border-slate-600/50'}`}>
              <input type="radio" name="delete-mode" value={m.id} checked={mode === m.id} onChange={() => setMode(m.id)} className="mt-0.5 accent-[#FBBF24]" />
              <div>
                <p className={`text-sm font-medium ${m.danger ? 'text-red-400' : 'text-white'}`}>{m.title}</p>
                <p className="text-xs text-slate-400 mt-0.5">{m.desc}</p>
              </div>
            </label>
          ))}
        </div>
        <div className="flex gap-2">
          <button onClick={onClose} className="flex-1 py-2 rounded-xl border border-slate-700/50 text-sm text-slate-400 hover:text-white cursor-pointer">Huỷ</button>
          <button onClick={handleDelete} disabled={loading}
            className={`flex-1 py-2 rounded-xl text-sm font-bold cursor-pointer disabled:opacity-50 transition-colors ${mode === 'everything' ? 'bg-red-600 hover:bg-red-700 text-white' : 'bg-gradient-to-r from-[#FBBF24] to-[#FB923C] text-[#012622]'}`}>
            {loading ? 'Đang xoá...' : 'Xác nhận xoá'}
          </button>
        </div>
      </div>
    </div>
  );
}

const API = import.meta.env.VITE_API_URL || '';

const PLATFORM_COLORS = {
  youtube:   'bg-red-500/20 text-red-400 border-red-500/30',
  tiktok:    'bg-pink-500/20 text-pink-400 border-pink-500/30',
  facebook:  'bg-blue-500/20 text-blue-400 border-blue-500/30',
  instagram: 'bg-purple-500/20 text-purple-400 border-purple-500/30',
  twitter:   'bg-sky-500/20 text-sky-400 border-sky-500/30',
  reddit:    'bg-orange-500/20 text-orange-400 border-orange-500/30',
  other:     'bg-slate-500/20 text-slate-400 border-slate-500/30',
};

function fmtDuration(s) {
  if (!s) return null;
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
  return `${m}:${String(sec).padStart(2,'0')}`;
}

function fmtDate(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString('vi-VN', { day:'2-digit', month:'2-digit', year:'numeric' });
}

function CollectionModal({ onClose, onCreated }) {
  const { session } = useAuth();
  const [name, setName] = useState('');
  const [color, setColor] = useState('#FBBF24');
  const [icon, setIcon] = useState('📁');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const EMOJIS = ['📁','🎬','🎵','🏆','❤️','⭐','🔥','📚','🎭','🌍'];
  const COLORS = ['#FBBF24','#34D399','#60A5FA','#F87171','#A78BFA','#FB923C','#38BDF8','#4ADE80'];

  async function handleCreate() {
    if (!name.trim()) return setError('Tên collection không được để trống.');
    setLoading(true); setError('');
    try {
      const r = await fetch(`${API}/api/v1/archive/collections`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${session?.access_token}` },
        body: JSON.stringify({ name: name.trim(), color, icon }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail?.message || d.detail || 'Lỗi');
      onCreated(d);
      onClose();
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-[#0d2320] border border-slate-700/50 rounded-2xl w-full max-w-md p-6" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-5">
          <h3 className="text-lg font-bold text-white">Tạo Collection Mới</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-white cursor-pointer"><X className="w-5 h-5" /></button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="text-xs text-slate-400 mb-1 block">Tên Collection</label>
            <input
              value={name} onChange={e => setName(e.target.value)}
              placeholder="VD: Tutorial Hay, Nhạc Chill..."
              className="w-full bg-slate-800/50 border border-slate-700/50 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-[#FBBF24]/50"
            />
          </div>

          <div>
            <label className="text-xs text-slate-400 mb-2 block">Icon</label>
            <div className="flex gap-2 flex-wrap">
              {EMOJIS.map(e => (
                <button key={e} onClick={() => setIcon(e)}
                  className={`w-9 h-9 rounded-lg text-lg flex items-center justify-center cursor-pointer transition-all ${icon === e ? 'bg-[#FBBF24]/20 ring-1 ring-[#FBBF24]' : 'bg-slate-800/50 hover:bg-slate-700/50'}`}
                >{e}</button>
              ))}
            </div>
          </div>

          <div>
            <label className="text-xs text-slate-400 mb-2 block">Màu</label>
            <div className="flex gap-2">
              {COLORS.map(c => (
                <button key={c} onClick={() => setColor(c)}
                  style={{ backgroundColor: c }}
                  className={`w-7 h-7 rounded-full cursor-pointer transition-all ${color === c ? 'ring-2 ring-white ring-offset-2 ring-offset-[#0d2320]' : 'opacity-70 hover:opacity-100'}`}
                />
              ))}
            </div>
          </div>

          {error && <p className="text-red-400 text-xs">{error}</p>}

          <button onClick={handleCreate} disabled={loading}
            className="w-full py-2 rounded-xl bg-gradient-to-r from-[#FBBF24] to-[#FB923C] text-[#012622] font-bold text-sm hover:opacity-90 transition disabled:opacity-50 cursor-pointer">
            {loading ? 'Đang tạo...' : 'Tạo Collection'}
          </button>
        </div>
      </div>
    </div>
  );
}

function ArchiveCard({ item, collections, onStar, onDelete, onRedownload, onTagFilter, onAddToCollection, onPin, onUnpin }) {
  const [menu, setMenu] = useState(false);
  const platform = item.platform || 'other';
  const badgeCls = PLATFORM_COLORS[platform] || PLATFORM_COLORS.other;
  const duration = fmtDuration(item.duration_seconds);
  const myCollections = (collections || []).filter(c => (item.collection_ids || []).includes(c.id));
  const retStatus = item.file_retention_status || 'temporary';
  const isExpired = retStatus === 'expired_metadata_only';

  return (
    <div className="bg-[#0d2320] border border-slate-700/30 rounded-xl overflow-hidden group hover:border-slate-600/50 transition-all">
      {/* Thumbnail */}
      <div className="relative aspect-video bg-slate-800/50 overflow-hidden">
        {item.thumbnail_url ? (
          <img src={item.thumbnail_url} alt={item.title} className="w-full h-full object-cover" loading="lazy" />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <Archive className="w-8 h-8 text-slate-600" />
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
            <span className="text-[10px] font-bold text-slate-400 bg-black/70 px-2 py-1 rounded">File hết hạn</span>
          </div>
        )}
      </div>

      {/* Content */}
      <div className="p-3 space-y-2">
        <div className="flex items-start justify-between gap-2">
          <p className="text-sm font-medium text-white leading-snug line-clamp-2 flex-1">{item.title}</p>
          <div className="flex items-center gap-1 shrink-0">
            <button onClick={() => onStar(item.id, !item.is_starred)}
              className={`p-1 rounded cursor-pointer transition-colors ${item.is_starred ? 'text-[#FBBF24]' : 'text-slate-600 hover:text-[#FBBF24]'}`}>
              <Star className="w-4 h-4" fill={item.is_starred ? 'currentColor' : 'none'} />
            </button>
            <div className="relative">
              <button onClick={() => setMenu(m => !m)}
                className="p-1 text-slate-600 hover:text-slate-300 cursor-pointer"><MoreHorizontal className="w-4 h-4" /></button>
              {menu && (
                <div className="absolute right-0 top-6 z-20 bg-[#0a1f1c] border border-slate-700/50 rounded-lg py-1 w-48 shadow-xl">
                  <button onClick={() => { onRedownload(item.id); setMenu(false); }}
                    className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-700/50 cursor-pointer">
                    <Download className="w-3.5 h-3.5" /> {isExpired ? 'Tải lại (rehydrate)' : 'Tải lại'}
                  </button>
                  <button onClick={() => { onAddToCollection(item.id); setMenu(false); }}
                    className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-700/50 cursor-pointer">
                    <Folder className="w-3.5 h-3.5" /> Thêm vào collection
                  </button>
                  {!isExpired && retStatus !== 'pinned' && (
                    <button onClick={() => { onPin(item.file_job_id); setMenu(false); }}
                      className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-[#FBBF24] hover:bg-[#FBBF24]/10 cursor-pointer">
                      <Pin className="w-3.5 h-3.5" /> Ghim file
                    </button>
                  )}
                  {retStatus === 'pinned' && (
                    <button onClick={() => { onUnpin(item.file_job_id); setMenu(false); }}
                      className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-700/50 cursor-pointer">
                      <PinOff className="w-3.5 h-3.5" /> Bỏ ghim
                    </button>
                  )}
                  <a href={item.original_url} target="_blank" rel="noreferrer"
                    className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-700/50 cursor-pointer">
                    <ExternalLink className="w-3.5 h-3.5" /> Mở link gốc
                  </a>
                  <hr className="border-slate-700/50 my-1" />
                  <button onClick={() => { onDelete(item); setMenu(false); }}
                    className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-red-400 hover:bg-red-900/20 cursor-pointer">
                    <Trash2 className="w-3.5 h-3.5" /> Xóa...
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>

        {item.creator_name && (
          <p className="text-xs text-slate-400 truncate">
            {item.creator_handle ? `@${item.creator_handle}` : item.creator_name}
          </p>
        )}

        {/* Hashtags */}
        {(item.hashtags || []).length > 0 && (
          <div className="flex gap-1 flex-wrap">
            {item.hashtags.slice(0,3).map(tag => (
              <button key={tag} onClick={() => onTagFilter(tag)}
                className="text-[10px] px-1.5 py-0.5 rounded bg-slate-700/50 text-slate-400 hover:bg-slate-600/50 hover:text-slate-200 cursor-pointer transition-colors">
                #{tag}
              </button>
            ))}
          </div>
        )}

        {/* User tags */}
        {(item.tags_user || []).length > 0 && (
          <div className="flex gap-1 flex-wrap">
            {item.tags_user.slice(0,3).map(tag => (
              <span key={tag} className="text-[10px] px-1.5 py-0.5 rounded bg-[#FBBF24]/10 text-[#FBBF24] border border-[#FBBF24]/20">{tag}</span>
            ))}
          </div>
        )}

        {/* Collections */}
        {myCollections.length > 0 && (
          <div className="flex gap-1 flex-wrap">
            {myCollections.map(c => (
              <span key={c.id} style={{ color: c.color || '#94a3b8', borderColor: (c.color || '#94a3b8') + '40' }}
                className="text-[10px] px-1.5 py-0.5 rounded border">
                {c.icon || '📁'} {c.name}
              </span>
            ))}
          </div>
        )}

        <div className="flex items-center justify-between">
          <p className="text-[10px] text-slate-600">{fmtDate(item.archived_at)}</p>
          <RetentionBadge status={retStatus} pinExpiry={item.pin_expires_at} />
        </div>

        {/* Re-download CTA for expired files */}
        {isExpired && (
          <button onClick={() => onRedownload(item.id)}
            className="w-full py-1.5 rounded-lg border border-[#FBBF24]/30 text-[#FBBF24] text-xs font-bold hover:bg-[#FBBF24]/10 cursor-pointer transition-colors flex items-center justify-center gap-1.5">
            <RefreshCw className="w-3 h-3" /> Tải lại để xem lại
          </button>
        )}
      </div>
    </div>
  );
}

export default function ArchivePage({ onNavigate }) {
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
  const searchTimer = useRef(null);

  const headers = { Authorization: `Bearer ${session?.access_token}` };

  async function fetchCollections() {
    const r = await fetch(`${API}/api/v1/archive/collections`, { headers });
    if (r.ok) { const d = await r.json(); setCollections(d.collections || []); }
  }

  const fetchItems = useCallback(async (p = 1) => {
    setLoading(true);
    try {
      if (searchQ.length >= 2) {
        const r = await fetch(`${API}/api/v1/archive/search?q=${encodeURIComponent(searchQ)}&limit=40`, { headers });
        if (r.ok) { const d = await r.json(); setItems(d.items); setTotal(d.total); }
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
    } finally { setLoading(false); }
  }, [searchQ, platform, sortBy, activeView]);

  useEffect(() => { fetchCollections(); }, []);
  useEffect(() => {
    clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => { fetchItems(1); }, 300);
  }, [fetchItems, fileStatus]);

  async function handleStar(id, starred) {
    await fetch(`${API}/api/v1/archive/${id}`, {
      method: 'PATCH',
      headers: { ...headers, 'Content-Type': 'application/json' },
      body: JSON.stringify({ is_starred: starred }),
    });
    setItems(prev => prev.map(i => i.id === id ? { ...i, is_starred: starred } : i));
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
      setTotal(t => t - 1);
    }
  }

  async function handlePin(jobId) {
    if (!jobId) return;
    const r = await fetch(`${API}/api/v1/storage/pin/${jobId}`, { method: 'POST', headers });
    const d = await r.json();
    if (r.ok) {
      setPinMsg(d.message || 'Đã ghim file.');
      setTimeout(() => setPinMsg(''), 4000);
      fetchItems(1);
    } else {
      setPinMsg(d.detail?.message || d.detail || 'Không thể ghim.');
      setTimeout(() => setPinMsg(''), 5000);
    }
  }

  async function handleUnpin(jobId) {
    if (!jobId) return;
    const r = await fetch(`${API}/api/v1/storage/pin/${jobId}`, { method: 'DELETE', headers });
    const d = await r.json();
    setPinMsg(d.message || 'Đã bỏ ghim.');
    setTimeout(() => setPinMsg(''), 4000);
    fetchItems(1);
  }

  async function handleRedownload(id) {
    const r = await fetch(`${API}/api/v1/archive/${id}/re-download`, { method: 'POST', headers });
    const d = await r.json();
    setRedownloadMsg(d.message || 'Đã tạo lại lệnh tải.');
    setTimeout(() => setRedownloadMsg(''), 4000);
  }

  async function handleAddToCollection(itemId, collId) {
    await fetch(`${API}/api/v1/archive/collections/${collId}/add?item_id=${itemId}`, {
      method: 'POST', headers,
    });
    fetchItems(1);
    setAddToColl(null);
  }

  async function handleDeleteCollection(collId) {
    if (!confirm('Xóa collection? Items vẫn còn trong archive.')) return;
    await fetch(`${API}/api/v1/archive/collections/${collId}`, { method: 'DELETE', headers });
    fetchCollections();
    if (activeView === collId) setActiveView('all');
  }

  async function handleExport(format) {
    const r = await fetch(`${API}/api/v1/archive/export?format=${format}`, { headers });
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `vidgrab-archive.${format}`; a.click();
    URL.revokeObjectURL(url);
  }

  const LIMIT = 24;
  const hasMore = items.length < total;

  const activeColl = collections.find(c => c.id === activeView);
  const viewLabel = activeView === 'all' ? `Tất Cả (${total})`
    : activeView === 'starred' ? 'Đã Gắn Sao'
    : activeView === 'today' ? 'Hôm Nay'
    : activeColl ? `${activeColl.icon || '📁'} ${activeColl.name}` : 'Archive';

  return (
    <div className="flex min-h-[calc(100vh-4rem)] bg-[#012622]">
      {/* ── Sidebar ──────────────────────────────────────── */}
      <aside className="w-56 shrink-0 border-r border-slate-700/30 p-4 flex flex-col gap-1">
        <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-2">Views</p>

        {[
          { id: 'all',     label: 'Tất Cả', icon: <Archive className="w-4 h-4" /> },
          { id: 'starred', label: 'Gắn Sao', icon: <Star className="w-4 h-4" /> },
          { id: 'today',   label: 'Hôm Nay', icon: <Clock className="w-4 h-4" /> },
        ].map(v => (
          <button key={v.id} onClick={() => { setActiveView(v.id); setPage(1); }}
            className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm cursor-pointer transition-colors text-left ${activeView === v.id ? 'bg-[#FBBF24]/10 text-[#FBBF24]' : 'text-slate-400 hover:bg-slate-700/30 hover:text-white'}`}>
            {v.icon} {v.label}
          </button>
        ))}

        <div className="mt-4 flex items-center justify-between">
          <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">Collections</p>
          <button onClick={() => setShowCollModal(true)}
            className="text-slate-500 hover:text-[#FBBF24] cursor-pointer transition-colors">
            <Plus className="w-3.5 h-3.5" />
          </button>
        </div>

        {collections.length === 0 && (
          <p className="text-xs text-slate-600 px-3 mt-1">Chưa có collection nào</p>
        )}

        {collections.map(c => (
          <button key={c.id} onClick={() => { setActiveView(c.id); setPage(1); }}
            className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm cursor-pointer transition-colors text-left group ${activeView === c.id ? 'bg-slate-700/50 text-white' : 'text-slate-400 hover:bg-slate-700/30 hover:text-white'}`}>
            <span style={{ color: c.color || '#94a3b8' }}>{c.icon || '📁'}</span>
            <span className="flex-1 truncate">{c.name}</span>
            <span className="text-[10px] text-slate-600">{c.item_count || 0}</span>
            <button onClick={e => { e.stopPropagation(); handleDeleteCollection(c.id); }}
              className="hidden group-hover:flex text-slate-600 hover:text-red-400 cursor-pointer">
              <X className="w-3 h-3" />
            </button>
          </button>
        ))}

        {/* Export */}
        <div className="mt-auto pt-4 border-t border-slate-700/30 space-y-1">
          <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-2">Export</p>
          <button onClick={() => handleExport('json')}
            className="flex items-center gap-2 w-full px-3 py-2 rounded-lg text-xs text-slate-400 hover:bg-slate-700/30 hover:text-white cursor-pointer">
            <FileJson className="w-4 h-4" /> JSON
          </button>
          <button onClick={() => handleExport('csv')}
            className="flex items-center gap-2 w-full px-3 py-2 rounded-lg text-xs text-slate-400 hover:bg-slate-700/30 hover:text-white cursor-pointer">
            <FileText className="w-4 h-4" /> CSV
          </button>
        </div>
      </aside>

      {/* ── Main Area ─────────────────────────────────────── */}
      <main className="flex-1 p-6 overflow-y-auto">
        {/* Header */}
        <div className="flex flex-col sm:flex-row gap-3 mb-5">
          <div className="flex-1">
            <h2 className="text-xl font-bold text-white mb-1">{viewLabel}</h2>
          </div>

          {/* Search */}
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
            <input
              value={searchQ} onChange={e => setSearchQ(e.target.value)}
              placeholder="Tìm kiếm archive..."
              className="w-64 bg-slate-800/50 border border-slate-700/50 rounded-lg pl-9 pr-3 py-2 text-sm text-white focus:outline-none focus:border-[#FBBF24]/50"
            />
            {searchQ && <button onClick={() => setSearchQ('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-500 hover:text-white cursor-pointer"><X className="w-3.5 h-3.5" /></button>}
          </div>

          <button onClick={() => setShowFilters(f => !f)}
            className={`flex items-center gap-1.5 px-3 py-2 rounded-lg border text-sm cursor-pointer transition-colors ${showFilters ? 'border-[#FBBF24]/50 text-[#FBBF24] bg-[#FBBF24]/10' : 'border-slate-700/50 text-slate-400 hover:text-white'}`}>
            <Filter className="w-4 h-4" /> Filter
          </button>
        </div>

        {/* File status pills — always visible */}
        <div className="flex gap-2 mb-3 flex-wrap">
          {[
            { val: '',          label: 'Tất cả' },
            { val: 'available', label: '✅ Khả dụng' },
            { val: 'pinned',    label: '📌 Đã ghim' },
            { val: 'expired',   label: '⏰ Hết hạn' },
          ].map(opt => (
            <button key={opt.val} onClick={() => { setFileStatus(opt.val); setPage(1); }}
              className={`px-3 py-1 rounded-full text-xs font-medium cursor-pointer transition-colors border ${fileStatus === opt.val ? 'bg-[#FBBF24]/15 border-[#FBBF24]/50 text-[#FBBF24]' : 'border-slate-700/40 text-slate-400 hover:text-white hover:border-slate-500'}`}>
              {opt.label}
            </button>
          ))}
        </div>

        {/* Filter bar */}
        {showFilters && (
          <div className="flex gap-3 mb-4 flex-wrap">
            <select value={platform} onChange={e => { setPlatform(e.target.value); setPage(1); }}
              className="bg-slate-800/50 border border-slate-700/50 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none cursor-pointer">
              <option value="">Tất cả nền tảng</option>
              {['youtube','tiktok','facebook','instagram','twitter','reddit'].map(p => (
                <option key={p} value={p}>{p.charAt(0).toUpperCase()+p.slice(1)}</option>
              ))}
            </select>
            <select value={sortBy} onChange={e => { setSortBy(e.target.value); setPage(1); }}
              className="bg-slate-800/50 border border-slate-700/50 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none cursor-pointer">
              <option value="archived_at">Mới lưu nhất</option>
              <option value="upload_date">Ngày upload</option>
              <option value="duration">Thời lượng</option>
              <option value="most_viewed">Lượt xem</option>
              <option value="title">Tiêu đề A-Z</option>
            </select>
          </div>
        )}

        {redownloadMsg && (
          <div className="mb-4 px-4 py-2 bg-green-900/30 border border-green-700/30 rounded-lg text-green-400 text-sm">
            {redownloadMsg}
          </div>
        )}
        {pinMsg && (
          <div className="mb-4 px-4 py-2 bg-[#FBBF24]/10 border border-[#FBBF24]/30 rounded-lg text-[#FBBF24] text-sm">
            {pinMsg}
          </div>
        )}

        {/* Grid */}
        {loading && items.length === 0 ? (
          <div className="flex items-center justify-center h-48 text-slate-500 text-sm">Đang tải...</div>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-48 gap-3 text-slate-500">
            <Archive className="w-12 h-12 opacity-30" />
            <p className="text-sm">Archive trống. Tải video để tự động lưu vào đây.</p>
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
                  className="px-6 py-2 rounded-xl border border-slate-700/50 text-sm text-slate-400 hover:text-white hover:border-slate-500 transition cursor-pointer disabled:opacity-50">
                  {loading ? 'Đang tải...' : `Xem thêm (${total - items.length} còn lại)`}
                </button>
              </div>
            )}
          </>
        )}
      </main>

      {/* ── Add to Collection modal ──────────────────────── */}
      {addToColl && (
        <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={() => setAddToColl(null)}>
          <div className="bg-[#0d2320] border border-slate-700/50 rounded-2xl w-full max-w-sm p-5" onClick={e => e.stopPropagation()}>
            <h3 className="font-bold text-white mb-3">Chọn Collection</h3>
            {collections.length === 0 ? (
              <p className="text-sm text-slate-400">Chưa có collection nào. Tạo mới trước nhé.</p>
            ) : (
              <div className="space-y-1">
                {collections.map(c => (
                  <button key={c.id} onClick={() => handleAddToCollection(addToColl, c.id)}
                    className="flex items-center gap-3 w-full px-3 py-2 rounded-lg hover:bg-slate-700/50 cursor-pointer text-sm text-slate-300 text-left">
                    <span style={{ color: c.color || '#94a3b8' }}>{c.icon || '📁'}</span>
                    {c.name}
                    <span className="ml-auto text-xs text-slate-600">{c.item_count || 0} items</span>
                  </button>
                ))}
              </div>
            )}
            <button onClick={() => setAddToColl(null)} className="mt-3 text-xs text-slate-500 hover:text-slate-300 cursor-pointer">Huỷ</button>
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

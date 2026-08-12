import { useState, useEffect, useCallback, useRef } from 'react';
import {
  History, Search, Trash2, Download, ExternalLink, Loader2,
  ChevronDown, SlidersHorizontal, MessageSquare, Plus, Pencil,
  Check, X, Clock, Filter, FileDown, RotateCcw,
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';

const apiBase = localStorage.getItem('vg_api_base') || '';
const API = `${apiBase}/api/v1`;

// Session ID for anonymous note ownership (generated once, stored in localStorage)
function getSessionId() {
  let sid = localStorage.getItem('vg_session_id');
  if (!sid) {
    sid = crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2);
    localStorage.setItem('vg_session_id', sid);
  }
  return sid;
}
const SESSION_ID = getSessionId();

const PLATFORM_BADGE = {
  youtube:   { label: 'YouTube',   color: 'bg-red-500/20 text-red-400' },
  tiktok:    { label: 'TikTok',    color: 'bg-pink-500/20 text-pink-400' },
  instagram: { label: 'Instagram', color: 'bg-purple-500/20 text-purple-400' },
  facebook:  { label: 'Facebook',  color: 'bg-blue-500/20 text-blue-400' },
  douyin:    { label: 'Douyin',    color: 'bg-sky-500/20 text-sky-400' },
  spotify:   { label: 'Spotify',   color: 'bg-green-500/20 text-green-400' },
  twitter:   { label: 'Twitter',   color: 'bg-sky-400/20 text-sky-300' },
  threads:   { label: 'Threads',   color: 'bg-violet-500/20 text-violet-400' },
  reddit:    { label: 'Reddit',    color: 'bg-orange-500/20 text-orange-400' },
  pinterest: { label: 'Pinterest', color: 'bg-rose-500/20 text-rose-400' },
};

function getPlatformKey(item) {
  if (item.platform) return item.platform.toLowerCase();
  const u = (item.original_url || '').toLowerCase();
  if (u.includes('youtube') || u.includes('youtu.be')) return 'youtube';
  if (u.includes('tiktok')) return 'tiktok';
  if (u.includes('instagram')) return 'instagram';
  if (u.includes('facebook') || u.includes('fb.watch')) return 'facebook';
  if (u.includes('douyin')) return 'douyin';
  if (u.includes('spotify')) return 'spotify';
  if (u.includes('twitter') || u.includes('x.com')) return 'twitter';
  if (u.includes('threads')) return 'threads';
  if (u.includes('reddit')) return 'reddit';
  if (u.includes('pinterest')) return 'pinterest';
  return null;
}

function fmtDuration(s) {
  if (!s) return null;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
  return `${m}:${String(sec).padStart(2,'0')}`;
}

function fmtTs(s) {
  if (s == null) return '';
  return fmtDuration(s) || `${s}s`;
}

// ─── Main Component ───────────────────────────────────────────────────────────

export default function UserHistoryContent({ onNavigate }) {
  const { session } = useAuth();

  // List state
  const [items, setItems]     = useState([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage]       = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [filterTab, setFilterTab] = useState('all');
  const [isOfflineCached, setIsOfflineCached] = useState(false);

  // Search & filters
  const [search, setSearch]                 = useState('');
  const [filterPlatform, setFilterPlatform] = useState('');
  const [filterCreator, setFilterCreator]   = useState('');
  const [filterHasNotes, setFilterHasNotes] = useState(false);
  const [filterTag, setFilterTag]           = useState('');
  const [sortBy, setSortBy]                 = useState('newest');
  const [showFilters, setShowFilters]       = useState(false);

  // Notes panel open state
  const [openNotesId, setOpenNotesId] = useState(null);

  const authHeaders = session ? { Authorization: `Bearer ${session.access_token}` } : {};

  const applyTabFilter = (itemsList) => {
    if (!itemsList || !itemsList.length) return itemsList;
    const now = Date.now();
    const todayStart = new Date(); todayStart.setHours(0,0,0,0);
    const weekAgo = now - 7 * 24 * 60 * 60 * 1000;
    if (filterTab === 'today') return itemsList.filter(i => new Date(i.created_at || i.timestamp || i.createdAt).getTime() >= todayStart.getTime());
    if (filterTab === 'recent') return itemsList.filter(i => new Date(i.created_at || i.timestamp || i.createdAt).getTime() >= weekAgo);
    if (filterTab === 'failed') return itemsList.filter(i => (i.status || '').toLowerCase().includes('fail') || !!i.error);
    return itemsList;
  };

  // ── Load history ───────────────────────────────────────────────────
  const load = useCallback(async (pg = 1, opts = {}) => {
    if (!session) return;
    if (pg === 1) setLoading(true);
    try {
      const params = new URLSearchParams({ page: pg, per_page: 20, sort_by: opts.sortBy || sortBy });
      if (opts.search ?? search) params.set('search', opts.search ?? search);
      if (opts.filterPlatform ?? filterPlatform) params.set('platform', opts.filterPlatform ?? filterPlatform);
      if (opts.filterCreator ?? filterCreator) params.set('creator', opts.filterCreator ?? filterCreator);
      if (opts.filterHasNotes ?? filterHasNotes) params.set('has_notes', 'true');
      if (opts.filterTag ?? filterTag) params.set('tag', opts.filterTag ?? filterTag);

      const res = await fetch(`${API}/user/history?${params}`, { headers: authHeaders });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setItems(prev => pg === 1 ? (data.items || []) : [...prev, ...(data.items || [])]);
      setHasMore(data.has_more);
      setPage(pg);
      if (pg === 1) {
        try { localStorage.setItem('vg_user_history_cache', JSON.stringify({ data: data, ts: Date.now() })); } catch {}
      }
    } catch (err) {
      console.error('Failed to load user history', err);
      try {
        const cached = JSON.parse(localStorage.getItem('vg_user_history_cache') || 'null');
        if (cached?.data) {
          const cachedItems = cached.data.items || cached.data.downloads || cached.data;
          if (Array.isArray(cachedItems)) { setItems(cachedItems); setIsOfflineCached(true); }
        }
      } catch {}
    } finally {
      setLoading(false);
    }
  }, [session, search, filterPlatform, filterCreator, filterHasNotes, filterTag, sortBy]);

  // Debounce search + creator filter
  const debounceRef = useRef(null);
  useEffect(() => {
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => { load(1); }, 350);
    return () => clearTimeout(debounceRef.current);
  }, [search, filterPlatform, filterCreator, filterHasNotes, filterTag, sortBy, session]);

  // ── Delete ─────────────────────────────────────────────────────────
  const [deleting, setDeleting] = useState(null);
  const handleDelete = async (id) => {
    setDeleting(id);
    try {
      await fetch(`${API}/user/history/${id}`, { method: 'DELETE', headers: authHeaders });
      setItems(prev => prev.filter(i => i.id !== id));
    } finally {
      setDeleting(null);
    }
  };

  // ── Hashtag click ──────────────────────────────────────────────────
  const handleTagClick = (tag) => {
    setFilterTag(tag === filterTag ? '' : tag);
    setShowFilters(true);
  };

  // ── Export ─────────────────────────────────────────────────────────
  const handleExport = async (format) => {
    if (!session) return;
    const url = `${API}/user/history/export?format=${format}&range_days=90`;
    const res = await fetch(url, { headers: authHeaders });
    if (!res.ok) return;
    const blob = await res.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = format === 'json' ? 'vidgrab-history.json' : 'vidgrab-history.csv';
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const activeFilters = [filterPlatform, filterCreator, filterHasNotes, filterTag].filter(Boolean).length;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-slate-800 flex items-center justify-center">
            <History className="w-5 h-5 text-[#FBBF24]" />
          </div>
          <div>
            <h1 className="text-white font-bold text-lg">Lich su tai</h1>
            <p className="text-slate-400 text-xs">Tat ca luot tai gan voi tai khoan</p>
          </div>
        </div>
        {/* Export */}
        <div className="flex gap-1.5">
          <button onClick={() => handleExport('csv')}
            className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-slate-800/60 border border-slate-700/50 text-slate-400 hover:text-white text-xs transition-colors cursor-pointer">
            <FileDown className="w-3 h-3" /> CSV
          </button>
          <button onClick={() => handleExport('json')}
            className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-slate-800/60 border border-slate-700/50 text-slate-400 hover:text-white text-xs transition-colors cursor-pointer">
            <FileDown className="w-3 h-3" /> JSON
          </button>
        </div>
      </div>

      {/* Tab filter */}
      <div className="flex gap-2 mb-4 flex-wrap">
        {[['all', 'Tất cả'], ['today', 'Hôm nay'], ['recent', '7 ngày'], ['failed', 'Thất bại']].map(([key, label]) => (
          <button key={key} onClick={() => setFilterTab(key)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${filterTab === key ? 'bg-indigo-600 text-white' : 'bg-white/5 text-white/50 hover:bg-white/10'}`}>
            {label}
          </button>
        ))}
      </div>

      {/* Search bar */}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
          <input
            type="text"
            placeholder="Tim kiem tieu de, creator, hashtag..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full bg-slate-800/50 border border-slate-600/40 rounded-xl pl-9 pr-4 py-2.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-[#FBBF24]/50 focus:ring-1 focus:ring-[#FBBF24]/20 transition"
          />
          {search && (
            <button onClick={() => setSearch('')}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-white cursor-pointer">
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
        <button
          onClick={() => setShowFilters(p => !p)}
          className={`flex items-center gap-1.5 px-3 py-2.5 rounded-xl border text-xs font-semibold transition-colors cursor-pointer ${
            showFilters || activeFilters > 0
              ? 'bg-[#FBBF24]/10 border-[#FBBF24]/40 text-[#FBBF24]'
              : 'bg-slate-800/50 border-slate-600/40 text-slate-400 hover:text-white'
          }`}
        >
          <SlidersHorizontal className="w-3.5 h-3.5" />
          Lo{activeFilters > 0 ? ` (${activeFilters})` : ''}
        </button>
      </div>

      {/* Filter bar */}
      {showFilters && (
        <div className="p-3 rounded-xl bg-slate-800/40 border border-slate-700/40 space-y-2.5">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {/* Platform */}
            <div>
              <label className="text-[10px] text-slate-500 uppercase mb-1 block">Nen tang</label>
              <select value={filterPlatform} onChange={e => setFilterPlatform(e.target.value)}
                className="w-full bg-slate-900/60 border border-slate-700 rounded-lg px-2 py-1.5 text-xs text-slate-200 focus:outline-none cursor-pointer">
                <option value="">Tat ca</option>
                {Object.keys(PLATFORM_BADGE).map(p => (
                  <option key={p} value={p}>{PLATFORM_BADGE[p].label}</option>
                ))}
              </select>
            </div>

            {/* Sort */}
            <div>
              <label className="text-[10px] text-slate-500 uppercase mb-1 block">Sap xep</label>
              <select value={sortBy} onChange={e => setSortBy(e.target.value)}
                className="w-full bg-slate-900/60 border border-slate-700 rounded-lg px-2 py-1.5 text-xs text-slate-200 focus:outline-none cursor-pointer">
                <option value="newest">Moi nhat</option>
                <option value="oldest">Cu nhat</option>
                <option value="most_viewed">Nhieu view nhat</option>
                <option value="longest">Dai nhat</option>
              </select>
            </div>

            {/* Creator */}
            <div>
              <label className="text-[10px] text-slate-500 uppercase mb-1 block">Creator</label>
              <input value={filterCreator} onChange={e => setFilterCreator(e.target.value)}
                placeholder="Ten creator..."
                className="w-full bg-slate-900/60 border border-slate-700 rounded-lg px-2 py-1.5 text-xs text-slate-200 placeholder-slate-600 focus:outline-none"
              />
            </div>

            {/* Has notes */}
            <div className="flex flex-col justify-end">
              <label className="text-[10px] text-slate-500 uppercase mb-1 block">Co ghi chu</label>
              <button
                onClick={() => setFilterHasNotes(p => !p)}
                className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold border transition-colors cursor-pointer ${
                  filterHasNotes
                    ? 'bg-[#FBBF24]/10 border-[#FBBF24]/40 text-[#FBBF24]'
                    : 'bg-slate-900/60 border-slate-700 text-slate-400'
                }`}
              >
                <MessageSquare className="w-3 h-3" />
                {filterHasNotes ? 'Dang loc' : 'Tat ca'}
              </button>
            </div>
          </div>

          {/* Active tag filter */}
          {filterTag && (
            <div className="flex items-center gap-2 text-xs text-slate-400">
              <Filter className="w-3 h-3" />
              Loc hashtag: <span className="text-[#FBBF24] font-semibold">#{filterTag}</span>
              <button onClick={() => setFilterTag('')} className="text-slate-500 hover:text-white cursor-pointer"><X className="w-3 h-3" /></button>
            </div>
          )}

          {/* Clear all */}
          {activeFilters > 0 && (
            <button
              onClick={() => { setFilterPlatform(''); setFilterCreator(''); setFilterHasNotes(false); setFilterTag(''); setSortBy('newest'); }}
              className="text-xs text-slate-500 hover:text-red-400 transition-colors cursor-pointer"
            >
              Xoa tat ca bo loc
            </button>
          )}
        </div>
      )}

      {/* Offline badge */}
      {isOfflineCached && (
        <div className="flex items-center gap-2 bg-yellow-500/10 border border-yellow-500/30 rounded-lg px-3 py-2 mb-3 text-xs text-yellow-400">
          <span>📵</span> Đang hiển thị dữ liệu đã lưu — kết nối lại để cập nhật
        </div>
      )}

      {/* List */}
      {loading && items.length === 0 ? (
        <div className="flex justify-center py-16">
          <Loader2 className="w-6 h-6 text-[#FBBF24] animate-spin" />
        </div>
      ) : items.length === 0 ? (
        <div className="text-center py-16 space-y-2">
          <History className="w-10 h-10 text-slate-600 mx-auto" />
          <p className="text-slate-400">{(search || activeFilters > 0) ? 'Khong tim thay ket qua' : 'Chua co lich su tai'}</p>
          {!(search || activeFilters > 0) && (
            <button onClick={() => onNavigate?.('landing', '/')}
              className="text-[#FBBF24] text-sm hover:underline cursor-pointer">
              Tai video dau tien &rarr;
            </button>
          )}
        </div>
      ) : (
        <div className="space-y-2">
          {applyTabFilter(items).map(item => (
            <HistoryItem
              key={item.id}
              item={item}
              onDelete={handleDelete}
              deleting={deleting === item.id}
              onTagClick={handleTagClick}
              activeTag={filterTag}
              isNotesOpen={openNotesId === item.id}
              onToggleNotes={() => setOpenNotesId(p => p === item.id ? null : item.id)}
              session={session}
              onRerun={(url) => {
                window.dispatchEvent(new CustomEvent('vidgrab:share-url', { detail: { url } }));
                onNavigate?.('home', '/');
              }}
            />
          ))}

          {hasMore && (
            <button
              onClick={() => load(page + 1)}
              className="w-full py-3 rounded-xl border border-slate-700/50 text-slate-400 hover:text-white hover:border-slate-600 transition-colors text-sm flex items-center justify-center gap-2 cursor-pointer"
            >
              <ChevronDown className="w-4 h-4" />
              Xem them
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ─── HistoryItem ──────────────────────────────────────────────────────────────

function HistoryItem({ item, onDelete, deleting, onTagClick, activeTag, isNotesOpen, onToggleNotes, session, onRerun }) {
  const platformKey = getPlatformKey(item);
  const badge = platformKey ? PLATFORM_BADGE[platformKey] : null;
  const statusColor = item.status === 'success' ? 'text-green-400' : 'text-red-400';
  const date = new Date(item.created_at).toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric' });

  const meta = item.job_metadata || {};
  const hashtags = (meta.hashtags || []).slice(0, 3);
  const duration = fmtDuration(meta.duration_seconds);
  const notesCount = (item.clip_notes || []).length;

  return (
    <div className="bg-slate-800/30 border border-slate-700/40 rounded-xl overflow-hidden hover:border-slate-600/60 transition-colors">
      <div className="flex items-start gap-3 p-3 group">
        {/* Thumbnail */}
        {item.thumbnail_url || meta.thumbnail_url ? (
          <img
            src={item.thumbnail_url || meta.thumbnail_url}
            alt=""
            className="w-16 h-10 object-cover rounded-lg flex-shrink-0 bg-slate-700"
          />
        ) : (
          <div className="w-16 h-10 rounded-lg bg-slate-700 flex-shrink-0" />
        )}

        {/* Info */}
        <div className="flex-1 min-w-0">
          <p className="text-white text-sm font-medium truncate">{item.title || item.original_url}</p>

          {/* Row 1: badges */}
          <div className="flex items-center gap-1.5 mt-1 flex-wrap">
            {badge && (
              <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${badge.color}`}>
                {badge.label}
              </span>
            )}
            <span className={`text-[10px] ${statusColor}`}>
              {item.status === 'success' ? '✓ Thanh cong' : '✗ That bai'}
            </span>
            {item.file_size_mb > 0 && (
              <span className="text-[10px] text-slate-500">{item.file_size_mb.toFixed(1)} MB</span>
            )}
            {duration && (
              <span className="text-[10px] text-slate-500 flex items-center gap-0.5">
                <Clock className="w-2.5 h-2.5" />{duration}
              </span>
            )}
            <span className="text-[10px] text-slate-600">{date}</span>
          </div>

          {/* Row 2: creator + hashtags */}
          {(meta.creator_name || meta.creator_handle || hashtags.length > 0) && (
            <div className="flex items-center gap-2 mt-1 flex-wrap">
              {(meta.creator_name || meta.creator_handle) && (
                <span className="text-[10px] text-slate-400">
                  @{meta.creator_handle || meta.creator_name}
                </span>
              )}
              {hashtags.map(tag => (
                <button
                  key={tag}
                  onClick={() => onTagClick(tag)}
                  className={`text-[10px] px-1.5 py-0.5 rounded cursor-pointer transition-colors ${
                    activeTag === tag
                      ? 'bg-[#FBBF24]/20 text-[#FBBF24]'
                      : 'bg-slate-700/50 text-slate-500 hover:text-slate-300'
                  }`}
                >
                  #{tag}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1 flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
          {item.original_url && onRerun && (
            <button
              onClick={() => onRerun(item.original_url)}
              title="Tải lại"
              className="flex items-center gap-1 p-1.5 rounded-lg text-slate-500 hover:text-emerald-400 hover:bg-slate-700 transition-colors cursor-pointer"
            >
              <RotateCcw className="w-3.5 h-3.5" />
            </button>
          )}
          <button
            onClick={onToggleNotes}
            title="Ghi chu"
            className={`flex items-center gap-1 p-1.5 rounded-lg transition-colors cursor-pointer ${
              isNotesOpen || notesCount > 0
                ? 'text-[#FBBF24] bg-[#FBBF24]/10'
                : 'text-slate-500 hover:text-[#FBBF24] hover:bg-slate-700'
            }`}
          >
            <MessageSquare className="w-3.5 h-3.5" />
            {notesCount > 0 && <span className="text-[10px] font-bold">{notesCount}</span>}
          </button>
          {item.original_url && (
            <a href={item.original_url} target="_blank" rel="noopener noreferrer"
              className="p-1.5 rounded-lg text-slate-500 hover:text-white hover:bg-slate-700 transition-colors">
              <ExternalLink className="w-3.5 h-3.5" />
            </a>
          )}
          <button
            onClick={() => onDelete(item.id)}
            disabled={deleting}
            className="p-1.5 rounded-lg text-slate-500 hover:text-red-400 hover:bg-slate-700 transition-colors cursor-pointer disabled:opacity-50"
          >
            {deleting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>

      {/* Clip Notes panel (inline expansion) */}
      {isNotesOpen && (
        <ClipNotesPanel jobId={item.id} session={session} />
      )}
    </div>
  );
}

// ─── ClipNotesPanel ───────────────────────────────────────────────────────────

function ClipNotesPanel({ jobId, session }) {
  const [notes, setNotes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [noteText, setNoteText] = useState('');
  const [tsInput, setTsInput] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [editingId, setEditingId] = useState(null);
  const [editText, setEditText] = useState('');
  const [editTs, setEditTs] = useState('');

  const authHeaders = session
    ? { Authorization: `Bearer ${session.access_token}`, 'X-Session-ID': SESSION_ID }
    : { 'X-Session-ID': SESSION_ID };

  // Parse mm:ss to total seconds
  function parseMmSs(str) {
    if (!str.trim()) return null;
    const parts = str.trim().split(':').map(Number);
    if (parts.length === 2) return parts[0] * 60 + parts[1];
    if (parts.length === 1 && !isNaN(parts[0])) return parts[0];
    return null;
  }

  // Load notes on mount
  useEffect(() => {
    fetch(`${API}/jobs/${jobId}/notes`, { headers: authHeaders })
      .then(r => r.ok ? r.json() : { notes: [] })
      .then(d => setNotes(d.notes || []))
      .finally(() => setLoading(false));
  }, [jobId]);

  const handleSubmit = async () => {
    if (!noteText.trim()) return;
    setSubmitting(true);
    setError('');
    const ts = parseMmSs(tsInput);
    try {
      const res = await fetch(`${API}/jobs/${jobId}/notes`, {
        method: 'POST',
        headers: { ...authHeaders, 'Content-Type': 'application/json' },
        body: JSON.stringify({ note_text: noteText.trim(), timestamp_seconds: ts }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.detail || 'Loi luu ghi chu.'); return; }
      setNotes(prev => [...prev, data]);
      setNoteText('');
      setTsInput('');
    } catch (e) {
      setError('Loi ket noi.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleEdit = async (noteId) => {
    const ts = parseMmSs(editTs);
    try {
      const res = await fetch(`${API}/jobs/${jobId}/notes/${noteId}`, {
        method: 'PATCH',
        headers: { ...authHeaders, 'Content-Type': 'application/json' },
        body: JSON.stringify({ note_text: editText.trim(), timestamp_seconds: ts }),
      });
      if (res.ok) {
        const updated = await res.json();
        setNotes(prev => prev.map(n => n.id === noteId ? updated : n));
        setEditingId(null);
      }
    } catch (e) {}
  };

  const handleDeleteNote = async (noteId) => {
    try {
      await fetch(`${API}/jobs/${jobId}/notes/${noteId}`, { method: 'DELETE', headers: authHeaders });
      setNotes(prev => prev.filter(n => n.id !== noteId));
    } catch (e) {}
  };

  return (
    <div className="px-3 pb-3 border-t border-slate-700/30 mt-0">
      <p className="text-[10px] text-slate-500 uppercase mt-2 mb-2 font-semibold tracking-wider flex items-center gap-1">
        <MessageSquare className="w-3 h-3" /> Ghi chu
      </p>

      {/* Existing notes */}
      {loading ? (
        <div className="flex justify-center py-3"><Loader2 className="w-4 h-4 text-slate-500 animate-spin" /></div>
      ) : (
        <div className="space-y-1.5 mb-3">
          {notes.length === 0 && <p className="text-xs text-slate-600">Chua co ghi chu nao.</p>}
          {notes.map(note => (
            <div key={note.id} className="flex items-start gap-2 bg-slate-900/40 rounded-lg px-2.5 py-2">
              {editingId === note.id ? (
                <div className="flex-1 space-y-1">
                  <div className="flex gap-2">
                    <input value={editTs} onChange={e => setEditTs(e.target.value)}
                      placeholder="mm:ss"
                      className="w-16 bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 focus:outline-none"
                    />
                    <textarea value={editText} onChange={e => setEditText(e.target.value)} rows={2}
                      className="flex-1 bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 resize-none focus:outline-none"
                    />
                  </div>
                  <div className="flex gap-1.5">
                    <button onClick={() => handleEdit(note.id)}
                      className="flex items-center gap-1 px-2 py-1 rounded bg-emerald-500/20 text-emerald-400 text-xs cursor-pointer hover:bg-emerald-500/30">
                      <Check className="w-3 h-3" /> Luu
                    </button>
                    <button onClick={() => setEditingId(null)}
                      className="flex items-center gap-1 px-2 py-1 rounded bg-slate-700 text-slate-400 text-xs cursor-pointer">
                      <X className="w-3 h-3" /> Huy
                    </button>
                  </div>
                </div>
              ) : (
                <div className="flex-1">
                  {note.timestamp_seconds != null && (
                    <span className="inline-block bg-[#FBBF24]/10 text-[#FBBF24] text-[10px] font-semibold px-1.5 py-0.5 rounded mr-1.5">
                      {fmtTs(note.timestamp_seconds)}
                    </span>
                  )}
                  <span className="text-xs text-slate-300">{note.note_text}</span>
                </div>
              )}
              {editingId !== note.id && (
                <div className="flex gap-1 flex-shrink-0">
                  <button onClick={() => { setEditingId(note.id); setEditText(note.note_text); setEditTs(note.timestamp_seconds != null ? fmtTs(note.timestamp_seconds) : ''); }}
                    className="p-1 text-slate-600 hover:text-slate-300 cursor-pointer">
                    <Pencil className="w-3 h-3" />
                  </button>
                  <button onClick={() => handleDeleteNote(note.id)}
                    className="p-1 text-slate-600 hover:text-red-400 cursor-pointer">
                    <X className="w-3 h-3" />
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Add note form */}
      {notes.length < 10 && (
        <div className="space-y-1.5">
          <div className="flex gap-2">
            <input value={tsInput} onChange={e => setTsInput(e.target.value)}
              placeholder="mm:ss"
              className="w-16 bg-slate-900/60 border border-slate-700 rounded-lg px-2 py-1.5 text-xs text-slate-200 placeholder-slate-600 focus:outline-none"
            />
            <input value={noteText} onChange={e => setNoteText(e.target.value)}
              placeholder="Them ghi chu (toi da 500 ky tu)..."
              maxLength={500}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSubmit(); } }}
              className="flex-1 bg-slate-900/60 border border-slate-700 rounded-lg px-2 py-1.5 text-xs text-slate-200 placeholder-slate-600 focus:outline-none"
            />
            <button onClick={handleSubmit} disabled={submitting || !noteText.trim()}
              className="px-2.5 py-1.5 rounded-lg bg-[#FBBF24]/20 text-[#FBBF24] text-xs font-bold border border-[#FBBF24]/30 hover:bg-[#FBBF24]/30 transition-colors cursor-pointer disabled:opacity-50 flex items-center gap-1">
              {submitting ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
              Luu
            </button>
          </div>
          {error && <p className="text-xs text-red-400">{error}</p>}
          <p className="text-[10px] text-slate-600">
            {notes.length}/10 ghi chu &middot; Enter de luu &middot; mm:ss de gan timestamp
          </p>
        </div>
      )}
      {notes.length >= 10 && (
        <p className="text-xs text-slate-500">Da dat gioi han 10 ghi chu.</p>
      )}
    </div>
  );
}

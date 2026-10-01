import { useState, useEffect } from 'react';
import { useAuth } from '../context/AuthContext';
import { ListMusic, RefreshCw, Trash2, Play, Plus, ExternalLink, Loader2 } from 'lucide-react';
import { API_BASE } from '../lib/apiBase';

const API = `${import.meta.env.VITE_API_URL || ''}/api/v1`;

function platformBadge(platform) {
  const p = (platform || '').toLowerCase();
  if (p === 'youtube')  return { label: 'YouTube',  cls: 'bg-danger-soft text-danger border border-danger/30' };
  if (p === 'spotify')  return { label: 'Spotify',  cls: 'bg-success-soft text-success border border-success/30' };
  if (p === 'tiktok')   return { label: 'TikTok',   cls: 'bg-surface-2 text-fg-2 border border-line-strong' };
  if (p === 'threads')  return { label: 'Threads',  cls: 'bg-surface-2 text-fg-2 border border-line' };
  return { label: platform || 'Other', cls: 'bg-surface-2 text-fg-muted border border-line-strong' };
}

function formatDate(iso) {
  if (!iso) return 'Chưa có';
  return new Date(iso).toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export default function PlaylistsPage({ onNavigate }) {
  const { session } = useAuth();
  const apiBase = API_BASE;

  const [playlists, setPlaylists]   = useState([]);
  const [loading, setLoading]       = useState(true);
  const [fetchError, setFetchError] = useState('');

  // save form
  const [saveUrl, setSaveUrl]         = useState('');
  const [saving, setSaving]           = useState(false);
  const [saveError, setSaveError]     = useState('');

  // per-card state maps: id -> boolean/string
  const [refreshing, setRefreshing]   = useState({});
  const [deleting, setDeleting]       = useState({});
  const [cardError, setCardError]     = useState({});

  // toast for copy-url
  const [toast, setToast] = useState('');

  const authHeader = () => ({ Authorization: `Bearer ${session?.access_token}` });

  const fetchPlaylists = async () => {
    setLoading(true);
    setFetchError('');
    try {
      const res = await fetch(`${apiBase}${API}/playlists`, { headers: authHeader() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setPlaylists(Array.isArray(data) ? data : (data.playlists || []));
    } catch (err) {
      setFetchError('Không tải được danh sách playlist. ' + (err.message || ''));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { if (session) fetchPlaylists(); }, [session]);

  const handleSave = async (e) => {
    e.preventDefault();
    if (!saveUrl.trim()) return;
    setSaving(true);
    setSaveError('');
    try {
      const res = await fetch(`${apiBase}${API}/playlists/save`, {
        method: 'POST',
        headers: { ...authHeader(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: saveUrl.trim() }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      setSaveUrl('');
      await fetchPlaylists();
    } catch (err) {
      setSaveError(err.message || 'Không thể lưu playlist. Thử lại sau.');
    } finally {
      setSaving(false);
    }
  };

  const handleRefresh = async (id) => {
    setRefreshing(prev => ({ ...prev, [id]: true }));
    setCardError(prev => ({ ...prev, [id]: '' }));
    try {
      const res = await fetch(`${apiBase}${API}/playlists/${id}/refresh`, {
        method: 'POST',
        headers: authHeader(),
      });
      if (!res.ok) { const d = await res.json(); throw new Error(d.detail || `HTTP ${res.status}`); }
      await fetchPlaylists();
    } catch (err) {
      setCardError(prev => ({ ...prev, [id]: err.message || 'Không thể tải lại.' }));
    } finally {
      setRefreshing(prev => ({ ...prev, [id]: false }));
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm('Xoá playlist này khỏi danh sách đã lưu?')) return;
    setDeleting(prev => ({ ...prev, [id]: true }));
    setCardError(prev => ({ ...prev, [id]: '' }));
    try {
      const res = await fetch(`${apiBase}${API}/playlists/${id}`, {
        method: 'DELETE',
        headers: authHeader(),
      });
      if (!res.ok) { const d = await res.json(); throw new Error(d.detail || `HTTP ${res.status}`); }
      setPlaylists(prev => prev.filter(p => p.id !== id));
    } catch (err) {
      setCardError(prev => ({ ...prev, [id]: err.message || 'Không thể xoá.' }));
    } finally {
      setDeleting(prev => ({ ...prev, [id]: false }));
    }
  };

  const handleDownload = (playlist) => {
    if (onNavigate && playlist.url) {
      try { sessionStorage.setItem('vg_pending_share_url', playlist.url); } catch {}
      onNavigate('landing', '/');
    } else if (playlist.url) {
      navigator.clipboard.writeText(playlist.url).then(() => {
        setToast('Đã sao chép link vào clipboard');
        setTimeout(() => setToast(''), 2500);
      });
    }
  };

  // derive tier info from count
  const isPro = playlists.length > 3;
  const limit = isPro ? 20 : 3;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-surface flex items-center justify-center">
            <ListMusic className="w-5 h-5 text-accent-text" />
          </div>
          <div>
            <h1 className="text-fg font-bold text-lg">Playlists đã lưu</h1>
            <p className="text-fg-muted text-xs">Lưu link YouTube / Spotify để tải lại sau</p>
          </div>
        </div>
        <button
          onClick={fetchPlaylists}
          disabled={loading}
          className="p-2 rounded-lg text-fg-muted hover:text-fg hover:bg-surface-2 transition-colors disabled:opacity-50"
          aria-label="Tải lại danh sách"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* Save new playlist form */}
      <form onSubmit={handleSave} className="rounded-xl border border-line bg-surface-2 p-4 space-y-3">
        <p className="text-fg font-semibold text-sm">Lưu playlist mới</p>
        <div className="flex gap-2">
          <input
            type="url"
            value={saveUrl}
            onChange={e => setSaveUrl(e.target.value)}
            placeholder="https://youtube.com/playlist?list=..."
            className="flex-1 bg-surface-2 border border-line-strong rounded-lg px-3 py-2 text-sm text-fg placeholder:text-fg-muted focus:outline-none focus:border-accent/60 transition-colors"
          />
          <button
            type="submit"
            disabled={saving || !saveUrl.trim()}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-accent hover:bg-accent-hover text-accent-fg text-sm font-bold transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {saving
              ? <Loader2 className="w-4 h-4 animate-spin" />
              : <Plus className="w-4 h-4" />}
            Lưu
          </button>
        </div>
        {saveError && (
          <p className="text-danger text-xs font-semibold">{saveError}</p>
        )}
      </form>

      {/* Limit indicator */}
      <div className="flex items-center justify-between text-xs text-fg-muted">
        <span>
          <span className="text-fg font-bold">{playlists.length}</span>
          <span>/{limit}</span>
          <span className="ml-1.5 text-[10px] font-bold text-accent-text bg-accent-soft border border-accent/20 px-1.5 py-0.5 rounded-full">
            {isPro ? 'Pro' : 'Free'}
          </span>
        </span>
        <span>playlist đã lưu</span>
      </div>

      {/* Fetch error */}
      {fetchError && (
        <div className="rounded-lg border border-danger/30 bg-danger-soft px-4 py-3 text-danger text-sm">
          {fetchError}
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex justify-center py-12">
          <Loader2 className="w-6 h-6 text-accent-text animate-spin" />
        </div>
      )}

      {/* Empty state */}
      {!loading && !fetchError && playlists.length === 0 && (
        <div className="text-center py-14 space-y-2">
          <ListMusic className="w-10 h-10 text-fg-muted mx-auto" />
          <p className="text-fg-muted text-sm">Chưa có playlist nào.</p>
          <p className="text-fg-muted text-xs">Lưu link YouTube hoặc Spotify để tải lại sau.</p>
        </div>
      )}

      {/* Playlist cards */}
      {!loading && playlists.length > 0 && (
        <div className="space-y-3">
          {playlists.map(pl => {
            const badge = platformBadge(pl.platform);
            const isRefreshing = refreshing[pl.id];
            const isDeleting   = deleting[pl.id];
            const err          = cardError[pl.id];

            return (
              <div
                key={pl.id}
                className="rounded-xl border border-line bg-surface-2 p-4 space-y-3"
              >
                {/* Top row */}
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 space-y-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${badge.cls}`}>
                        {badge.label}
                      </span>
                      {pl.item_count != null && (
                        <span className="text-xs text-fg-muted">{pl.item_count} bài</span>
                      )}
                    </div>
                    <p className="text-fg font-semibold text-sm leading-snug truncate">
                      {pl.title || pl.url}
                    </p>
                    <p className="text-fg-muted text-xs">
                      Cập nhật: {formatDate(pl.last_refreshed_at || pl.updated_at)}
                    </p>
                  </div>
                  {pl.url && (
                    <a
                      href={pl.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex-shrink-0 p-1.5 rounded-lg text-fg-muted hover:text-fg-2 hover:bg-surface-2 transition-colors"
                      aria-label="Mở playlist"
                    >
                      <ExternalLink className="w-3.5 h-3.5" />
                    </a>
                  )}
                </div>

                {/* Error */}
                {err && (
                  <p className="text-danger text-xs font-semibold">{err}</p>
                )}

                {/* Actions */}
                <div className="flex items-center gap-2 flex-wrap">
                  <button
                    onClick={() => handleRefresh(pl.id)}
                    disabled={isRefreshing || isDeleting}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-2 text-fg-2 hover:bg-line text-xs font-bold transition-colors disabled:opacity-50"
                  >
                    {isRefreshing
                      ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      : <RefreshCw className="w-3.5 h-3.5" />}
                    Tải lại
                  </button>

                  <button
                    onClick={() => handleDownload(pl)}
                    disabled={isDeleting}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent-soft text-accent-text border border-accent/25 hover:bg-accent/20 text-xs font-bold transition-colors disabled:opacity-50"
                  >
                    <Play className="w-3.5 h-3.5" />
                    Tải xuống
                  </button>

                  <button
                    onClick={() => handleDelete(pl.id)}
                    disabled={isRefreshing || isDeleting}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-danger-soft text-danger border border-danger/25 hover:bg-danger/20 text-xs font-bold transition-colors disabled:opacity-50 ml-auto"
                  >
                    {isDeleting
                      ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      : <Trash2 className="w-3.5 h-3.5" />}
                    Xoá
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Toast */}
      {toast && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 px-4 py-2.5 rounded-xl bg-surface border border-line-strong text-fg text-sm font-semibold shadow-xl">
          {toast}
        </div>
      )}
    </div>
  );
}

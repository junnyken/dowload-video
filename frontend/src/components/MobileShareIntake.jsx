import { useState, useEffect, useRef } from 'react';
import { X, ChevronDown, ChevronUp } from 'lucide-react';

function detectPlatform(url) {
  if (!url) return { name: 'Unknown', emoji: '🌐', color: '#64748b' };
  const u = url.toLowerCase();
  if (u.includes('tiktok.com'))                          return { name: 'TikTok',     emoji: '🎵', color: '#ff0050' };
  if (u.includes('instagram.com'))                       return { name: 'Instagram',  emoji: '📸', color: '#e1306c' };
  if (u.includes('youtube.com') || u.includes('youtu.be')) return { name: 'YouTube', emoji: '▶️', color: '#ff0000' };
  if (u.includes('facebook.com') || u.includes('fb.watch')) return { name: 'Facebook', emoji: '🎬', color: '#1877f2' };
  if (u.includes('twitter.com') || u.includes('x.com')) return { name: 'X/Twitter', emoji: '🐦', color: '#1da1f2' };
  if (u.includes('spotify.com'))                         return { name: 'Spotify',    emoji: '🎵', color: '#1ed760' };
  if (u.includes('soundcloud.com'))                      return { name: 'SoundCloud', emoji: '🎶', color: '#ff5500' };
  return { name: 'Video', emoji: '🌐', color: '#64748b' };
}

function shimmerCls() {
  return 'bg-slate-700/50 animate-pulse rounded-lg';
}

export default function MobileShareIntake({ onNavigate }) {
  const [show, setShow]             = useState(false);
  const [url, setUrl]               = useState('');
  const [platform, setPlatform]     = useState(null);
  const [loading, setLoading]       = useState(false);
  const [videoInfo, setVideoInfo]   = useState(null);
  const [expanded, setExpanded]     = useState(false);
  const [visible, setVisible]       = useState(false); // for CSS animation
  const abortRef                    = useRef(null);

  useEffect(() => {
    const handler = (e) => {
      // Only intercept on mobile
      if (window.innerWidth >= 768) return;

      const sharedUrl = e.detail?.url || (typeof e.detail === 'string' ? e.detail : '');
      if (!sharedUrl) return;

      setUrl(sharedUrl);
      setPlatform(detectPlatform(sharedUrl));
      setVideoInfo(null);
      setExpanded(false);
      setShow(true);
      setTimeout(() => setVisible(true), 10); // trigger slide-up animation

      // Fetch metadata
      setLoading(true);
      if (abortRef.current) abortRef.current.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;

      fetch(`/api/v1/fetch-link?url=${encodeURIComponent(sharedUrl)}`, { signal: ctrl.signal })
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (data) setVideoInfo(data);
        })
        .catch(() => {})
        .finally(() => setLoading(false));
    };

    window.addEventListener('vidgrab:share-url', handler);
    return () => {
      window.removeEventListener('vidgrab:share-url', handler);
      if (abortRef.current) abortRef.current.abort();
    };
  }, []);

  const close = () => {
    setVisible(false);
    setTimeout(() => {
      setShow(false);
      setUrl('');
      setVideoInfo(null);
      setLoading(false);
    }, 300);
  };

  const goHome = () => {
    close();
    onNavigate('landing', '/');
  };

  const handleHD = () => {
    goHome();
  };

  const handleMP3 = () => {
    window.dispatchEvent(new CustomEvent('vidgrab:set-format:mp3'));
    goHome();
  };

  const handleNoWatermark = () => {
    window.dispatchEvent(new CustomEvent('vidgrab:set-nowatermark'));
    goHome();
  };

  const handleBatch = () => {
    if (url) {
      window.dispatchEvent(new CustomEvent(`vidgrab:add-to-batch:${url}`));
    }
    close();
  };

  if (!show) return null;

  const plat = platform || { name: 'Video', emoji: '🌐', color: '#64748b' };
  const title = videoInfo?.title || '';
  const thumbnail = videoInfo?.thumbnail || '';
  const duration = videoInfo?.duration || '';
  const quality = videoInfo?.formats?.[0]?.quality || '';

  const shortUrl = url.length > 50 ? url.slice(0, 47) + '...' : url;

  return (
    <>
      {/* Backdrop */}
      <div
        className={`fixed inset-0 z-[70] bg-black/60 transition-opacity duration-300 ${
          visible ? 'opacity-100' : 'opacity-0'
        }`}
        onClick={close}
        aria-hidden="true"
      />

      {/* Sheet */}
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Chia sẻ vào VidGrab"
        className={`fixed inset-x-0 bottom-0 z-[71] bg-[#0d2821]/98 backdrop-blur-xl rounded-t-3xl shadow-[0_-8px_40px_rgba(0,0,0,0.4)] transition-transform duration-300 ease-out ${
          visible ? 'translate-y-0' : 'translate-y-full'
        }`}
        style={{ maxHeight: '90vh', overflowY: 'auto' }}
      >
        {/* Drag handle */}
        <div className="flex justify-center pt-3 pb-1">
          <div className="w-10 h-1 rounded-full bg-slate-600/60" />
        </div>

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-700/40">
          <h2 className="text-slate-100 font-semibold text-base">Chia sẻ vào VidGrab</h2>
          <button
            onClick={close}
            className="w-8 h-8 flex items-center justify-center rounded-full bg-slate-700/40 text-slate-400 hover:text-slate-100 hover:bg-slate-700 transition-colors"
            aria-label="Đóng"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Platform + video preview */}
        <div className="px-5 pt-4 pb-3">
          {/* Platform pill */}
          <div
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold mb-3"
            style={{ backgroundColor: `${plat.color}20`, color: plat.color, border: `1px solid ${plat.color}40` }}
          >
            <span>{plat.emoji}</span>
            <span>{plat.name}</span>
          </div>

          {/* Thumbnail + meta */}
          {loading ? (
            <div className="flex gap-3">
              <div className={`w-20 h-14 flex-shrink-0 ${shimmerCls()}`} />
              <div className="flex-1 space-y-2 pt-1">
                <div className={`h-3.5 ${shimmerCls()} w-full`} />
                <div className={`h-3.5 ${shimmerCls()} w-2/3`} />
                <div className={`h-3 ${shimmerCls()} w-1/3`} />
              </div>
            </div>
          ) : videoInfo ? (
            <div className="flex gap-3">
              {thumbnail && (
                <img
                  src={thumbnail}
                  alt="thumbnail"
                  className="w-20 h-14 flex-shrink-0 rounded-xl object-cover bg-slate-700/40"
                  onError={(e) => { e.target.style.display = 'none'; }}
                />
              )}
              <div className="flex-1 min-w-0">
                <p className="text-slate-100 text-sm font-medium line-clamp-2 leading-snug">{title || 'Không có tiêu đề'}</p>
                <div className="flex items-center gap-2 mt-1 flex-wrap">
                  {duration && <span className="text-slate-400 text-[11px]">⏱ {duration}</span>}
                  {quality  && <span className="text-slate-400 text-[11px]">{quality}</span>}
                </div>
              </div>
            </div>
          ) : (
            <p className="text-slate-400 text-sm line-clamp-2 break-all">{shortUrl}</p>
          )}
        </div>

        {/* Primary actions */}
        <div className="px-5 pb-3 grid grid-cols-3 gap-2">
          <button
            onClick={handleHD}
            className="flex flex-col items-center gap-1.5 py-3 rounded-2xl bg-gradient-to-b from-[#FBBF24]/20 to-[#FBBF24]/10 border border-[#FBBF24]/30 text-[#FBBF24] hover:bg-[#FBBF24]/25 transition-colors active:scale-95"
          >
            <span className="text-xl">⬇️</span>
            <span className="text-[11px] font-semibold">Tải HD</span>
          </button>

          <button
            onClick={handleMP3}
            className="flex flex-col items-center gap-1.5 py-3 rounded-2xl bg-[#1a3a2a]/60 border border-slate-700/40 text-slate-200 hover:bg-slate-700/30 transition-colors active:scale-95"
          >
            <span className="text-xl">🎵</span>
            <span className="text-[11px] font-semibold">MP3</span>
          </button>

          <button
            onClick={handleNoWatermark}
            className="flex flex-col items-center gap-1.5 py-3 rounded-2xl bg-[#1a3a2a]/60 border border-slate-700/40 text-slate-200 hover:bg-slate-700/30 transition-colors active:scale-95"
          >
            <span className="text-xl">✨</span>
            <span className="text-[11px] font-semibold leading-tight text-center">Không watermark</span>
          </button>
        </div>

        {/* Secondary actions (expandable) */}
        <div className="px-5 pb-4">
          <button
            onClick={() => setExpanded((v) => !v)}
            className="w-full flex items-center justify-between py-2 text-slate-400 text-xs font-medium hover:text-slate-200 transition-colors"
          >
            <span>Thêm lựa chọn</span>
            {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
          </button>

          {expanded && (
            <div className="grid grid-cols-2 gap-2 mt-2">
              <button
                onClick={handleBatch}
                className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-[#1a3a2a]/60 border border-slate-700/40 text-slate-300 text-xs font-medium hover:bg-slate-700/30 transition-colors active:scale-95"
              >
                <span>📦</span> Thêm vào batch
              </button>
              <button
                onClick={close}
                className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-[#1a3a2a]/60 border border-slate-700/40 text-slate-400 text-xs font-medium hover:bg-slate-700/30 transition-colors active:scale-95"
              >
                <span>✕</span> Huỷ
              </button>
            </div>
          )}
        </div>

        {/* URL row */}
        <div className="px-5 pb-6 border-t border-slate-700/30 pt-3">
          <p className="text-slate-500 text-[11px] truncate">{shortUrl}</p>
        </div>
      </div>
    </>
  );
}

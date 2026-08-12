import { useState, useEffect } from 'react';
import { AlertTriangle, X, ChevronDown, ChevronUp } from 'lucide-react';

const API_BASE = import.meta.env.VITE_API_URL || '';

const PLATFORM_LABEL = {
  youtube: 'YouTube', tiktok: 'TikTok', facebook: 'Facebook',
  instagram: 'Instagram', twitter: 'Twitter/X', reddit: 'Reddit',
  bilibili: 'Bilibili', spotify: 'Spotify', soundcloud: 'SoundCloud',
  threads: 'Threads', douyin: 'Douyin', linkedin: 'LinkedIn',
};

export default function PlatformStatusBanner() {
  const [data, setData]       = useState(null);
  const [expanded, setExpanded] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function fetch_() {
      try {
        const r = await fetch(`${API_BASE}/api/v1/platform-status`);
        const j = await r.json();
        if (!cancelled) setData(j);
      } catch { /* silently ignore network errors */ }
    }
    fetch_();
    const id = setInterval(fetch_, 60_000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  if (!data || data.all_healthy || dismissed) return null;

  const degraded = (data.platforms || []).filter(p => p.status === 'degraded');
  const constrained = (data.platforms || []).filter(p => p.status === 'constrained');
  const issues = [...degraded, ...constrained];
  if (issues.length === 0) return null;

  const hasSevere = degraded.length > 0;

  return (
    <div className={`w-full max-w-3xl mb-4 rounded-2xl border text-sm ${
      hasSevere
        ? 'bg-red-50 border-red-200 text-red-800'
        : 'bg-amber-50 border-amber-200 text-amber-800'
    }`}>
      <div className="flex items-center gap-2 px-4 py-3">
        <AlertTriangle className={`w-4 h-4 flex-shrink-0 ${hasSevere ? 'text-red-500' : 'text-amber-500'}`} />
        <span className="flex-1 font-medium">
          {hasSevere
            ? `${degraded.length} nền tảng đang gặp sự cố`
            : `${constrained.length} nền tảng có thể chậm hơn bình thường`}
        </span>
        <button
          onClick={() => setExpanded(v => !v)}
          className="opacity-60 hover:opacity-100 transition-opacity p-0.5"
          aria-label="Toggle details"
        >
          {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
        </button>
        <button
          onClick={() => setDismissed(true)}
          className="opacity-60 hover:opacity-100 transition-opacity p-0.5"
          aria-label="Dismiss"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {expanded && (
        <div className="px-4 pb-3 flex flex-wrap gap-2">
          {issues.map(p => (
            <span
              key={p.platform}
              className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium ${
                p.status === 'degraded'
                  ? 'bg-red-100 text-red-700'
                  : 'bg-amber-100 text-amber-700'
              }`}
            >
              {PLATFORM_LABEL[p.platform] || p.platform}
              {p.status === 'degraded' && ' — đang lỗi'}
              {p.status === 'constrained' && ' — chậm'}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

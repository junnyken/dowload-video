import { useState, useEffect, useCallback } from 'react';
import { MonitorDown, Loader2, ChevronDown } from 'lucide-react';
import { API_BASE } from '../lib/apiBase';

const PAGE = 20;
// Server answers these when the feature is off / the table is missing; the
// section is then hidden entirely instead of showing an error.
const HIDE_CODES = ['client_api_disabled', 'storage_not_ready'];

function fmtSize(n) {
  if (n == null) return null;
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)} GB`;
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(n / 1024))} KB`;
}

function fmtDate(iso) {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return '';
  return d.toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export default function DesktopHistorySection({ session }) {
  const [items, setItems] = useState([]);
  const [next, setNext] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [hidden, setHidden] = useState(false);

  const token = session?.access_token;

  const load = useCallback(async (before) => {
    if (!token) return;
    setLoading(true);
    setError(false);
    try {
      const qs = new URLSearchParams({ limit: String(PAGE) });
      if (before) qs.set('before', before);
      const res = await fetch(`${API_BASE}/api/v1/client/history?${qs}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        let code = null;
        try { code = (await res.json())?.error_code; } catch { /* non-JSON body */ }
        if (res.status === 503 && HIDE_CODES.includes(code)) { setHidden(true); return; }
        throw new Error(`HTTP ${res.status}`);
      }
      const data = await res.json();
      setItems((prev) => (before ? [...prev, ...(data.items || [])] : (data.items || [])));
      setNext(data.nextBefore || null);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => { load(null); }, [load]);

  if (hidden || !token) return null;

  return (
    <section className="space-y-3 pt-4" aria-label="Tải bằng app Windows">
      <div className="flex items-center gap-2">
        <MonitorDown className="w-4 h-4 text-accent-text" />
        <h3 className="text-fg text-sm font-semibold">Tải bằng app Windows</h3>
      </div>

      {loading && items.length === 0 ? (
        <div className="flex justify-center py-8">
          <Loader2 className="w-5 h-5 text-accent-text animate-spin" />
        </div>
      ) : error && items.length === 0 ? (
        <div className="bg-surface-2 border border-line rounded-xl p-4 text-sm text-fg-muted flex items-center justify-between gap-3">
          <span>Không tải được lịch sử từ app Windows.</span>
          <button onClick={() => load(null)} className="text-accent-text hover:underline cursor-pointer">Thử lại</button>
        </div>
      ) : items.length === 0 ? (
        <p className="bg-surface-2 border border-line rounded-xl p-4 text-sm text-fg-muted text-center">
          Chưa có lượt tải nào từ app Windows
        </p>
      ) : (
        <div className="space-y-2">
          {items.map((it) => {
            const ok = it.state === 'completed';
            const size = fmtSize(it.fileSize);
            return (
              <div key={it.id || it.clientId} className="bg-surface-2 border border-line rounded-xl p-3 hover:border-line-strong transition-colors">
                <p className="text-fg text-sm font-medium truncate">{it.title || it.url}</p>
                <div className="flex items-center gap-2 mt-1 flex-wrap text-[11px] text-fg-muted">
                  <span className={ok ? 'text-success' : 'text-danger'}>{ok ? 'Hoàn tất' : 'Thất bại'}</span>
                  {it.platform && <span>{it.platform}</span>}
                  {it.formatLabel && <span>{it.formatLabel}</span>}
                  {size && <span>{size}</span>}
                  <span>{fmtDate(it.finishedAt || it.createdAt)}</span>
                </div>
              </div>
            );
          })}
          {error && <p className="text-xs text-danger">Không tải thêm được. Thử lại sau.</p>}
          {next && (
            <button
              onClick={() => load(next)}
              disabled={loading}
              className="w-full py-3 rounded-xl border border-line text-fg-muted hover:text-fg hover:border-line-strong transition-colors text-sm flex items-center justify-center gap-2 cursor-pointer disabled:opacity-60"
            >
              <ChevronDown className="w-4 h-4" />
              Xem thêm
            </button>
          )}
        </div>
      )}
    </section>
  );
}

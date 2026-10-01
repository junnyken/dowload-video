import { useState, useEffect, useCallback } from "react";
import { API_BASE } from '../../lib/apiBase';

const SOURCE_CONFIG = {
  auto_tuner: { label: "Auto-Tune", color: "bg-surface-2 text-fg-2" },
  playbook:   { label: "Playbook",  color: "bg-surface-2 text-fg-2" },
  anomaly:    { label: "Bất thường",color: "bg-accent-soft text-accent-text" },
  recovery:   { label: "Phục hồi", color: "bg-success-soft text-success" },
};

function timeAgo(iso) {
  if (!iso) return "";
  const diff = Math.floor((Date.now() - new Date(iso)) / 1000);
  if (diff < 60) return `${diff}s trước`;
  if (diff < 3600) return `${Math.floor(diff / 60)}p trước`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h trước`;
  return `${Math.floor(diff / 86400)}d trước`;
}

const PAGE_SIZE = 20;

export default function AutomationHistoryPanel({ adminToken }) {
  const [events, setEvents] = useState([]);
  const [filters, setFilters] = useState({ auto_tuner: true, playbook: true, anomaly: true, recovery: true });
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);

  const fetch_ = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(`${API_BASE}/api/v1/intelligence/automation-history`, { headers: { "X-Admin-Token": adminToken } });
      const d = await r.json();
      setEvents(d.events || []);
    } catch (_) {}
    setLoading(false);
  }, [adminToken]);

  useEffect(() => { fetch_(); }, [fetch_]);

  const filtered = events.filter((e) => filters[e.source] !== false);
  const pages = Math.ceil(filtered.length / PAGE_SIZE);
  const visible = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const downloadCsv = () => {
    const rows = [["Thời gian", "Nguồn", "Hành động", "Lý do", "Kết quả"], ...filtered.map((e) => [e.timestamp, e.source, e.action, e.reason, e.outcome])];
    const csv = rows.map((r) => r.map((c) => `"${(c || "").replace(/"/g, '""')}"`).join(",")).join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    a.download = "automation-history.csv";
    a.click();
  };

  return (
    <div className="bg-canvas border border-line rounded-xl p-5">
      <div className="flex items-center justify-between mb-4">
        <span className="text-base font-semibold text-fg">Lịch sử Tự động hóa</span>
        <div className="flex gap-2">
          <button onClick={fetch_} className="text-xs px-2 py-1 rounded bg-surface text-fg-muted hover:text-fg transition">Làm mới</button>
          <button onClick={downloadCsv} className="text-xs px-2 py-1 rounded bg-surface text-fg-muted hover:text-fg transition">CSV</button>
        </div>
      </div>

      <div className="flex gap-2 flex-wrap mb-3">
        {Object.entries(SOURCE_CONFIG).map(([k, v]) => (
          <button key={k} onClick={() => { setFilters((f) => ({ ...f, [k]: !f[k] })); setPage(0); }}
            className={`text-xs px-2.5 py-1 rounded-full border transition ${filters[k] ? v.color + " border-current" : "text-fg-muted border-line bg-transparent"}`}>
            {v.label}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="text-fg-muted text-sm text-center py-6">Đang tải...</div>
      ) : filtered.length === 0 ? (
        <div className="text-fg-muted text-sm text-center py-6">Không có lịch sử tự động hóa</div>
      ) : (
        <>
          <div className="space-y-1 max-h-80 overflow-y-auto pr-1">
            {visible.map((e, i) => {
              const cfg = SOURCE_CONFIG[e.source] || SOURCE_CONFIG.recovery;
              return (
                <div key={i} className="flex items-start gap-2.5 py-2 border-b border-line last:border-0">
                  <span className={`text-xs px-1.5 py-0.5 rounded font-medium shrink-0 mt-0.5 ${cfg.color}`}>{cfg.label}</span>
                  <div className="flex-1 min-w-0">
                    <div className="text-xs text-fg-2 truncate">{e.action}</div>
                    {e.reason && <div className="text-xs text-fg-muted truncate">{e.reason}</div>}
                  </div>
                  <div className="shrink-0 text-right">
                    <div className="text-xs text-fg-muted">{timeAgo(e.timestamp)}</div>
                    <div className={`text-xs ${e.outcome === "applied" || e.outcome === "success" ? "text-success" : e.outcome === "failed" ? "text-danger" : "text-fg-muted"}`}>
                      {e.outcome}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {pages > 1 && (
            <div className="flex items-center justify-between mt-3 pt-3 border-t border-line">
              <button onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}
                className="text-xs px-3 py-1 rounded bg-surface text-fg-muted hover:text-fg disabled:opacity-30 transition">
                ← Trước
              </button>
              <span className="text-xs text-fg-muted">{page + 1} / {pages}</span>
              <button onClick={() => setPage((p) => Math.min(pages - 1, p + 1))} disabled={page >= pages - 1}
                className="text-xs px-3 py-1 rounded bg-surface text-fg-muted hover:text-fg disabled:opacity-30 transition">
                Sau →
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

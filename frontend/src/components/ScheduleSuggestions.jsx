import { useState, useEffect, useCallback } from "react";
import { API_BASE } from '../lib/apiBase';

export default function ScheduleSuggestions({ authToken, onApplySuggestion }) {
  const [suggestions, setSuggestions] = useState([]);
  const [driftAlerts, setDriftAlerts] = useState([]);
  const [applying, setApplying] = useState(null);
  const [toast, setToast] = useState(null);

  const showToast = (msg) => { setToast(msg); setTimeout(() => setToast(null), 3000); };

  const fetch_ = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/v1/intelligence/schedule-suggestions`, {
        headers: authToken ? { Authorization: `Bearer ${authToken}` } : {},
      });
      const d = await r.json();
      setSuggestions(d.suggestions || []);
      setDriftAlerts(d.drift_alerts || []);
    } catch (_) {}
  }, [authToken]);

  useEffect(() => { fetch_(); }, [fetch_]);

  const dismiss = async (jobId) => {
    try {
      await fetch(`${API_BASE}/api/v1/intelligence/schedule-suggestions/${jobId}/dismiss`, {
        method: "POST",
        headers: authToken ? { Authorization: `Bearer ${authToken}` } : {},
      });
      setSuggestions((s) => s.filter((x) => x.job_id !== jobId));
    } catch (_) {}
  };

  const apply = async (s) => {
    setApplying(s.job_id);
    try {
      if (onApplySuggestion) onApplySuggestion(s.job_id, s.suggested_cron);
      await fetch(`${API_BASE}/api/v1/intelligence/schedule-suggestions/${s.job_id}/dismiss`, {
        method: "POST",
        headers: authToken ? { Authorization: `Bearer ${authToken}` } : {},
      });
      setSuggestions((prev) => prev.filter((x) => x.job_id !== s.job_id));
      showToast("✓ Đã áp dụng lịch mới");
    } catch (_) {}
    setApplying(null);
  };

  if (suggestions.length === 0 && driftAlerts.length === 0) return null;

  return (
    <div className="space-y-3 mb-4">
      {toast && (
        <div className="fixed bottom-4 right-4 z-50 px-4 py-2 rounded-lg bg-green-600 text-white text-sm shadow-lg">{toast}</div>
      )}

      {driftAlerts.length > 0 && (
        <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-lg px-4 py-2.5 text-sm text-yellow-300">
          ⚠️ {driftAlerts.length} job đang bị trễ — hàng đợi có thể đang bận
        </div>
      )}

      {suggestions.length > 0 && (
        <div className="bg-zinc-900/60 border border-zinc-800 rounded-xl p-4">
          <div className="text-sm font-medium text-white mb-3">📅 Gợi ý lịch biểu tối ưu</div>
          <div className="space-y-3">
            {suggestions.map((s) => (
              <div key={s.job_id} className="bg-zinc-800/50 rounded-lg p-3">
                <div className="flex items-start justify-between gap-2 mb-2">
                  <div>
                    <div className="text-xs text-zinc-300 font-mono">
                      <span className="text-zinc-500">{s.current_cron}</span>
                      {" → "}
                      <span className="text-green-300">{s.suggested_cron}</span>
                    </div>
                    <div className="text-xs text-zinc-500 mt-0.5">{s.reason}</div>
                  </div>
                  <div className="flex gap-1.5 shrink-0">
                    <button onClick={() => apply(s)} disabled={applying === s.job_id}
                      className="text-xs px-2.5 py-1 rounded bg-green-600/30 text-green-300 hover:bg-green-600/50 transition disabled:opacity-50">
                      {applying === s.job_id ? "..." : "Áp dụng"}
                    </button>
                    <button onClick={() => dismiss(s.job_id)} className="text-xs px-2.5 py-1 rounded bg-zinc-700 text-zinc-400 hover:text-white transition">
                      Bỏ qua
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

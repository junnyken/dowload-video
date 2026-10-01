import { useState, useEffect, useCallback } from "react";
import { API_BASE } from '../../lib/apiBase';

function timeAgo(iso) {
  if (!iso) return "";
  const diff = Math.floor((Date.now() - new Date(iso)) / 1000);
  if (diff < 60) return `${diff}s trước`;
  if (diff < 3600) return `${Math.floor(diff / 60)}p trước`;
  return `${Math.floor(diff / 3600)}h trước`;
}

const CONFIDENCE_CONFIG = {
  high:   "bg-success-soft text-success border-success/30",
  medium: "bg-accent-soft text-accent-text border-accent/30",
  low:    "bg-line text-fg-2 border-line-strong",
};

const ACTION_LABELS = {
  refresh_cookies:       "Làm mới cookie",
  lower_concurrency:     "Giảm concurrency",
  pause_low_priority:    "Tạm dừng job thấp",
  rotate_fallback_order: "Xoay proxy fallback",
  trigger_cleanup:       "Dọn dẹp ngay",
};

export default function PlaybooksPanel({ adminToken }) {
  const [tab, setTab] = useState("playbooks");
  const [playbooks, setPlaybooks] = useState([]);
  const [history, setHistory] = useState([]);
  const [expanded, setExpanded] = useState(null);
  const [executing, setExecuting] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [toast, setToast] = useState(null);

  const showToast = (msg, ok = true) => {
    setToast({ msg, ok });
    setTimeout(() => setToast(null), 3000);
  };

  const fetchPlaybooks = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/v1/intelligence/playbooks`, { headers: { "X-Admin-Token": adminToken } });
      const d = await r.json();
      setPlaybooks(d.playbooks || []);
    } catch (_) {}
  }, [adminToken]);

  const fetchHistory = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/v1/intelligence/playbooks/history`, { headers: { "X-Admin-Token": adminToken } });
      const d = await r.json();
      setHistory((d.history || []).slice(0, 20));
    } catch (_) {}
  }, [adminToken]);

  useEffect(() => { fetchPlaybooks(); fetchHistory(); }, [fetchPlaybooks, fetchHistory]);

  const execute = async (action, params = {}) => {
    setConfirm(null);
    setExecuting(action);
    try {
      const r = await fetch(`${API_BASE}/api/v1/intelligence/playbooks/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Admin-Token": adminToken },
        body: JSON.stringify({ action, params }),
      });
      const d = await r.json();
      if (d.success) { showToast(`✓ ${d.message}`); fetchHistory(); }
      else showToast(`✗ ${d.message}`, false);
    } catch (e) {
      showToast(`✗ Lỗi: ${e.message}`, false);
    }
    setExecuting(null);
  };

  return (
    <div className="bg-canvas border border-line rounded-xl p-5">
      {toast && (
        <div className={`fixed bottom-4 right-4 z-50 px-4 py-2 rounded-lg text-sm font-medium shadow-lg transition ${toast.ok ? "bg-success text-success-fg" : "bg-danger text-danger-fg"}`}>
          {toast.msg}
        </div>
      )}

      {confirm && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60">
          <div className="bg-surface border border-line rounded-xl p-5 max-w-sm w-full mx-4">
            <div className="text-fg font-semibold mb-2">Xác nhận thực hiện</div>
            <div className="text-fg-muted text-sm mb-4">Bạn có chắc muốn thực hiện: <span className="text-fg">{ACTION_LABELS[confirm] || confirm}</span>?</div>
            <div className="flex gap-2">
              <button onClick={() => execute(confirm)} className="flex-1 py-2 bg-accent hover:bg-accent-hover text-accent-fg rounded-lg text-sm font-medium transition">Xác nhận</button>
              <button onClick={() => setConfirm(null)} className="flex-1 py-2 bg-surface-2 hover:bg-line text-fg rounded-lg text-sm transition">Hủy</button>
            </div>
          </div>
        </div>
      )}

      <div className="flex items-center justify-between mb-4">
        <span className="text-base font-semibold text-fg">Recovery Playbooks</span>
        <div className="flex gap-1">
          {["playbooks", "history"].map((t) => (
            <button key={t} onClick={() => setTab(t)}
              className={`text-xs px-3 py-1.5 rounded-lg transition ${tab === t ? "bg-accent text-accent-fg" : "bg-surface text-fg-muted hover:text-accent-fg"}`}>
              {t === "playbooks" ? "Playbooks" : "Lịch sử"}
            </button>
          ))}
        </div>
      </div>

      {tab === "playbooks" && (
        <div className="space-y-3">
          {playbooks.length === 0 ? (
            <div className="text-fg-muted text-sm text-center py-6">Không có playbook</div>
          ) : playbooks.map((pb) => (
            <div key={pb.id} className={`rounded-lg border p-4 ${pb.is_active ? "border-accent/40 bg-accent-soft" : "border-line bg-surface-2"}`}>
              <div className="flex items-start justify-between gap-2">
                <div className="flex-1">
                  <div className="flex items-center gap-2 flex-wrap mb-1">
                    <span className="text-sm font-medium text-fg">{pb.name}</span>
                    {pb.is_active && <span className="text-xs px-2 py-0.5 rounded-full bg-accent-soft text-accent-text border border-accent/30">Đang kích hoạt</span>}
                    <span className={`text-xs px-2 py-0.5 rounded-full border ${CONFIDENCE_CONFIG[pb.confidence] || CONFIDENCE_CONFIG.low}`}>
                      {pb.confidence === "high" ? "Cao" : pb.confidence === "medium" ? "Trung bình" : "Thấp"}
                    </span>
                  </div>
                  <div className="text-xs text-fg-muted">{pb.description}</div>
                </div>
                <button onClick={() => setExpanded(expanded === pb.id ? null : pb.id)}
                  className="shrink-0 text-xs px-2 py-1 rounded bg-surface-2 text-fg-2 hover:bg-line transition">
                  {expanded === pb.id ? "Thu" : "Xem"}
                </button>
              </div>

              {expanded === pb.id && (
                <div className="mt-3 pt-3 border-t border-line space-y-3">
                  <div>
                    <div className="text-xs text-fg-muted font-medium mb-1.5">Bước thực hiện thủ công:</div>
                    <ol className="space-y-1">
                      {(pb.manual_steps || []).map((s, i) => (
                        <li key={i} className="text-xs text-fg-2 flex gap-2">
                          <span className="text-fg-muted shrink-0">{i + 1}.</span>{s}
                        </li>
                      ))}
                    </ol>
                  </div>
                  {(pb.auto_actions || []).length > 0 && (
                    <div>
                      <div className="text-xs text-fg-muted font-medium mb-1.5">Hành động an toàn:</div>
                      <div className="flex flex-wrap gap-2">
                        {pb.auto_actions.map((a) => (
                          <button key={a} onClick={() => setConfirm(a)} disabled={executing === a}
                            className="text-xs px-3 py-1.5 rounded-lg bg-surface-2 text-fg-2 border border-line hover:bg-line transition disabled:opacity-50">
                            {executing === a ? "..." : ACTION_LABELS[a] || a}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {tab === "history" && (
        <div className="space-y-2">
          {history.length === 0 ? (
            <div className="text-fg-muted text-sm text-center py-6">Chưa có lịch sử</div>
          ) : history.map((h, i) => (
            <div key={i} className="flex items-center gap-3 py-2 border-b border-line last:border-0">
              <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${h.result?.success ? "bg-success" : "bg-danger"}`} />
              <div className="flex-1 min-w-0">
                <div className="text-xs text-fg truncate">{ACTION_LABELS[h.action] || h.action}</div>
                <div className="text-xs text-fg-muted">{h.result?.message}</div>
              </div>
              <span className="text-xs text-fg-muted shrink-0">{timeAgo(h.timestamp)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

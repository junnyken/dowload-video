import { useState, useEffect, useCallback } from "react";

function timeAgo(iso) {
  if (!iso) return "";
  const diff = Math.floor((Date.now() - new Date(iso)) / 1000);
  if (diff < 60) return `${diff}s trước`;
  if (diff < 3600) return `${Math.floor(diff / 60)}p trước`;
  return `${Math.floor(diff / 3600)}h trước`;
}

const CONFIDENCE_CONFIG = {
  high:   "bg-green-500/20 text-green-300 border-green-500/30",
  medium: "bg-yellow-500/20 text-yellow-300 border-yellow-500/30",
  low:    "bg-zinc-600/40 text-zinc-300 border-zinc-600",
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
      const r = await fetch("/api/v1/intelligence/playbooks", { headers: { "X-Admin-Token": adminToken } });
      const d = await r.json();
      setPlaybooks(d.playbooks || []);
    } catch (_) {}
  }, [adminToken]);

  const fetchHistory = useCallback(async () => {
    try {
      const r = await fetch("/api/v1/intelligence/playbooks/history", { headers: { "X-Admin-Token": adminToken } });
      const d = await r.json();
      setHistory((d.history || []).slice(0, 20));
    } catch (_) {}
  }, [adminToken]);

  useEffect(() => { fetchPlaybooks(); fetchHistory(); }, [fetchPlaybooks, fetchHistory]);

  const execute = async (action, params = {}) => {
    setConfirm(null);
    setExecuting(action);
    try {
      const r = await fetch("/api/v1/intelligence/playbooks/execute", {
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
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
      {toast && (
        <div className={`fixed bottom-4 right-4 z-50 px-4 py-2 rounded-lg text-sm font-medium shadow-lg transition ${toast.ok ? "bg-green-600 text-white" : "bg-red-600 text-white"}`}>
          {toast.msg}
        </div>
      )}

      {confirm && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60">
          <div className="bg-zinc-800 border border-zinc-700 rounded-xl p-5 max-w-sm w-full mx-4">
            <div className="text-white font-semibold mb-2">Xác nhận thực hiện</div>
            <div className="text-zinc-400 text-sm mb-4">Bạn có chắc muốn thực hiện: <span className="text-white">{ACTION_LABELS[confirm] || confirm}</span>?</div>
            <div className="flex gap-2">
              <button onClick={() => execute(confirm)} className="flex-1 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium transition">Xác nhận</button>
              <button onClick={() => setConfirm(null)} className="flex-1 py-2 bg-zinc-700 hover:bg-zinc-600 text-white rounded-lg text-sm transition">Hủy</button>
            </div>
          </div>
        </div>
      )}

      <div className="flex items-center justify-between mb-4">
        <span className="text-base font-semibold text-white">Recovery Playbooks</span>
        <div className="flex gap-1">
          {["playbooks", "history"].map((t) => (
            <button key={t} onClick={() => setTab(t)}
              className={`text-xs px-3 py-1.5 rounded-lg transition ${tab === t ? "bg-blue-600 text-white" : "bg-zinc-800 text-zinc-400 hover:text-white"}`}>
              {t === "playbooks" ? "Playbooks" : "Lịch sử"}
            </button>
          ))}
        </div>
      </div>

      {tab === "playbooks" && (
        <div className="space-y-3">
          {playbooks.length === 0 ? (
            <div className="text-zinc-500 text-sm text-center py-6">Không có playbook</div>
          ) : playbooks.map((pb) => (
            <div key={pb.id} className={`rounded-lg border p-4 ${pb.is_active ? "border-orange-500/40 bg-orange-500/5" : "border-zinc-700/50 bg-zinc-800/40"}`}>
              <div className="flex items-start justify-between gap-2">
                <div className="flex-1">
                  <div className="flex items-center gap-2 flex-wrap mb-1">
                    <span className="text-sm font-medium text-white">{pb.name}</span>
                    {pb.is_active && <span className="text-xs px-2 py-0.5 rounded-full bg-orange-500/20 text-orange-300 border border-orange-500/30">Đang kích hoạt</span>}
                    <span className={`text-xs px-2 py-0.5 rounded-full border ${CONFIDENCE_CONFIG[pb.confidence] || CONFIDENCE_CONFIG.low}`}>
                      {pb.confidence === "high" ? "Cao" : pb.confidence === "medium" ? "Trung bình" : "Thấp"}
                    </span>
                  </div>
                  <div className="text-xs text-zinc-400">{pb.description}</div>
                </div>
                <button onClick={() => setExpanded(expanded === pb.id ? null : pb.id)}
                  className="shrink-0 text-xs px-2 py-1 rounded bg-zinc-700 text-zinc-300 hover:bg-zinc-600 transition">
                  {expanded === pb.id ? "Thu" : "Xem"}
                </button>
              </div>

              {expanded === pb.id && (
                <div className="mt-3 pt-3 border-t border-zinc-700/50 space-y-3">
                  <div>
                    <div className="text-xs text-zinc-500 font-medium mb-1.5">Bước thực hiện thủ công:</div>
                    <ol className="space-y-1">
                      {(pb.manual_steps || []).map((s, i) => (
                        <li key={i} className="text-xs text-zinc-300 flex gap-2">
                          <span className="text-zinc-600 shrink-0">{i + 1}.</span>{s}
                        </li>
                      ))}
                    </ol>
                  </div>
                  {(pb.auto_actions || []).length > 0 && (
                    <div>
                      <div className="text-xs text-zinc-500 font-medium mb-1.5">Hành động an toàn:</div>
                      <div className="flex flex-wrap gap-2">
                        {pb.auto_actions.map((a) => (
                          <button key={a} onClick={() => setConfirm(a)} disabled={executing === a}
                            className="text-xs px-3 py-1.5 rounded-lg bg-blue-600/20 text-blue-300 border border-blue-600/30 hover:bg-blue-600/30 transition disabled:opacity-50">
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
            <div className="text-zinc-500 text-sm text-center py-6">Chưa có lịch sử</div>
          ) : history.map((h, i) => (
            <div key={i} className="flex items-center gap-3 py-2 border-b border-zinc-800 last:border-0">
              <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${h.result?.success ? "bg-green-500" : "bg-red-500"}`} />
              <div className="flex-1 min-w-0">
                <div className="text-xs text-white truncate">{ACTION_LABELS[h.action] || h.action}</div>
                <div className="text-xs text-zinc-500">{h.result?.message}</div>
              </div>
              <span className="text-xs text-zinc-500 shrink-0">{timeAgo(h.timestamp)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

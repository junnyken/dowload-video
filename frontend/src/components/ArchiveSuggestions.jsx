import { useState, useEffect, useCallback } from "react";
import { API_BASE } from '../lib/apiBase';

export default function ArchiveSuggestions({ authToken }) {
  const [tagSuggestions, setTagSuggestions] = useState([]);
  const [duplicates, setDuplicates] = useState([]);
  const [dupModal, setDupModal] = useState(null);
  const [accepting, setAccepting] = useState(null);
  const [toast, setToast] = useState(null);
  const [tagsExpanded, setTagsExpanded] = useState(false);
  const [dupExpanded, setDupExpanded] = useState(false);

  const showToast = (msg) => { setToast(msg); setTimeout(() => setToast(null), 3000); };

  const fetch_ = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/v1/intelligence/archive-suggestions`, {
        headers: authToken ? { Authorization: `Bearer ${authToken}` } : {},
      });
      const d = await r.json();
      setTagSuggestions(d.tag_suggestions || []);
      setDuplicates(d.duplicates || []);
    } catch (_) {}
  }, [authToken]);

  useEffect(() => { fetch_(); }, [fetch_]);

  const acceptTags = async (item) => {
    setAccepting(item.item_id);
    try {
      await fetch(`${API_BASE}/api/v1/intelligence/archive-suggestions/${item.item_id}/accept-tags`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}) },
        body: JSON.stringify({ tags: item.suggested_tags }),
      });
      setTagSuggestions((s) => s.filter((x) => x.item_id !== item.item_id));
      showToast(`✓ Đã thêm ${item.suggested_tags.length} tag`);
    } catch (_) {}
    setAccepting(null);
  };

  const dismissTag = async (itemId) => {
    try {
      await fetch(`${API_BASE}/api/v1/intelligence/archive-suggestions/${itemId}/dismiss`, {
        method: "POST",
        headers: authToken ? { Authorization: `Bearer ${authToken}` } : {},
      });
      setTagSuggestions((s) => s.filter((x) => x.item_id !== itemId));
    } catch (_) {}
  };

  if (tagSuggestions.length === 0 && duplicates.length === 0) return null;

  const visibleTags = tagsExpanded ? tagSuggestions : tagSuggestions.slice(0, 3);
  const visibleDups = dupExpanded ? duplicates : duplicates.slice(0, 3);

  return (
    <div className="space-y-3 mb-4">
      {toast && (
        <div className="fixed bottom-4 right-4 z-50 px-4 py-2 rounded-lg bg-green-600 text-white text-sm shadow-lg">
          {toast}
        </div>
      )}

      {dupModal && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60">
          <div className="bg-zinc-800 border border-zinc-700 rounded-xl p-5 max-w-md w-full mx-4">
            <div className="text-white font-semibold mb-3">Có thể trùng lặp</div>
            <div className="text-zinc-400 text-sm mb-3">"{dupModal.title}"</div>
            <div className="space-y-2">
              {(dupModal.items || []).map((it, i) => (
                <div key={i} className="bg-zinc-700/50 rounded p-2 text-sm">
                  <div className="text-white">{it.title}</div>
                  <div className="text-zinc-500 text-xs">{it.created_at ? new Date(it.created_at).toLocaleString("vi-VN") : ""}</div>
                </div>
              ))}
            </div>
            <button onClick={() => setDupModal(null)} className="mt-4 w-full py-2 rounded-lg bg-zinc-700 text-white text-sm hover:bg-zinc-600 transition">Đóng</button>
          </div>
        </div>
      )}

      {tagSuggestions.length > 0 && (
        <div className="bg-zinc-900/60 border border-zinc-800 rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm font-medium text-white">💡 Gợi ý tag tự động</span>
            <span className="text-xs text-zinc-500">{tagSuggestions.length} mục</span>
          </div>
          <div className="space-y-2">
            {visibleTags.map((s) => (
              <div key={s.item_id} className="flex items-center gap-2 flex-wrap">
                <span className="text-xs text-zinc-400 shrink-0 max-w-[120px] truncate" title={s.title}>{s.title}</span>
                <div className="flex gap-1 flex-wrap flex-1">
                  {(s.suggested_tags || []).map((t) => (
                    <span key={t} className="px-2 py-0.5 rounded-full bg-blue-500/15 text-blue-300 text-xs border border-blue-500/20">{t}</span>
                  ))}
                </div>
                <button onClick={() => acceptTags(s)} disabled={accepting === s.item_id}
                  className="text-xs px-2 py-1 rounded bg-blue-600/30 text-blue-300 hover:bg-blue-600/50 transition disabled:opacity-50 shrink-0">
                  {accepting === s.item_id ? "..." : "Áp dụng"}
                </button>
                <button onClick={() => dismissTag(s.item_id)} className="text-xs text-zinc-600 hover:text-zinc-400 transition shrink-0">✕</button>
              </div>
            ))}
          </div>
          {tagSuggestions.length > 3 && (
            <button onClick={() => setTagsExpanded(!tagsExpanded)} className="mt-2 text-xs text-zinc-500 hover:text-zinc-300 transition">
              {tagsExpanded ? "Thu gọn" : `Xem thêm ${tagSuggestions.length - 3} mục`}
            </button>
          )}
        </div>
      )}

      {duplicates.length > 0 && (
        <div className="bg-zinc-900/60 border border-zinc-800 rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm font-medium text-white">🔍 Có thể trùng lặp</span>
            <span className="text-xs text-zinc-500">{duplicates.length} nhóm</span>
          </div>
          <div className="space-y-2">
            {visibleDups.map((d, i) => (
              <div key={i} className="flex items-center gap-2">
                <span className="text-xs text-zinc-400 flex-1 truncate">{d.title}</span>
                <span className="text-xs px-1.5 py-0.5 rounded bg-yellow-500/20 text-yellow-300">{(d.items || []).length} bản</span>
                <button onClick={() => setDupModal(d)} className="text-xs px-2 py-1 rounded bg-zinc-800 text-zinc-300 hover:bg-zinc-700 transition">Xem</button>
                <button onClick={() => setDuplicates((prev) => prev.filter((_, j) => j !== i))} className="text-xs text-zinc-600 hover:text-zinc-400 transition">✕</button>
              </div>
            ))}
          </div>
          {duplicates.length > 3 && (
            <button onClick={() => setDupExpanded(!dupExpanded)} className="mt-2 text-xs text-zinc-500 hover:text-zinc-300 transition">
              {dupExpanded ? "Thu gọn" : `Xem thêm ${duplicates.length - 3} nhóm`}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

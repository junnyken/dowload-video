// SmartActionsPanel — shows after fetch-link completes
// Tabs: Trim | Highlights | GIF | Metadata
// Each tab shows AI suggestions + action buttons
// Default collapsed to not overwhelm the main download UI

import { useState, useEffect, useCallback, useRef } from "react";

const API_BASE = "/api/v1";
const POLL_INTERVAL_MS = 2000;
const POLL_MAX_ATTEMPTS = 30; // ~60s timeout

// ---------------------------------------------------------------------------
// Utility helpers
// ---------------------------------------------------------------------------

function fmtSeconds(s) {
  if (s == null) return "—";
  const m = Math.floor(s / 60);
  const sec = Math.round(s % 60);
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}

function fmtRange(start, end) {
  return `${fmtSeconds(start)} → ${fmtSeconds(end)}`;
}

function ConfidenceBadge({ value }) {
  if (value == null) return null;
  const pct = Math.round(value * 100);
  const color =
    value >= 0.7
      ? "bg-green-100 text-green-700"
      : value >= 0.4
      ? "bg-yellow-100 text-yellow-700"
      : "bg-gray-100 text-gray-500";
  return (
    <span className={`text-xs font-medium px-1.5 py-0.5 rounded ${color}`}>
      {pct}%
    </span>
  );
}

function ScoreBar({ label, value, color = "bg-blue-400" }) {
  const pct = Math.min(100, Math.round((value || 0) * 100));
  return (
    <div className="flex items-center gap-2 text-xs text-gray-500">
      <span className="w-20 shrink-0">{label}</span>
      <div className="flex-1 h-1.5 bg-gray-200 rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full`} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-8 text-right">{pct}%</span>
    </div>
  );
}

function LoadingSkeleton() {
  return (
    <div className="space-y-3 animate-pulse p-3">
      {[1, 2, 3].map((i) => (
        <div key={i} className="h-12 bg-gray-100 rounded-lg" />
      ))}
    </div>
  );
}

function ProGate({ feature }) {
  return (
    <div className="flex flex-col items-center justify-center py-6 text-center">
      <span className="text-2xl mb-2">🔒</span>
      <p className="text-sm font-medium text-gray-700">
        Nâng cấp Pro để xem {feature}
      </p>
      <a
        href="/pricing"
        className="mt-3 text-xs text-blue-600 underline hover:text-blue-800"
      >
        Xem gói Pro →
      </a>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Trim
// ---------------------------------------------------------------------------

function TrimTab({ suggestions, onApplyTrim }) {
  if (!suggestions || suggestions.length === 0) {
    return (
      <p className="text-xs text-gray-400 py-4 text-center">
        Không có gợi ý cắt nào.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      {suggestions.map((s, i) => (
        <div key={i} className="border border-gray-100 rounded-lg p-3 bg-gray-50">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-xs font-semibold text-gray-700">
              {s.mode_label || s.mode || `Gợi ý ${i + 1}`}
            </span>
            <ConfidenceBadge value={s.confidence} />
          </div>
          <div className="text-sm font-mono text-gray-800 mb-2">
            {fmtRange(s.suggested_start, s.suggested_end)}
          </div>
          {s.reasons && s.reasons.length > 0 && (
            <div className="flex flex-wrap gap-1 mb-2">
              {s.reasons.map((r, ri) => (
                <span
                  key={ri}
                  className="text-xs bg-white border border-gray-200 text-gray-500 px-1.5 py-0.5 rounded"
                >
                  {r}
                </span>
              ))}
            </div>
          )}
          <button
            onClick={() => onApplyTrim({ start: s.suggested_start, end: s.suggested_end })}
            className="w-full text-xs font-medium bg-blue-600 hover:bg-blue-700 text-white py-1.5 rounded transition-colors"
          >
            Áp dụng gợi ý này
          </button>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Highlights (Clips)
// ---------------------------------------------------------------------------

function HighlightsTab({ suggestions, onQueueClips, tierFiltered }) {
  if (tierFiltered && (!suggestions || suggestions.length === 0)) {
    return <ProGate feature="highlights" />;
  }

  if (!suggestions || suggestions.length === 0) {
    return (
      <p className="text-xs text-gray-400 py-4 text-center">
        Không phát hiện highlight nào.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      {suggestions.map((s, i) => (
        <div key={i} className="border border-gray-100 rounded-lg p-3 bg-gray-50">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-xs font-semibold text-gray-700">
              {s.label || `Highlight ${i + 1}`}
            </span>
            <div className="flex items-center gap-1.5">
              {s.duration != null && (
                <span className="text-xs bg-blue-50 text-blue-600 border border-blue-100 px-1.5 py-0.5 rounded">
                  {fmtSeconds(s.duration)}
                </span>
              )}
              <ConfidenceBadge value={s.confidence} />
            </div>
          </div>
          <div className="text-sm font-mono text-gray-800 mb-1.5">
            {fmtRange(s.start, s.end)}
          </div>
          {s.reason && (
            <p className="text-xs text-gray-500 mb-2">{s.reason}</p>
          )}
          <div className="flex gap-2">
            <button
              onClick={() => {
                const ts = Math.floor(s.start || 0);
                window.open(`${window.location.origin}?t=${ts}`, "_blank");
              }}
              className="flex-1 text-xs border border-gray-300 text-gray-600 hover:bg-gray-100 py-1.5 rounded transition-colors"
            >
              Xem đoạn này
            </button>
            <button
              onClick={() => onQueueClips([i])}
              className="flex-1 text-xs font-medium bg-blue-600 hover:bg-blue-700 text-white py-1.5 rounded transition-colors"
            >
              Cắt clip
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: GIF
// ---------------------------------------------------------------------------

function GifTab({ suggestions, onApplyGif }) {
  if (!suggestions || suggestions.length === 0) {
    return (
      <p className="text-xs text-gray-400 py-4 text-center">
        Không tìm thấy đoạn phù hợp để tạo GIF.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      {suggestions.map((s, i) => (
        <div key={i} className="border border-gray-100 rounded-lg p-3 bg-gray-50">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-sm font-mono text-gray-800">
              {fmtRange(s.start, s.end)}
            </span>
            {s.has_scene_cut && (
              <span className="text-xs bg-amber-50 text-amber-600 border border-amber-200 px-1.5 py-0.5 rounded">
                ⚠ cắt cảnh
              </span>
            )}
          </div>
          <div className="space-y-1.5 mb-3">
            <ScoreBar label="Loop score" value={s.loop_score} color="bg-purple-400" />
            <ScoreBar label="Motion" value={s.motion_score} color="bg-blue-400" />
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => onApplyGif({ index: i, quality: "480p" })}
              className="flex-1 text-xs border border-gray-300 text-gray-600 hover:bg-gray-100 py-1.5 rounded transition-colors"
            >
              GIF 480p
            </button>
            <button
              onClick={() => onApplyGif({ index: i, quality: "720p" })}
              className="flex-1 text-xs font-medium bg-purple-600 hover:bg-purple-700 text-white py-1.5 rounded transition-colors"
            >
              GIF 720p
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Metadata
// ---------------------------------------------------------------------------

function MetadataTab({ suggestions }) {
  const [copied, setCopied] = useState(false);

  if (!suggestions || (!suggestions.filename_cleaned && !suggestions.tags)) {
    return (
      <p className="text-xs text-gray-400 py-4 text-center">
        Không có gợi ý metadata.
      </p>
    );
  }

  const { original_filename, filename_cleaned, tags = [], confidence } = suggestions;

  function handleCopy() {
    if (filename_cleaned) {
      navigator.clipboard.writeText(filename_cleaned).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1800);
      });
    }
  }

  return (
    <div className="space-y-3">
      {filename_cleaned && (
        <div className="border border-gray-100 rounded-lg p-3 bg-gray-50">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-xs font-semibold text-gray-600">Tên file gợi ý</span>
            <ConfidenceBadge value={confidence} />
          </div>
          {original_filename && original_filename !== filename_cleaned && (
            <p className="text-xs text-red-400 line-through mb-1 break-all">
              {original_filename}
            </p>
          )}
          <p className="text-xs text-green-700 font-medium break-all mb-2">
            {filename_cleaned}
          </p>
          <div className="flex gap-2">
            <button
              onClick={handleCopy}
              className="flex-1 text-xs border border-gray-300 text-gray-600 hover:bg-gray-100 py-1.5 rounded transition-colors"
            >
              {copied ? "Đã sao chép ✓" : "Sao chép"}
            </button>
            <button
              onClick={() => {
                window.dispatchEvent(
                  new CustomEvent("vidgrab:set-filename", { detail: filename_cleaned })
                );
              }}
              className="flex-1 text-xs font-medium bg-gray-700 hover:bg-gray-800 text-white py-1.5 rounded transition-colors"
            >
              Dùng tên này
            </button>
          </div>
        </div>
      )}

      {tags && tags.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-gray-600 mb-1.5">Tags</p>
          <div className="flex flex-wrap gap-1">
            {tags.map((tag, i) => (
              <span
                key={i}
                className="text-xs bg-blue-50 text-blue-600 border border-blue-100 px-2 py-0.5 rounded-full"
              >
                {tag}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const TAB_CONFIG = [
  { id: "trim", label: "✂️ Trim", key: "trim_suggestions" },
  { id: "clips", label: "⚡ Highlights", key: "clip_suggestions" },
  { id: "gif", label: "🎞️ GIF", key: "gif_suggestions" },
  { id: "metadata", label: "🏷️ Metadata", key: "metadata_suggestions" },
];

export default function SmartActionsPanel({
  videoInfo,
  mediaUrl,
  onApplyTrim,
  onApplyGif,
  onQueueClips,
  hidePoweredBy = false,
}) {
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const [activeTab, setActiveTab] = useState("trim");

  const pollRef = useRef(null);
  const pollCountRef = useRef(0);

  // Stop polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const startAnalysis = useCallback(async () => {
    if (!mediaUrl && !videoInfo?.webpage_url) return;

    setLoading(true);
    setError(null);
    setResult(null);

    const url = mediaUrl || videoInfo?.webpage_url;
    const durationHint =
      videoInfo?.duration != null ? parseFloat(videoInfo.duration) : undefined;

    try {
      const res = await fetch(`${API_BASE}/analyze-media`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          url,
          analyses: ["trim", "gif", "metadata", "clips"],
          ...(durationHint != null && { duration_hint_s: durationHint }),
        }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        if (res.status === 429) {
          throw new Error(err.detail || "Đã đạt giới hạn phân tích hôm nay.");
        }
        throw new Error(err.detail || `Lỗi ${res.status}`);
      }

      const { job_id, status } = await res.json();

      if (status === "cached" || status === "done") {
        await fetchResult(job_id);
        return;
      }

      // Start polling
      pollCountRef.current = 0;
      pollRef.current = setInterval(async () => {
        pollCountRef.current += 1;
        if (pollCountRef.current >= POLL_MAX_ATTEMPTS) {
          clearInterval(pollRef.current);
          setLoading(false);
          setError("Phân tích mất quá lâu. Vui lòng thử lại.");
          return;
        }
        const done = await fetchResult(job_id);
        if (done) clearInterval(pollRef.current);
      }, POLL_INTERVAL_MS);
    } catch (err) {
      setError(err.message || "Phân tích thất bại.");
      setLoading(false);
    }
  }, [mediaUrl, videoInfo]);

  async function fetchResult(jobId) {
    try {
      const res = await fetch(`${API_BASE}/analyze/${jobId}`, {
        credentials: "include",
      });
      if (!res.ok) return false;

      const data = await res.json();
      if (data.status === "done") {
        setResult(data);
        setLoading(false);

        // Auto-select first tab with results
        for (const tab of TAB_CONFIG) {
          const val = data[tab.key];
          const hasData = Array.isArray(val) ? val.length > 0 : val && Object.keys(val).length > 0;
          if (hasData) {
            setActiveTab(tab.id);
            break;
          }
        }
        return true;
      }
      return false;
    } catch {
      return false;
    }
  }

  function handleToggle() {
    const next = !expanded;
    setExpanded(next);
    if (next && !result && !loading && !error) {
      startAnalysis();
    }
  }

  // Determine visible tabs (those with data)
  const visibleTabs = result
    ? TAB_CONFIG.filter((t) => {
        const val = result[t.key];
        // Always show metadata tab if present, even if empty object
        if (t.id === "metadata") return val != null;
        return Array.isArray(val) ? val.length > 0 : false;
      })
    : [];

  // Handler adapters that call parent callbacks and also hit the apply APIs
  async function handleApplyTrim({ start, end }) {
    if (onApplyTrim) onApplyTrim({ start, end });
    if (!result?.job_id) return;
    try {
      await fetch(`${API_BASE}/smart-trim/apply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          job_id: result.job_id,
          suggestion_index: 0,
          media_url: mediaUrl || videoInfo?.webpage_url || "",
        }),
      });
    } catch {
      // Non-blocking
    }
  }

  async function handleApplyGif({ index, quality }) {
    if (onApplyGif) onApplyGif({ index, quality });
    if (!result?.job_id) return;
    try {
      await fetch(`${API_BASE}/smart-gif/apply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          job_id: result.job_id,
          suggestion_index: index,
          media_url: mediaUrl || videoInfo?.webpage_url || "",
          quality,
        }),
      });
    } catch {
      // Non-blocking
    }
  }

  async function handleQueueClips(indices) {
    if (onQueueClips) onQueueClips(indices);
    if (!result?.job_id) return;
    try {
      await fetch(`${API_BASE}/smart-clips/apply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          job_id: result.job_id,
          suggestion_indices: indices,
          media_url: mediaUrl || videoInfo?.webpage_url || "",
        }),
      });
    } catch {
      // Non-blocking
    }
  }

  if (!videoInfo && !mediaUrl) return null;

  return (
    <div className="w-full max-w-sm border border-gray-200 rounded-xl bg-white shadow-sm overflow-hidden">
      {/* Toggle header */}
      <button
        onClick={handleToggle}
        className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-gray-50 transition-colors"
        aria-expanded={expanded}
      >
        <div className="flex items-center gap-2">
          <span className="text-base">✨</span>
          <span className="text-sm font-semibold text-gray-800">Smart Analysis</span>
          {loading && (
            <span className="text-xs text-gray-400 animate-pulse">Đang phân tích...</span>
          )}
          {result && !loading && (
            <span className="text-xs bg-green-100 text-green-600 px-1.5 py-0.5 rounded">
              Xong
            </span>
          )}
        </div>
        <span className="text-gray-400 text-xs">{expanded ? "▲" : "▼"}</span>
      </button>

      {/* Collapsible body */}
      {expanded && (
        <div className="border-t border-gray-100">
          {/* Loading state */}
          {loading && !result && (
            <div className="px-4 pt-3">
              <p className="text-xs text-gray-400 mb-2 flex items-center gap-1">
                <span className="inline-block w-4 h-4 border-2 border-blue-300 border-t-blue-600 rounded-full animate-spin" />
                Đang phân tích
                <span className="animate-pulse">...</span>
              </p>
              <LoadingSkeleton />
            </div>
          )}

          {/* Error state */}
          {error && !loading && (
            <div className="px-4 py-4 text-center">
              <p className="text-sm text-red-500 mb-3">{error}</p>
              <button
                onClick={startAnalysis}
                className="text-xs text-blue-600 underline hover:text-blue-800"
              >
                Thử lại
              </button>
              <p className="text-xs text-gray-400 mt-1">
                (hoặc dùng trim thủ công bên dưới)
              </p>
            </div>
          )}

          {/* Results */}
          {result && !loading && (
            <>
              {/* Tab bar */}
              {visibleTabs.length > 0 && (
                <div className="flex border-b border-gray-100 overflow-x-auto">
                  {visibleTabs.map((tab) => (
                    <button
                      key={tab.id}
                      onClick={() => setActiveTab(tab.id)}
                      className={`flex-shrink-0 px-3 py-2 text-xs font-medium transition-colors ${
                        activeTab === tab.id
                          ? "border-b-2 border-blue-600 text-blue-600"
                          : "text-gray-500 hover:text-gray-700"
                      }`}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>
              )}

              {/* Tab content */}
              <div className="px-3 py-3">
                {activeTab === "trim" && (
                  <TrimTab
                    suggestions={result.trim_suggestions}
                    onApplyTrim={handleApplyTrim}
                  />
                )}
                {activeTab === "clips" && (
                  <HighlightsTab
                    suggestions={result.clip_suggestions}
                    onQueueClips={handleQueueClips}
                    tierFiltered={result.tier_filtered}
                  />
                )}
                {activeTab === "gif" && (
                  <GifTab
                    suggestions={result.gif_suggestions}
                    onApplyGif={handleApplyGif}
                  />
                )}
                {activeTab === "metadata" && (
                  <MetadataTab suggestions={result.metadata_suggestions} />
                )}
              </div>

              {/* Warnings */}
              {result.warnings && result.warnings.length > 0 && (
                <div className="px-3 pb-2">
                  {result.warnings.map((w, i) => (
                    <p key={i} className="text-xs text-amber-500">
                      ⚠ {w}
                    </p>
                  ))}
                </div>
              )}
            </>
          )}

          {/* Footer */}
          {!hidePoweredBy && (
            <div className="px-4 py-2 border-t border-gray-50 text-right">
              <span className="text-xs text-gray-300">Powered by VidGrab AI</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

import { useState, useEffect } from "react";

const PLATFORM_NAMES = {
  youtube: "YouTube", tiktok: "TikTok", facebook: "Facebook",
  instagram: "Instagram", douyin: "Douyin", twitter: "Twitter/X",
  reddit: "Reddit", pinterest: "Pinterest", threads: "Threads",
};

export default function SmartDefaultsBanner({ platform, userId, onAccept, onDismiss }) {
  const [suggestion, setSuggestion] = useState(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!platform || !userId) return;
    const dismissed = sessionStorage.getItem(`smart_default_dismissed:${platform}`);
    if (dismissed) return;

    const token = localStorage.getItem("sb-access-token");
    if (!token) return;

    fetch(`/api/v1/intelligence/smart-defaults/${platform}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => r.json())
      .then((d) => {
        if (d.suggested && d.quality && d.quality !== "video") {
          setSuggestion(d);
          setVisible(true);
        }
      })
      .catch(() => {});
  }, [platform, userId]);

  useEffect(() => {
    if (!visible) return;
    const t = setTimeout(() => {
      setVisible(false);
      if (onDismiss) onDismiss();
    }, 8000);
    return () => clearTimeout(t);
  }, [visible, onDismiss]);

  const handleAccept = () => {
    setVisible(false);
    if (onAccept && suggestion) onAccept(suggestion);
  };

  const handleDismiss = () => {
    sessionStorage.setItem(`smart_default_dismissed:${platform}`, "1");
    setVisible(false);
    if (onDismiss) onDismiss();
  };

  if (!visible || !suggestion) return null;

  return (
    <div className="animate-fade-in-up flex items-center gap-3 px-4 py-2.5 rounded-lg bg-blue-500/10 border border-blue-500/30 text-sm">
      <span className="text-blue-300 shrink-0">💡</span>
      <span className="text-zinc-300 flex-1">
        Bạn thường dùng <span className="text-white font-medium">{suggestion.quality}</span> cho {PLATFORM_NAMES[platform] || platform} — dùng lại không?
      </span>
      <button onClick={handleAccept} className="text-xs px-2.5 py-1 rounded bg-blue-600 hover:bg-blue-700 text-white font-medium transition shrink-0">
        Dùng
      </button>
      <button onClick={handleDismiss} className="text-xs text-zinc-500 hover:text-zinc-300 transition shrink-0">✕</button>
    </div>
  );
}

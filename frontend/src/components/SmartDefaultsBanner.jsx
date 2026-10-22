import { useState, useEffect } from "react";
import { API_BASE } from '../lib/apiBase';
import { Trans, useTranslation } from 'react-i18next';

const PLATFORM_NAMES = {
  youtube: "YouTube", tiktok: "TikTok", facebook: "Facebook",
  instagram: "Instagram", douyin: "Douyin", twitter: "Twitter/X",
  reddit: "Reddit", pinterest: "Pinterest", threads: "Threads",
};

export default function SmartDefaultsBanner({ platform, userId, onAccept, onDismiss }) {
  const { t } = useTranslation('common');
  const [suggestion, setSuggestion] = useState(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!platform || !userId) return;
    const dismissed = sessionStorage.getItem(`smart_default_dismissed:${platform}`);
    if (dismissed) return;

    const token = localStorage.getItem("sb-access-token");
    if (!token) return;

    fetch(`${API_BASE}/api/v1/intelligence/smart-defaults/${platform}`, {
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
    const timer = setTimeout(() => {
      setVisible(false);
      if (onDismiss) onDismiss();
    }, 8000);
    return () => clearTimeout(timer);
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
    <div className="animate-fade-in-up flex items-center gap-3 px-4 py-2.5 rounded-lg bg-surface-2 border border-line text-sm">
      <span className="text-fg-2 shrink-0">💡</span>
      <span className="text-fg-2 flex-1">
        <Trans t={t} i18nKey="smartDefaults.suggest" values={{ quality: suggestion.quality, platform: PLATFORM_NAMES[platform] || platform }}
          components={{ q: <span className="text-fg font-medium" /> }} />
      </span>
      <button onClick={handleAccept} className="text-xs px-2.5 py-1 rounded bg-accent hover:bg-accent-hover text-accent-fg font-medium transition shrink-0">
        {t('smartDefaults.use')}
      </button>
      <button onClick={handleDismiss} className="text-xs text-fg-muted hover:text-fg-2 transition shrink-0">✕</button>
    </div>
  );
}

import { useState, useEffect, useRef } from "react";
import { useTranslation } from 'react-i18next';

// Help text lives in locales/*/common.json (help.<topic>).
const HELP_TOPICS = ['quality_selector', 'batch_download', 'schedule', 'archive_pin', 'remove_watermark', 'webhook', 'approval_workflow', 'auto_retry'];

export function ContextualHelp({ topic, position = "right" }) {
  const { t } = useTranslation('common');
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const text = HELP_TOPICS.includes(topic) ? t(`help.${topic}`) : undefined;

  useEffect(() => {
    if (!open) return;
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  if (!text) return null;

  const posClass = {
    right:  "left-6 top-0",
    top:    "bottom-6 left-1/2 -translate-x-1/2",
    bottom: "top-6 left-1/2 -translate-x-1/2",
  }[position] || "left-6 top-0";

  return (
    <span ref={ref} className="relative inline-flex items-center">
      <button
        onClick={() => setOpen(!open)}
        className="w-4 h-4 rounded-full bg-surface-2 text-fg-muted hover:bg-line hover:text-fg text-xs flex items-center justify-center transition ml-1"
        title={t('helpTitle')}
      >
        ?
      </button>
      {open && (
        <div className={`absolute z-50 w-64 p-3 bg-surface border border-line-strong rounded-lg shadow-xl text-xs text-fg-2 leading-relaxed ${posClass}`}>
          {text}
          <div className="absolute w-2 h-2 bg-surface border-l border-t border-line-strong rotate-[-135deg] -left-1 top-3" />
        </div>
      )}
    </span>
  );
}

export function FirstTimeHint({ feature, children }) {
  const { t } = useTranslation('common');
  const [seen, setSeen] = useState(true);

  useEffect(() => {
    try {
      const raw = localStorage.getItem("vidgrab:seen_features");
      const list = raw ? JSON.parse(raw) : [];
      setSeen(list.includes(feature));
    } catch (_) {}
  }, [feature]);

  const markSeen = () => {
    try {
      const raw = localStorage.getItem("vidgrab:seen_features");
      const list = raw ? JSON.parse(raw) : [];
      if (!list.includes(feature)) {
        list.push(feature);
        localStorage.setItem("vidgrab:seen_features", JSON.stringify(list));
      }
    } catch (_) {}
    setSeen(true);
  };

  return (
    <span className="relative inline-flex items-center gap-1" onClick={markSeen}>
      {children}
      {!seen && (
        <span className="absolute -top-1.5 -right-2 px-1.5 py-0.5 text-[9px] font-bold rounded-full bg-accent text-accent-fg animate-pulse leading-none">
          {t('newBadge')}
        </span>
      )}
    </span>
  );
}

export default ContextualHelp;

import { useState } from 'react';
import { Check, Copy, ExternalLink } from 'lucide-react';
import { api } from '../lib/tauri';

/** Shows a VidGrab link with "Mở" (opens the default browser through the
 * validated Rust open_url command) and "Sao chép" as the fallback. */
export function CopyLink({ url, label }: { url: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <span className="inline-flex max-w-full items-center gap-1.5">
      <code className="truncate rounded bg-surface-2 px-1.5 py-0.5 text-xs text-fg-2 select-text">{label ?? url}</code>
      <button
        type="button"
        aria-label="Mở trong trình duyệt"
        title="Mở trong trình duyệt"
        className="inline-flex h-6 items-center gap-1 rounded-md px-1.5 text-xs font-medium text-accent-text hover:bg-accent-soft"
        onClick={() => { api.openUrl(url).catch(() => { /* not allowed or not in app: copy still works */ }); }}
      >
        <ExternalLink size={13} />
        Mở
      </button>
      <button
        type="button"
        aria-label="Sao chép liên kết"
        title="Sao chép liên kết"
        className="inline-flex h-6 items-center gap-1 rounded-md px-1.5 text-xs font-medium text-accent-text hover:bg-accent-soft"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(url);
            setDone(true);
            setTimeout(() => setDone(false), 1800);
          } catch { /* clipboard blocked */ }
        }}
      >
        {done ? <Check size={13} /> : <Copy size={13} />}
        {done ? 'Đã chép' : 'Sao chép'}
      </button>
    </span>
  );
}

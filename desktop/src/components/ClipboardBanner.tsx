import { ClipboardCheck, Download, SlidersHorizontal, X } from 'lucide-react';
import { useEffect } from 'react';
import { Button } from './ui';
import { chooseQuality, clipOffer, dismissOffer, downloadNow } from '../lib/clipboard';
import { updateSettings } from '../lib/settings';

const AUTO_HIDE_MS = 60_000;

/** "Đã nhận link vừa sao chép" — clipboard link watch, mode "ask" (14/10). */
export function ClipboardBanner() {
  const offer = clipOffer.use();
  useEffect(() => {
    if (!offer) return undefined;
    const t = setTimeout(dismissOffer, AUTO_HIDE_MS);
    return () => clearTimeout(t);
  }, [offer]);
  if (!offer) return null;
  const short = offer.url.length > 70 ? `${offer.url.slice(0, 67)}…` : offer.url;
  // wording: BA review
  return (
    <div role="status" className="fixed bottom-4 left-1/2 z-40 w-[420px] -translate-x-1/2 rounded-xl border border-line bg-surface p-3 shadow-card">
      <div className="flex items-start gap-3">
        <ClipboardCheck size={20} className="mt-0.5 shrink-0 text-accent-text" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-fg">Đã nhận link {offer.platform} vừa sao chép</p>
          <p className="truncate text-xs text-fg-muted" title={offer.url}>{short}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button variant="primary" icon={<Download size={16} />} onClick={() => downloadNow(offer.url, offer.platform)}>Tải</Button>
            <Button icon={<SlidersHorizontal size={16} />} onClick={() => chooseQuality(offer.url)}>Chọn chất lượng</Button>
          </div>
          <button
            type="button"
            className="mt-2 text-xs text-fg-muted underline underline-offset-2 hover:text-fg-2"
            onClick={() => { updateSettings({ clipboardMode: 'off' }); dismissOffer(); }}
          >
            Tắt tự nhận link
          </button>
        </div>
        <button type="button" aria-label="Bỏ qua" title="Bỏ qua" onClick={dismissOffer}
          className="rounded-md p-1 text-fg-muted hover:bg-canvas hover:text-fg">
          <X size={16} />
        </button>
      </div>
    </div>
  );
}

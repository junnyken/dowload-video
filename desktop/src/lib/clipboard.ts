// Clipboard link watch — app glue (pure rules in clipboard-core.ts, the
// clipboard itself is read in Rust: clip_watch.rs).
//
// ask  → a banner offers "Tải" (default quality) / "Chọn chất lượng" / "Bỏ qua";
//        if the window is hidden in the tray, a Windows toast tells the user.
// auto → the link is analysed and queued with the default quality at once.
// off  → Rust stops reading the clipboard.
import { decide, normalize } from './clipboard-core';
import { api, onClipboardLink } from './tauri';
import { settings } from './settings';
import { createStore } from './store';
import { analyze, probes } from './probes';
import { go, openInChannels, toast } from './ui';
import { isChannelOnDownloadScreen } from './urls';
import { addToQueue } from '../screens/DownloadScreen';

export type ClipOffer = { url: string; platform: string };
export const clipOffer = createStore<ClipOffer | null>(null);

const seen = new Map<string, number>();
const ignored = new Set<string>();
const AUTO_WAIT_MS = 2 * 60 * 1000;

/** Call before the app itself copies a link, so it is not offered back. */
export function ignoreCopied(url: string) {
  ignored.add(normalize(url));
}

/** Analyse the link and queue it with the default quality. */
export function downloadNow(url: string, platform: string) {
  clipOffer.set(null);
  if (isChannelOnDownloadScreen(url)) {
    openInChannels(url);
    return;
  }
  if (!analyze(url)) {
    // already on the Download screen: let the user finish there
    go('download');
    return;
  }
  const stop = () => { unsub(); clearTimeout(timer); };
  const timer = setTimeout(stop, AUTO_WAIT_MS);
  const unsub = probes.subscribe(() => {
    const card = probes.get().cards.find((c) => c.url === url);
    if (!card) { stop(); return; }
    if (card.status === 'ready') {
      stop();
      void addToQueue(card).then((ok) => {
        if (ok) toast('success', `Đã thêm video ${platform} vừa sao chép vào hàng đợi.`);
        else go('download');
      });
    } else if (card.status === 'error') {
      stop();
      toast('error', `Không phân tích được link ${platform} vừa sao chép. Xem ở mục Tải xuống.`);
      go('download');
    }
  });
}

/** "Chọn chất lượng": open the Download screen with the link analysed. */
export function chooseQuality(url: string) {
  clipOffer.set(null);
  go('download');
  analyze(url);
}

export function dismissOffer() {
  clipOffer.set(null);
}

function apply() {
  void api.clipboardWatchSet(settings.get().clipboardMode !== 'off').catch(() => {});
}

let started = false;
export function startClipboardWatch() {
  if (started) return;
  started = true;
  apply();
  let mode = settings.get().clipboardMode;
  settings.subscribe(() => {
    const m = settings.get().clipboardMode;
    if (m !== mode) { mode = m; apply(); if (m === 'off') clipOffer.set(null); }
  });
  void onClipboardLink((raw) => {
    const d = decide(raw, settings.get().clipboardMode, seen, ignored, Date.now());
    if (d.action === 'none') return;
    if (d.action === 'auto') {
      downloadNow(d.url, d.platform);
      if (document.hidden) void api.notify('VidGrab', `Đang tải link ${d.platform} vừa sao chép.`).catch(() => {});
      return;
    }
    clipOffer.set({ url: d.url, platform: d.platform });
    if (document.hidden) {
      void api.notify('VidGrab', `Đã nhận link ${d.platform} vừa sao chép. Mở VidGrab để tải.`).catch(() => {});
    }
  });
}
// wording: BA review (all user-facing strings above)

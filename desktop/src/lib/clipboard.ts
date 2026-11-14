// Clipboard link watch — app glue (pure rules in clipboard-core.ts, the
// clipboard itself is read in Rust: clip_watch.rs).
//
// ask  → a banner offers "Tải" (default quality) / "Chọn chất lượng" / "Bỏ qua";
//        if the window is hidden in the tray, a Windows toast tells the user.
// auto → the link is analysed and queued with the default quality at once.
// off  → Rust stops reading the clipboard.
import { decide, normalize } from './clipboard-core';
import { planExtJob } from './ext-handoff-core';
import { joinPath } from './watch-core';
import { api, onClipboardLink, onExtDownload, type ExtJob } from './tauri';
import { ensureOutDir, settings } from './settings';
import { createStore } from './store';
import { analyze, probes } from './probes';
import { go, openInChannels, toast } from './ui';
import { isChannelOnDownloadScreen } from './urls';
import { addToQueue } from '../screens/DownloadScreen';
import { enqueue } from './queue';
import { findDuplicate } from './dedupe';
import { auth } from './auth';
import { quota, refreshQuota } from './quota';

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
        // not added but the card is still there (e.g. no folder / disk space): finish on the Download screen
        else if (probes.get().cards.some((c) => c.url === url)) go('download');
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

// 0.19.0: the VidGrab browser extension hands over what it cannot download
// in the browser (DASH, audio in a separate playlist). Same path as a copied
// link in "auto" mode: analysed and queued with the default quality, here on
// this machine — not on the VidGrab server.
async function handleExtDownload(job: ExtJob) {
  const st = settings.get();
  const plan = planExtJob(job, { defaultQuality: st.defaultQuality, guest: auth.get().status !== 'in' });
  if (plan.mode === 'probe') {
    const name = (job.title || '').trim() || 'video từ trình duyệt';
    downloadNow(plan.url, plan.platform);
    if (document.hidden) void api.notify('VidGrab', `Đang tải "${name.slice(0, 60)}" do tiện ích gửi sang.`).catch(() => {});
    else toast('info', `Tiện ích VidGrab đã gửi "${name.slice(0, 60)}" sang app — đang phân tích để tải.`);
    return;
  }

  if (auth.get().status !== 'in') {
    const msg = 'Tính năng tải phim & luồng phát từ các trang web ngoài chỉ dành cho tài khoản gói cao nhất (Team). Hãy đăng nhập để sử dụng.';
    if (document.hidden) void api.notify('VidGrab', msg).catch(() => {});
    toast('error', msg);
    go('settings');
    return;
  }
  if (!quota.get().snap) {
    await refreshQuota().catch(() => {});
  }
  const snap = quota.get().snap;
  if (snap && snap.universalStream === false) {
    const msg = 'Tính năng tải phim & luồng phát từ các trang web ngoài chỉ dành cho gói tài khoản cao nhất (Team). Vui lòng nâng cấp tài khoản.';
    if (document.hidden) void api.notify('VidGrab', msg).catch(() => {});
    toast('error', msg);
    return;
  }

  const outDir = (await ensureOutDir()) || st.outDir;
  if (!outDir) {
    toast('error', 'Chưa chọn thư mục lưu tệp tải về.');
    go('settings');
    return;
  }
  const dup = await findDuplicate(plan.url);
  if (dup?.kind === 'queued') {
    toast('info', 'Video này đang có trong hàng đợi.');
    go('queue');
    return;
  }
  const targetDir = plan.seriesFolder ? joinPath(outDir, plan.seriesFolder) : outDir;
  enqueue({
    url: plan.url,
    title: plan.title,
    thumbnail: null,
    platform: plan.platform,
    uploader: null,
    outDir: targetDir,
    formatId: plan.formatId,
    audioOnly: plan.audioOnly,
    formatLabel: plan.formatLabel,
    estSize: null,
    quality: plan.quality,
    extHeaders: plan.headers,
    fileTitle: plan.fileTitle,
    fileId: plan.fileId,
    localOnly: true,
    extCookieJar: job.cookieJar || undefined,
    extSubtitles: plan.subtitles.length ? plan.subtitles : undefined,
  });
  if (document.hidden) void api.notify('VidGrab', `Đang tải "${plan.title.slice(0, 60)}" do tiện ích gửi sang.`).catch(() => {});
  else toast('success', `Đã thêm "${plan.title.slice(0, 60)}" từ tiện ích vào hàng đợi tải.`);
}

let extStarted = false;
export function startExtensionBridge() {
  if (extStarted) return;
  extStarted = true;
  void onExtDownload((job) => { void handleExtDownload(job); });
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

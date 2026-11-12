// Run: npm test   (pure logic only: no Tauri, no network)
// Phase 33F — Auto-download mode, folder sanitization & catch-up rules (TDD).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  MAX_AUTO_PER_CHANNEL,
  allowedModes,
  autoDownloadedNotifyText,
  emptyLocal,
  parseStatus,
  parseSubscriptions,
  pendingItems,
  quotaExhaustedNotifyText,
  runAutoDownloadPoll,
  sanitizeFolderName,
  joinPath,
  type EnqueueWithClaims,
  type WatchItem,
  type WatchSubscription,
} from '../src/lib/watch-core.ts';

const mkSub = (id: string, mode: string, channel = 'meo', status = 'active'): WatchSubscription => ({
  id,
  mode,
  status,
  created_at: '2026-11-10T00:00:00Z',
  baseline_completed_at: '2026-11-10T00:05:00Z',
  new_items_7d: 0,
  source: {
    id: `src-${id}`,
    platform: 'tiktok',
    display_name: channel,
    canonical_url: `https://www.tiktok.com/@${channel}`,
    status: 'active',
    last_scan_at: '2026-11-12T00:00:00Z',
    next_scan_at: '2026-11-12T06:00:00Z',
    last_failure_category: null,
  },
});

const mkItem = (
  id: string,
  subId: string,
  channel: string,
  at = '2026-11-12T01:00:00Z',
  url: string | null = `https://www.tiktok.com/@${channel}/video/${id}`,
): WatchItem => ({
  id,
  source_id: `src-${subId}`,
  subscription_id: subId,
  channel,
  platform: 'tiktok',
  title: `Video ${id}`,
  url,
  published_at: '2026-11-12T00:50:00Z',
  discovered_at: at,
});

const statusPayload = (over: Record<string, unknown> = {}) => ({
  enabled: true,
  kill_switch: false,
  admin_only: false,
  platforms: ['tiktok', 'youtube'],
  tier: 'starter',
  auto_download_allowed: true,
  limits: { max_sources: 2, min_interval_sec: 21600, replace_cooldown_sec: 3600 },
  used: 1,
  ...over,
});

test('1. tier không đủ -> không có lựa chọn tự tải & không kích hoạt tự tải', async () => {
  const freeSt = parseStatus(200, statusPayload({ tier: 'free', auto_download_allowed: false }));
  assert.equal(freeSt.kind, 'ok');
  if (freeSt.kind !== 'ok') return;
  assert.equal(freeSt.status.autoDownloadAllowed, false);
  assert.deepEqual(allowedModes(freeSt.status), ['notify_only', 'one_tap']);

  const starterSt = parseStatus(200, statusPayload({ tier: 'starter', auto_download_allowed: true }));
  assert.equal(starterSt.kind, 'ok');
  if (starterSt.kind !== 'ok') return;
  assert.equal(starterSt.status.autoDownloadAllowed, true);
  assert.deepEqual(allowedModes(starterSt.status), ['notify_only', 'one_tap', 'auto_download']);

  // Even if a stale subscription still says auto_download locally, autoDownloadAllowed=false prevents auto-enqueue
  let called = 0;
  const fakeEnqueue: EnqueueWithClaims = async (videos) => {
    called += videos.length;
    return { enqueued: videos.map((v) => ({ id: v.id })), refused: [] };
  };
  const baseLocal = { ...emptyLocal(), since: '2026-11-11T00:00:00Z' };
  const out = await runAutoDownloadPoll({
    local: baseLocal,
    items: [mkItem('1', 's1', 'meo')],
    subs: [mkSub('s1', 'auto_download', 'meo')],
    autoDownloadAllowed: false,
    nowIso: '2026-11-12T02:00:00Z',
    quality: '1080',
    baseDir: 'D:\\VidGrab',
    enqueueWithClaims: fakeEnqueue,
  });
  assert.equal(called, 0);
  assert.equal(out.downloadedBatches.length, 0);
});

test('2. poll có item auto_download -> enqueue đúng thư mục kênh + thông báo Đã tải N video mới từ @kênh', async () => {
  const calls: { urls: string[]; outDir: string; title: string }[] = [];
  const fakeEnqueue: EnqueueWithClaims = async (videos, ch) => {
    calls.push({ urls: videos.map((v) => v.url), outDir: ch.outDir, title: ch.title });
    return { enqueued: videos.map((v) => ({ id: v.id })), refused: [] };
  };
  const subs = parseSubscriptions({
    subscriptions: [
      {
        id: 's1',
        mode: 'auto_download',
        status: 'active',
        new_items_7d: 2,
        source: { platform: 'tiktok', display_name: '@Kenh Moi: Official*', canonical_url: 'https://www.tiktok.com/@kenhmoi' },
      },
    ],
  });
  assert.equal(subs[0].mode, 'auto_download');

  const items = [
    mkItem('10', 's1', '@Kenh Moi: Official*', '2026-11-12T01:00:00Z'),
    mkItem('11', 's1', '@Kenh Moi: Official*', '2026-11-12T01:05:00Z'),
  ];
  const out = await runAutoDownloadPoll({
    local: { ...emptyLocal(), since: '2026-11-11T00:00:00Z' },
    items,
    subs,
    autoDownloadAllowed: true,
    nowIso: '2026-11-12T02:00:00Z',
    quality: 'best',
    baseDir: 'D:\\Downloads\\VidGrab',
    enqueueWithClaims: fakeEnqueue,
  });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].outDir, 'D:\\Downloads\\VidGrab\\Kenh Moi Official');
  assert.equal(calls[0].urls.length, 2);
  assert.equal(out.downloadedBatches.length, 1);
  assert.equal(out.downloadedBatches[0].outDir, 'D:\\Downloads\\VidGrab\\Kenh Moi Official');
  assert.equal(
    autoDownloadedNotifyText(out.downloadedBatches[0].count, out.downloadedBatches[0].channel).title,
    'Đã tải 2 video mới từ @Kenh Moi: Official*',
  );
  assert.equal(out.notifyBatches.length, 0, 'auto_download channel does not also fire manual "bấm Tải" toast');
});

test('3. item notify_only / one_tap -> không tự tải (chỉ thông báo)', async () => {
  let enqueuedCount = 0;
  const fakeEnqueue: EnqueueWithClaims = async (videos) => {
    enqueuedCount += videos.length;
    return { enqueued: videos.map((v) => ({ id: v.id })), refused: [] };
  };
  const subs = [mkSub('s_notify', 'notify_only', 'kenhA'), mkSub('s_tap', 'one_tap', 'kenhB')];
  const items = [mkItem('1', 's_notify', 'kenhA'), mkItem('2', 's_tap', 'kenhB')];
  const out = await runAutoDownloadPoll({
    local: { ...emptyLocal(), since: '2026-11-11T00:00:00Z' },
    items,
    subs,
    autoDownloadAllowed: true,
    nowIso: '2026-11-12T02:00:00Z',
    quality: '1080',
    baseDir: 'D:\\VidGrab',
    enqueueWithClaims: fakeEnqueue,
  });

  assert.equal(enqueuedCount, 0, 'notify_only and one_tap must never auto-enqueue');
  assert.equal(out.downloadedBatches.length, 0);
  assert.equal(out.notifyBatches.length, 2, 'manual channels still get normal new-video notifications');
  assert.equal(pendingItems(items, out.local).length, 2);
});

test('4. hết lượt -> giữ lại trong "Video mới" + thông báo 1 lần "Hết lượt tải hôm nay, còn N video chờ"', async () => {
  const fakeEnqueue: EnqueueWithClaims = async (videos) => {
    // Allow 1st video, refuse the remaining 2 due to daily quota
    return {
      enqueued: [{ id: videos[0].id }],
      refused: videos.slice(1).map((v) => ({ video: { id: v.id }, detail: 'Hết lượt hôm nay.' })),
    };
  };
  const subs = [mkSub('s1', 'auto_download', 'meo')];
  const items = [mkItem('1', 's1', 'meo'), mkItem('2', 's1', 'meo'), mkItem('3', 's1', 'meo')];

  const poll1 = await runAutoDownloadPoll({
    local: { ...emptyLocal(), since: '2026-11-11T00:00:00Z' },
    items,
    subs,
    autoDownloadAllowed: true,
    nowIso: '2026-11-12T03:00:00Z',
    quality: '1080',
    baseDir: 'D:\\VidGrab',
    enqueueWithClaims: fakeEnqueue,
  });

  assert.deepEqual(poll1.local.done, ['1']);
  assert.deepEqual(pendingItems(items, poll1.local).map((i) => i.id), ['2', '3'], 'refused items stay in Video mới');
  assert.equal(poll1.quotaWarning, 'Hết lượt tải hôm nay, còn 2 video chờ');
  assert.equal(quotaExhaustedNotifyText(2).body, 'Hết lượt tải hôm nay, còn 2 video chờ');

  // Second poll on the same day with quota still 0 -> must NOT repeat the quota warning notification
  const fakeAllRefused: EnqueueWithClaims = async (videos) => ({
    enqueued: [],
    refused: videos.map((v) => ({ video: { id: v.id }, detail: 'Hết lượt hôm nay.' })),
  });
  const poll2 = await runAutoDownloadPoll({
    local: poll1.local,
    items,
    subs,
    autoDownloadAllowed: true,
    nowIso: '2026-11-12T03:15:00Z',
    quality: '1080',
    baseDir: 'D:\\VidGrab',
    enqueueWithClaims: fakeAllRefused,
  });
  assert.equal(poll2.quotaWarning, null, 'only notify once per day when quota is exhausted');
  assert.deepEqual(pendingItems(items, poll2.local).map((i) => i.id), ['2', '3']);
});

test('5. chạy poll 2 lần + kiểm file đã tồn tại trong thư mục kênh -> không tải trùng', async () => {
  const queuedIds: string[] = [];
  const fakeEnqueue: EnqueueWithClaims = async (videos) => {
    queuedIds.push(...videos.map((v) => v.id));
    return { enqueued: videos.map((v) => ({ id: v.id })), refused: [] };
  };
  const subs = [mkSub('s1', 'auto_download', 'meo')];
  const items = [mkItem('1', 's1', 'meo'), mkItem('2', 's1', 'meo')];

  // Item '2' already exists as a file in D:\VidGrab\meo on disk
  const existsCheck = async (outDir: string, it: WatchItem) => outDir === 'D:\\VidGrab\\meo' && it.id === '2';

  const poll1 = await runAutoDownloadPoll({
    local: { ...emptyLocal(), since: '2026-11-11T00:00:00Z' },
    items,
    subs,
    autoDownloadAllowed: true,
    nowIso: '2026-11-12T04:00:00Z',
    quality: '1080',
    baseDir: 'D:\\VidGrab',
    enqueueWithClaims: fakeEnqueue,
    fileExistsInDir: existsCheck,
  });

  assert.deepEqual(queuedIds, ['1'], 'item 2 already on disk in channel folder -> skipped');
  assert.deepEqual(poll1.local.done.sort(), ['1', '2']);

  // Poll 2 with exact same items -> zero enqueue calls
  const poll2 = await runAutoDownloadPoll({
    local: poll1.local,
    items,
    subs,
    autoDownloadAllowed: true,
    nowIso: '2026-11-12T04:15:00Z',
    quality: '1080',
    baseDir: 'D:\\VidGrab',
    enqueueWithClaims: fakeEnqueue,
    fileExistsInDir: existsCheck,
  });

  assert.deepEqual(queuedIds, ['1'], 'running poll twice never downloads duplicates');
  assert.equal(poll2.downloadedBatches.length, 0);
});

test('6. mở lại app sau thời gian tắt: tải bù nhưng tối đa 20 video/kênh một lần', async () => {
  assert.equal(MAX_AUTO_PER_CHANNEL, 20);
  const enqueuedBatches: number[] = [];
  const fakeEnqueue: EnqueueWithClaims = async (videos) => {
    enqueuedBatches.push(videos.length);
    return { enqueued: videos.map((v) => ({ id: v.id })), refused: [] };
  };
  const subs = [mkSub('s1', 'auto_download', 'meo')];
  const manyItems = Array.from({ length: 28 }, (_, idx) =>
    mkItem(`v${idx + 1}`, 's1', 'meo', `2026-11-12T01:${String(idx).padStart(2, '0')}:00Z`),
  );

  // Even on first local run or reopen catch-up, auto_download catches up capped at 20/channel
  const run1 = await runAutoDownloadPoll({
    local: emptyLocal(),
    items: manyItems,
    subs,
    autoDownloadAllowed: true,
    nowIso: '2026-11-12T05:00:00Z',
    quality: '1080',
    baseDir: 'D:\\VidGrab',
    enqueueWithClaims: fakeEnqueue,
  });

  assert.deepEqual(enqueuedBatches, [20], 'capped at 20 videos per channel in a single run');
  assert.equal(run1.local.done.length, 20);
  assert.equal(pendingItems(manyItems, run1.local).length, 8, 'remaining 8 stay pending for next run');
});

test('7. sanitizeFolderName: ký tự cấm Windows, tên hệ thống (CON/NUL...), dấu chấm/khoảng trắng cuối, emoji, quá dài', () => {
  // Forbidden characters <>:"/\|?*
  assert.equal(sanitizeFolderName('Kênh <Mới>: "Test" / \\ | ? * Video'), 'Kênh Mới Test Video');
  assert.equal(sanitizeFolderName('<>:"/\\|?*'), 'Kenh');

  // Windows reserved device names (case-insensitive, bare or with extension)
  for (const reserved of ['CON', 'con', 'NUL', 'nul', 'PRN', 'AUX', 'COM1', 'com9', 'LPT1', 'lpt9', 'CON.txt', 'nul.mp4']) {
    const safe = sanitizeFolderName(reserved);
    const stem = safe.split('.')[0].toUpperCase();
    assert.equal(
      /^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$/.test(stem),
      false,
      `reserved name "${reserved}" must not remain "${safe}"`,
    );
    assert.ok(safe.length > 0);
  }

  // Trailing dots and spaces
  assert.equal(sanitizeFolderName('Ten Kenh...   .. '), 'Ten Kenh');
  assert.equal(sanitizeFolderName('...   '), 'Kenh');

  // Emoji (must keep valid UTF-16 without lone surrogates even when truncated)
  const emojiName = '🎬🎵 Kênh Âm Nhạc 🔥✨';
  assert.equal(sanitizeFolderName(emojiName), '🎬🎵 Kênh Âm Nhạc 🔥✨');
  const longEmoji = '🎬'.repeat(70);
  const slicedEmoji = sanitizeFolderName(longEmoji);
  assert.equal(Array.from(slicedEmoji).length <= 60, true);
  assert.equal(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(slicedEmoji), false, 'no broken surrogate pair');

  // Very long name + trailing dot exposed at truncation boundary
  const longWithDots = `${'A'.repeat(58)}..B.${'C'.repeat(40)}`;
  const truncated = sanitizeFolderName(longWithDots);
  assert.ok(truncated.length <= 60);
  assert.equal(/[. ]$/.test(truncated), false, 'no trailing dot or space after truncation');

  // joinPath combines base and sanitized folder cleanly
  assert.equal(joinPath('D:\\VidGrab\\', sanitizeFolderName('@Meo Official.')), 'D:\\VidGrab\\@Meo Official');
});

test('8. cài đặt chạy nền: mặc định TẮT cho Thu nhỏ xuống khay khi đóng', async () => {
  const { readFile } = await import('node:fs/promises');
  const settingsSrc = await readFile(new URL('../src/lib/settings.ts', import.meta.url), 'utf8');
  assert.match(settingsSrc, /closeToTray:\s*false/, 'DEFAULTS.closeToTray must be false (OFF by default)');
  const rustSrc = await readFile(new URL('../src-tauri/src/lib.rs', import.meta.url), 'utf8');
  assert.match(rustSrc, /close_to_tray:\s*AtomicBool::new\(false\)/, 'Rust AppFlags default close_to_tray must be false');
});

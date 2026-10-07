// "Tài khoản nền tảng" (PLAN-32D §3, P1): the user's own platform cookies,
// used by the local yt-dlp only. The webview never sees a cookie: Rust reads
// them from the login window, stores them encrypted (Windows DPAPI) and hands
// yt-dlp a temp file per run. Here: the server switch (features.cookiePlatforms),
// the saved-state per platform, the first-use consent, open / finish / clear.
// Pure rules: cookies-core.ts.
import { createStore } from './store';
import { api, onLoginClosed } from './tauri';
import { apiFetch } from './http';
import { confirmDialog, toast } from './ui';
import { errorMessage, toAppError } from './errors';
import {
  COOKIE_PLATFORMS, parseCookiePlatforms, planRoute, platformLabelOf, type CookieStatus, type Plan,
} from './cookies-core';

export type CookieState = {
  /** null until GET /client/version answered once (or the cached copy was read). */
  enabled: string[] | null;
  status: Record<string, CookieStatus>;
  /** Platform whose login window is open (the "Xong" button shows for it). */
  pending: string | null;
  busy: string | null;
};

const FEATURES_KEY = 'vg.cookies.features';
const CONSENT_KEY = 'vg.cookies.consent';

function readJson<T>(key: string, fallback: T): T {
  try { return (JSON.parse(localStorage.getItem(key) ?? 'null') as T) ?? fallback; } catch { return fallback; }
}
function writeJson(key: string, v: unknown) {
  try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* storage unavailable */ }
}

// The last known server switch, so an offline start still knows (cookies work offline).
export const cookieState = createStore<CookieState>({
  enabled: (() => { const v = readJson<unknown>(FEATURES_KEY, null); return v == null ? null : parseCookiePlatforms(v); })(),
  status: {},
  pending: null,
  busy: null,
});

const consent = (): Record<string, number> => readJson<Record<string, number>>(CONSENT_KEY, {});
export const hasConsent = (slug: string) => typeof consent()[slug] === 'number';
function setConsent(slug: string, on: boolean) {
  const c = { ...consent() };
  if (on) c[slug] = Date.now(); else delete c[slug];
  writeJson(CONSENT_KEY, c);
}

/** Usable for downloads: saved, and the user agreed to the first-use text. */
export function usable(slug: string): boolean {
  return !!cookieState.get().status[slug]?.saved && hasConsent(slug);
}

/** The route a URL takes right now (queue, probe, channel listing). */
export function planFor(url: string): Plan {
  return planRoute(url, { enabled: cookieState.get().enabled ?? [], usable });
}

/** Platforms the server allows, in table order (what Settings lists). */
export function offeredPlatforms(enabled: string[] | null) {
  return COOKIE_PLATFORMS.filter((p) => (enabled ?? []).includes(p.slug));
}

let featuresP: Promise<void> | null = null;
/** GET /client/version -> features.cookiePlatforms. Network failure keeps the cached value. */
export function loadFeatures(): Promise<void> {
  featuresP ??= (async () => {
    try {
      const r = await apiFetch<{ features?: { cookiePlatforms?: unknown } }>('/api/v1/client/version');
      if (r.status === 200 && r.data) {
        const list = parseCookiePlatforms(r.data.features?.cookiePlatforms);
        writeJson(FEATURES_KEY, list);
        cookieState.set((s) => ({ ...s, enabled: list }));
      }
    } catch { /* offline: keep what we know */ }
  })().finally(() => { featuresP = null; });
  return featuresP;
}

export async function refreshCookieStatus(): Promise<void> {
  try {
    const rows = await api.cookiesStatus();
    const status: Record<string, CookieStatus> = {};
    for (const r of rows) status[r.platform] = r;
    cookieState.set((s) => ({ ...s, status }));
  } catch { /* not in the app */ }
}

// wording: BA review
export function consentText(label: string): string {
  return `VidGrab sẽ dùng phiên đăng nhập ${label} của bạn để tải video NGAY TRÊN MÁY NÀY. Cookie được mã hoá bằng Windows và không gửi lên máy chủ VidGrab. Việc tải tự động có thể trái điều khoản của ${label}; bạn tự chịu trách nhiệm với tài khoản của mình. Có thể xoá bất kỳ lúc nào trong Cài đặt.`;
}

/** "Kết nối": first-use consent (once per platform), then the platform's page in its own window. */
export async function connect(slug: string): Promise<void> {
  const label = platformLabelOf(slug);
  if (!hasConsent(slug)) {
    // wording: BA review
    const ok = await confirmDialog({ title: `Dùng tài khoản ${label} để tải`, text: consentText(label), okLabel: 'Đồng ý và kết nối' });
    if (!ok) return;
    setConsent(slug, true);
  }
  cookieState.set((s) => ({ ...s, busy: slug }));
  try {
    await api.cookiesLoginOpen(slug);
    cookieState.set((s) => ({ ...s, pending: slug }));
  } catch (e) {
    toast('error', errorMessage(toAppError(e).code));
  } finally {
    cookieState.set((s) => ({ ...s, busy: null }));
  }
}

/** "Xong": Rust reads the window's cookies, keeps the platform's own, encrypts them, closes the window. */
export async function finish(slug: string): Promise<void> {
  const label = platformLabelOf(slug);
  cookieState.set((s) => ({ ...s, busy: slug }));
  try {
    const r = await api.cookiesLoginFinish(slug);
    cookieState.set((s) => ({ ...s, pending: null }));
    toast('success', `Đã kết nối ${label} (${r.cookieCount} cookie).`); // wording: BA review
  } catch (e) {
    const code = toAppError(e).code;
    if (code === 'cookie_required') {
      // wording: BA review
      toast('error', slug === 'douyin'
        ? 'Chưa nhận được phiên Douyin. Hãy mở một video trong cửa sổ Douyin, bấm phát, rồi bấm Xong.'
        : `Chưa nhận được phiên ${label}. Hãy đăng nhập trong cửa sổ ${label} rồi bấm Xong.`);
    } else if (code === 'cancelled') {
      cookieState.set((s) => ({ ...s, pending: null })); // the window was closed
      toast('info', `Cửa sổ ${label} đã đóng. Bấm Kết nối để thử lại.`); // wording: BA review
    } else {
      toast('error', errorMessage(code));
    }
  } finally {
    cookieState.set((s) => ({ ...s, busy: null }));
    void refreshCookieStatus();
  }
}

/** "Xoá": deletes the saved blob (and wipes an open login window). Consent is asked again next time. */
export async function clearPlatform(slug: string): Promise<void> {
  const label = platformLabelOf(slug);
  // wording: BA review
  const ok = await confirmDialog({ title: `Xoá kết nối ${label}?`, text: `VidGrab sẽ xoá phiên ${label} đã lưu trên máy này. Video cần đăng nhập sẽ không tải được cho đến khi bạn kết nối lại.`, okLabel: 'Xoá', danger: true });
  if (!ok) return;
  cookieState.set((s) => ({ ...s, busy: slug }));
  try {
    await api.cookiesClear(slug);
    setConsent(slug, false);
    cookieState.set((s) => ({ ...s, pending: s.pending === slug ? null : s.pending }));
    toast('info', `Đã xoá kết nối ${label}.`); // wording: BA review
  } catch (e) {
    toast('error', errorMessage(toAppError(e).code));
  } finally {
    cookieState.set((s) => ({ ...s, busy: null }));
    void refreshCookieStatus();
  }
}

let inited = false;
export async function initCookies() {
  if (inited) return;
  inited = true;
  await onLoginClosed((p) => {
    cookieState.set((s) => ({ ...s, pending: s.pending === p.platform ? null : s.pending }));
    void refreshCookieStatus();
  });
  await refreshCookieStatus(); // local and fast; the server switch is cached (FEATURES_KEY)
  void loadFeatures();
  // The server switch can change while the app sits in the tray.
  setInterval(() => void loadFeatures(), 30 * 60_000);
}

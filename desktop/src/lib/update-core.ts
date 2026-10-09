// Update gate (task #6125, PLAN-32E P1 step 3) — pure logic, no imports,
// so `npm test` can run it under plain node.

/** a < b for "0.8.0"-style versions (same rule as format.ts versionLess). */
export function versionLess(a: string, b: string): boolean {
  const pa = a.replace(/^v/, '').split('.').map((x) => parseInt(x, 10) || 0);
  const pb = b.replace(/^v/, '').split('.').map((x) => parseInt(x, 10) || 0);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const x = pa[i] ?? 0, y = pb[i] ?? 0;
    if (x !== y) return x < y;
  }
  return false;
}

export type UpdateInfo = {
  required: boolean;
  minSupported: string;
  latest: string;
  downloadUrl: string;
  detail: string;
};

export const NO_UPDATE: UpdateInfo = { required: false, minSupported: '', latest: '', downloadUrl: '', detail: '' };

const str = (v: unknown) => (typeof v === 'string' ? v.trim() : '');

/** From GET /client/version: this build is below the server's minimum. */
export function fromVersionInfo(appVersion: string | null, data: unknown): UpdateInfo {
  const d = (data && typeof data === 'object' ? data : {}) as Record<string, unknown>;
  const min = str(d.minSupported);
  if (!appVersion || !min) return NO_UPDATE;
  return {
    required: versionLess(appVersion, min),
    minSupported: min,
    latest: str(d.latest),
    downloadUrl: str(d.downloadUrl),
    detail: '',
  };
}

/** From any API answer: the server's 426 update_required (PLAN-32E). */
export function fromApiAnswer(status: number, data: unknown): UpdateInfo | null {
  if (status !== 426) return null;
  const d = (data && typeof data === 'object' ? data : {}) as Record<string, unknown>;
  if (d.error_code !== 'update_required') return null;
  return {
    required: true,
    minSupported: str(d.minSupported),
    latest: str(d.latest),
    downloadUrl: str(d.downloadUrl),
    detail: str(d.detail),
  };
}

/** Where the "Tải bản mới" button goes: the server's link only when the app
 * may open it (open_url allows the VidGrab hosts only), else the website's
 * download page. */
export function installLink(downloadUrl: string, website: string, allowedHosts: readonly string[]): string {
  try {
    const u = new URL(downloadUrl);
    if (u.protocol === 'https:' && allowedHosts.includes(u.hostname)) return u.href;
  } catch { /* empty or malformed */ }
  return `${website.replace(/\/$/, '')}/download`;
}

// ---- In-app update (task #6205) --------------------------------------------
// Rust's update_check / update_install (src-tauri/src/app_update.rs). The
// webview never sees the download URL or signature, only this.

export type InAppOffer = { available: boolean; version?: string; notes?: string };
export type InAppProgress = { downloaded: number; total?: number | null; percent?: number | null };

/** The version to offer with "Cập nhật ngay", or null: only a well-formed
 * answer naming a version newer than the running one. */
export function inAppVersion(appVersion: string | null, offer: unknown): string | null {
  const o = (offer && typeof offer === 'object' ? offer : {}) as Record<string, unknown>;
  const v = str(o.version);
  if (o.available !== true || !appVersion || !/^v?\d+\.\d+\.\d+$/.test(v)) return null;
  return versionLess(appVersion, v) ? v.replace(/^v/, '') : null;
}

/** 0..100, or null when the size is unknown. */
export function progressPercent(p: InAppProgress | null): number | null {
  if (!p) return null;
  if (typeof p.percent === 'number' && Number.isFinite(p.percent)) return Math.max(0, Math.min(100, Math.round(p.percent)));
  if (typeof p.total === 'number' && p.total > 0) return Math.max(0, Math.min(100, Math.floor((p.downloaded / p.total) * 100)));
  return null;
}

/** The updater cannot run here (dev browser, build without the feature):
 * the UI then behaves as before 0.11 (manual link only). */
export function updaterMissing(code: string | null | undefined): boolean {
  return code === 'updater_unavailable' || code === 'not_in_app';
}

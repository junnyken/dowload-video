// Machine code from Rust (device.rs): cached for the life of the app.
import { api } from './tauri';

export type DeviceInfo = { hash: string; code: string; displayName: string; source: 'machine' | 'fallback' };

let infoP: Promise<DeviceInfo | null> | null = null;
/** null when the command is unavailable (browser dev build without the mock); a failure is retried next call. */
export function deviceInfo(): Promise<DeviceInfo | null> {
  infoP ??= api.deviceInfo().then(
    (d) => (d && /^[0-9a-f]{64}$/.test(d.hash) ? d : null),
    () => null,
  ).then((d) => { if (!d) infoP = null; return d; });
  return infoP;
}

export async function deviceHash(): Promise<string | null> {
  return (await deviceInfo())?.hash ?? null;
}

let verP: Promise<string | null> | null = null;
export function appVersion(): Promise<string | null> {
  verP ??= api.getVersion().then((v) => v, () => null).then((v) => { if (!v) verP = null; return v; });
  return verP;
}

/** Headers every API call carries (X-VG-Device, X-VG-Client); empty when unknown. */
export async function vgHeaders(): Promise<Record<string, string>> {
  const [d, v] = await Promise.all([deviceInfo(), appVersion()]);
  const h: Record<string, string> = {};
  if (d) h['X-VG-Device'] = d.hash;
  if (v && /^[\x20-\x7e]{1,32}$/.test(v)) h['X-VG-Client'] = v;
  return h;
}

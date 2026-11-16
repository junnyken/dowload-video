// 0.22.0: wires subtrans-core to the Rust commands, the session token and the API host.
import { API_BASE } from './config';
import { api } from './tauri';
import { getAccessToken } from './auth';
import { vgHeaders } from './device';
import { translateSubtitle } from './subtrans-core';

export { SubtransError } from './subtrans-core';

const AUDIO = /\.(mp3|m4a|opus|ogg|wav|flac|aac)$/i;

/** Only videos carry subtitles. */
export const canTranslate = (filePath: string | null): filePath is string => !!filePath && !AUDIO.test(filePath);

export function translateToVi(filePath: string, onProgress?: (pct: number) => void): Promise<string> {
  return translateSubtitle(filePath, {
    list: api.subtitleList,
    read: api.subtitleRead,
    write: api.subtitleWrite,
    token: () => getAccessToken(),
    fetch: async (path, init) => {
      const ctl = new AbortController();
      if (init?.signal) {
        if (init.signal.aborted) ctl.abort();
        else init.signal.addEventListener('abort', () => ctl.abort(), { once: true });
      }
      const tm = setTimeout(() => ctl.abort(), 20_000);
      try {
        return await fetch(API_BASE + path, {
          ...init,
          signal: ctl.signal,
          headers: { ...(await vgHeaders()), ...(init.headers as Record<string, string>) },
        });
      } finally {
        clearTimeout(tm);
      }
    },
    sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
    onProgress,
  });
}

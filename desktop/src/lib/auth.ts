// Supabase auth. Since 0.4.0 the app never asks for the password: the user
// signs in on the website ("Đăng nhập qua trình duyệt", Rust browser_login,
// docs/desktop/C1-CONTRACT.md §5), which hands back a refresh token over a
// one-shot 127.0.0.1 callback; we swap it for this app's own session. The
// session lives ONLY in Windows Credential Manager through
// auth_save/auth_load/auth_clear (never localStorage).
import { createClient, type SupabaseClient, type Session } from '@supabase/supabase-js';
import { SUPABASE_ANON_KEY, SUPABASE_URL } from './config';
import { createStore } from './store';
import { api, loadMock, mockMode } from './tauri';
import { authErrorCode, toAppError, type AppError } from './errors';

export type AuthState = { status: 'loading' | 'out' | 'in'; email: string | null };
export const auth = createStore<AuthState>({ status: 'loading', email: null });

let client: SupabaseClient | null = null;
let mockToken: string | null = null;

const STORAGE_KEY = 'vidgrab-desktop-auth';

/** Only the session key goes to Credential Manager (auth_*); anything else supabase-js asks for stays in memory. */
function sessionStorageAdapter() {
  const mem = new Map<string, string>();
  return {
    getItem: async (k: string) => (k === STORAGE_KEY ? ((await api.authLoad()) ?? null) : (mem.get(k) ?? null)),
    setItem: async (k: string, v: string) => {
      if (k !== STORAGE_KEY) return void mem.set(k, v);
      await api.authSave(slimSession(v));
    },
    removeItem: async (k: string) => {
      if (k !== STORAGE_KEY) return void mem.delete(k);
      await api.authClear();
    },
  };
}

/** Windows Credential Manager blobs are small (2560 bytes), so keep only what supabase-js needs. */
function slimSession(raw: string): string {
  try {
    const s = JSON.parse(raw);
    const u = s.user ?? {};
    return JSON.stringify({
      access_token: s.access_token, refresh_token: s.refresh_token, token_type: s.token_type,
      expires_in: s.expires_in, expires_at: s.expires_at,
      user: { id: u.id, aud: u.aud, role: u.role, email: u.email, app_metadata: {}, user_metadata: {}, created_at: u.created_at },
    });
  } catch {
    return raw;
  }
}

function getClient(): SupabaseClient {
  client ??= createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    auth: {
      storageKey: STORAGE_KEY,
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: false,
      storage: sessionStorageAdapter(),
    },
  });
  return client;
}

function apply(s: Session | null) {
  auth.set(s?.user ? { status: 'in', email: s.user.email ?? null } : { status: 'out', email: null });
}

export async function initAuth() {
  if (mockMode) {
    auth.set({ status: 'out', email: null });
    return;
  }
  try {
    const c = getClient();
    c.auth.onAuthStateChange((_e, s) => apply(s));
    const { data } = await c.auth.getSession(); // refreshes an expired token when possible
    apply(data.session);
  } catch {
    auth.set({ status: 'out', email: null });
  }
}

/**
 * Opens the website's sign-in page in the default browser and waits (up to
 * 5 minutes) for it to hand back a refresh token, then swaps that token for
 * the app's own session (refresh tokens are single-use, so the copy that
 * passed through the browser is spent). Throws AppError (errors.ts); code
 * 'cancelled' when cancelBrowserSignIn() was called.
 */
export async function signInWithBrowser(): Promise<void> {
  let refreshToken: string;
  try {
    refreshToken = await api.browserLogin();
  } catch (e) {
    throw toAppError(e);
  }
  try {
    if (mockMode) {
      const m = await loadMock();
      const s = await m.mockExchange(refreshToken);
      mockToken = s.access_token;
      auth.set({ status: 'in', email: s.user.email });
      return;
    }
    const { error } = await getClient().auth.refreshSession({ refresh_token: refreshToken });
    if (error) throw error;
  } catch (e) {
    const err: AppError = { code: authErrorCode(e), message: (e as Error)?.message ?? '' };
    throw err;
  }
}

/** Stops waiting for the browser (closing the sign-in dialog). */
export function cancelBrowserSignIn(): void {
  api.cancelBrowserLogin().catch(() => { /* nothing waiting */ });
}

export async function signOut(): Promise<void> {
  mockToken = null;
  if (mockMode) {
    auth.set({ status: 'out', email: null });
    return;
  }
  try {
    // 'local': end only this app's session. Each device has its own session
    // since 0.4.0, so the default ('global') would also sign the user out of
    // the website and every other device.
    await getClient().auth.signOut({ scope: 'local' }); // clears storage via removeItem -> auth_clear
  } catch { /* offline: still drop the local session */ }
  try { await api.authClear(); } catch { /* ignore */ }
  auth.set({ status: 'out', email: null });
}

/** Fresh access token (supabase-js refreshes it when near expiry), or null. */
export async function getAccessToken(forceRefresh = false): Promise<string | null> {
  if (mockMode) return mockToken;
  try {
    const c = getClient();
    if (forceRefresh) {
      const { data } = await c.auth.refreshSession();
      return data.session?.access_token ?? null;
    }
    const { data } = await c.auth.getSession();
    return data.session?.access_token ?? null;
  } catch {
    return null;
  }
}

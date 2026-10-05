// Supabase email+password auth. The session lives ONLY in Windows Credential
// Manager through auth_save/auth_load/auth_clear (never localStorage).
import { createClient, type SupabaseClient, type Session } from '@supabase/supabase-js';
import { SUPABASE_ANON_KEY, SUPABASE_URL } from './config';
import { createStore } from './store';
import { api, loadMock, mockMode } from './tauri';
import { authErrorCode, type AppError } from './errors';

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

/** Throws AppError with a code understood by errors.ts. */
export async function signIn(email: string, password: string): Promise<void> {
  try {
    if (mockMode) {
      const m = await loadMock();
      const s = await m.mockSignIn(email, password);
      mockToken = s.access_token;
      auth.set({ status: 'in', email });
      return;
    }
    const { error } = await getClient().auth.signInWithPassword({ email, password });
    if (error) throw error;
  } catch (e) {
    const err: AppError = { code: authErrorCode(e), message: (e as Error)?.message ?? '' };
    throw err;
  }
}

export async function signOut(): Promise<void> {
  mockToken = null;
  if (mockMode) {
    auth.set({ status: 'out', email: null });
    return;
  }
  try {
    await getClient().auth.signOut(); // clears storage via removeItem -> auth_clear
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

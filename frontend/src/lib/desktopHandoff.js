/**
 * Browser sign-in for the VidGrab Windows app (task #6039).
 *
 * The app starts a one-shot listener on http://127.0.0.1:<port>/callback and
 * opens /desktop-login?port=<port>&state=<state>. This page signs the user in
 * here (captcha applies) and sends the app ONLY a refresh token, plus the
 * state, to that loopback address. The app swaps the refresh token for its own
 * session at once (refresh tokens are single-use), so the copy left in the
 * browser history is spent.
 *
 * The app gets its own Supabase session from a fresh password sign-in, never
 * the website's: Supabase refresh tokens are single-use and reusing one outside
 * the 10 s reuse window revokes the whole session
 * (https://supabase.com/docs/guides/auth/sessions), so sharing the website's
 * session would sign one of the two out.
 */
import { createClient } from '@supabase/supabase-js';

const PORT_RE = /^[1-9][0-9]{3,4}$/;
const STATE_RE = /^[0-9a-f]{64}$/;              // 32 random bytes from the app, lower-case hex
const REFRESH_RE = /^[A-Za-z0-9_-]{8,512}$/;

/** { port, state } from the query string, or null when anything is off. */
export function parseDesktopLoginParams(search) {
  const p = new URLSearchParams(search);
  const portRaw = p.get('port') || '';
  const state = p.get('state') || '';
  if (!PORT_RE.test(portRaw) || !STATE_RE.test(state)) return null;
  const port = Number(portRaw);
  if (port < 1024 || port > 65535) return null;
  return { port, state };
}

/** Only ever http://127.0.0.1:<validated port>/callback — never any other host. */
export function desktopCallbackUrl({ port, state }, refreshToken) {
  if (!REFRESH_RE.test(refreshToken || '')) throw new Error('unexpected refresh token format');
  const u = new URL('http://127.0.0.1/callback');
  u.port = String(port);
  u.searchParams.set('state', state);
  u.searchParams.set('refresh_token', refreshToken);
  return u.toString();
}

/**
 * Password sign-in on a throw-away client: nothing is persisted, the website's
 * own session (if any) is not touched, and the token is not auto-refreshed
 * here (the app does that).
 */
export async function signInForDesktop(email, password, captchaToken) {
  const mem = new Map();
  const client = createClient(
    import.meta.env.VITE_SUPABASE_URL || '',
    import.meta.env.VITE_SUPABASE_ANON_KEY || '',
    {
      auth: {
        storageKey: 'vidgrab-desktop-handoff',
        persistSession: false,
        autoRefreshToken: false,
        detectSessionInUrl: false,
        storage: {
          getItem: (k) => mem.get(k) ?? null,
          setItem: (k, v) => { mem.set(k, v); },
          removeItem: (k) => { mem.delete(k); },
        },
      },
    },
  );
  const { data, error } = await client.auth.signInWithPassword({
    email,
    password,
    ...(captchaToken ? { options: { captchaToken } } : {}),
  });
  return { refreshToken: data?.session?.refresh_token || '', error };
}

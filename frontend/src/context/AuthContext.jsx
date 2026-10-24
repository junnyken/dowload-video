import { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { supabase } from '../lib/supabaseClient';
import { API_BASE } from '../lib/apiBase';
import { adminSessionToken } from '../lib/adminSession';
import { installDeviceLink, onSignedOut } from '../lib/deviceLink';

installDeviceLink();

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser]               = useState(null);
  const [session, setSession]         = useState(null);
  const [loading, setLoading]         = useState(true);
  const [preferences, setPreferences] = useState(null);
  // Server signed this device out (too many devices on the account): message shown once.
  const [deviceNotice, setDeviceNotice] = useState('');

  useEffect(() => onSignedOut(async (message) => {
    setDeviceNotice(message || 'signed_out');
    // 'local': only this browser — the default ('global') would sign the
    // account out of every other device too.
    await supabase.auth.signOut({ scope: 'local' });
  }), []);

  // ── Load session on mount + subscribe to changes ─────────────────
  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      setSession(session);
      setUser(session?.user ?? null);
      setLoading(false);
    });

    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      (_event, session) => {
        setSession(session);
        setUser(session?.user ?? null);
      }
    );

    return () => subscription.unsubscribe();
  }, []);

  // ── Load preferences when user logs in ───────────────────────────
  useEffect(() => {
    if (!user) { setPreferences(null); return; }
    fetchPreferences();
  }, [user]);

  // ── Hand the session to the VidGrab browser extension ────────────
  // 22/10 (owner): signed in on the site, the extension still said "Khách"
  // (5 a day) for a Pro user — it only got a token after its own "Kết nối tài
  // khoản" button, and that token (Supabase, one hour) was never renewed. Now
  // the session goes to the extension whenever the person is signed in here,
  // and again when the extension says it is present (it may load after this
  // page); the extension swaps it for its own 30-day session
  // (backend app/core/extension_session.py) and skips the swap while it has one.
  //
  // We postMessage to our own origin only. The extension's web-bridge.js
  // content script is the sole listener, and it (plus the service worker)
  // re-verifies the origin, so this never exposes the token to another site.
  useEffect(() => {
    if (!session?.access_token) return undefined;
    const send = () => window.postMessage({
      __vg_source: 'webapp',
      type: 'VG_AUTH_TOKEN_FROM_WEB',
      token: session.access_token,
      email: session.user?.email || user?.email || '',
    }, window.location.origin);
    send();
    const onPresent = (event) => {
      if (event.source !== window || event.origin !== window.location.origin) return;
      if (event.data && event.data.__vg_source === 'extension' && event.data.type === 'VG_EXTENSION_PRESENT') send();
    };
    window.addEventListener('message', onPresent);
    return () => window.removeEventListener('message', onPresent);
  }, [session, user]);

  const fetchPreferences = useCallback(async () => {
    if (!session) return;
    try {
      const apiBase = API_BASE;
      const res = await fetch(`${apiBase}/api/v1/user/preferences`, {
        headers: { Authorization: `Bearer ${session.access_token}` },
      });
      if (res.ok) setPreferences(await res.json());
    } catch { /* non-critical */ }
  }, [session]);

  // ── Auth actions ──────────────────────────────────────────────────
  // captchaToken: Cloudflare Turnstile token from the form, or '' when the
  // widget is off (VITE_TURNSTILE_SITE_KEY empty). Supabase checks it only
  // once captcha protection is enabled in the dashboard; until then an absent
  // token is fine. See docs/runbooks/antispam-signup.md.
  const signUp = useCallback(async (email, password, displayName, captchaToken) => {
    // Without emailRedirectTo, Supabase falls back to the project's Site URL.
    // That is still the default http://localhost:3000, so every confirmation
    // email sent to a real user landed on ERR_CONNECTION_REFUSED — the account
    // was created and confirmed, but the person was dropped on a dead page and
    // had no way to tell it had worked.
    //
    // NOTE: Supabase only honours this when the URL is allow-listed under
    // Authentication -> URL Configuration -> Redirect URLs; otherwise it
    // silently reverts to the Site URL. Both need to be set for the prod
    // domain — see docs/RUNBOOK.md.
    const { data, error } = await supabase.auth.signUp({
      email,
      password,
      options: {
        data: { display_name: displayName },
        emailRedirectTo: `${window.location.origin}/`,
        ...(captchaToken ? { captchaToken } : {}),
      },
    });
    return { data, error };
  }, []);

  const signIn = useCallback(async (email, password, captchaToken) => {
    const { data, error } = await supabase.auth.signInWithPassword({
      email,
      password,
      ...(captchaToken ? { options: { captchaToken } } : {}),
    });
    return { data, error };
  }, []);

  const signOut = useCallback(async () => {
    // 24/10: 'local' — only this browser; the default ('global') signed the
    // account out of every other device and the Windows app as well.
    await supabase.auth.signOut({ scope: 'local' });
    setPreferences(null);
    // Don't leave a signed-out session live inside the extension.
    window.postMessage(
      { __vg_source: 'webapp', type: 'VG_AUTH_LOGOUT_FROM_WEB' },
      window.location.origin,
    );
  }, []);

  const resetPassword = useCallback(async (email, captchaToken) => {
    const { data, error } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: `${window.location.origin}/reset-password`,
      ...(captchaToken ? { captchaToken } : {}),
    });
    return { data, error };
  }, []);

  // Set a new password for the session Supabase establishes from a recovery
  // link. Kept here rather than in the page so the page never touches the
  // Supabase client directly, matching the rest of the auth surface.
  const updatePassword = useCallback(async (newPassword) => {
    const { data, error } = await supabase.auth.updateUser({ password: newPassword });
    return { data, error };
  }, []);

  const savePreferences = useCallback(async (updates) => {
    if (!session) return;
    try {
      const apiBase = API_BASE;
      await fetch(`${apiBase}/api/v1/user/preferences`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${session.access_token}`,
        },
        body: JSON.stringify(updates),
      });
      setPreferences(prev => ({ ...prev, ...updates }));
    } catch { /* non-critical */ }
  }, [session]);

  // ── Utility: add auth header to existing fetch options ───────────
  // Also carries a live admin-panel session (X-Admin-Token): admins have no
  // daily download limit on any platform.
  const withAuth = useCallback((opts = {}) => {
    const adminToken = adminSessionToken();
    if (!session?.access_token && !adminToken) return opts;
    return {
      ...opts,
      headers: {
        ...(opts.headers || {}),
        ...(session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {}),
        ...(adminToken ? { 'X-Admin-Token': adminToken } : {}),
      },
    };
  }, [session]);

  return (
    <AuthContext.Provider value={{
      user,
      session,
      loading,
      preferences,
      signUp,
      signIn,
      signOut,
      resetPassword,
      updatePassword,
      savePreferences,
      refetchPreferences: fetchPreferences,
      withAuth,
      isAuthenticated: !!user,
      deviceNotice,
      clearDeviceNotice: () => setDeviceNotice(''),
    }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
}

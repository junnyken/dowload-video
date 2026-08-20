import { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { supabase } from '../lib/supabaseClient';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser]               = useState(null);
  const [session, setSession]         = useState(null);
  const [loading, setLoading]         = useState(true);
  const [preferences, setPreferences] = useState(null);

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
  // The extension's "Kết nối tài khoản" button opens this app with
  // ?connect_extension=1 and then waits for a token. Nothing ever sent one,
  // so the extension stayed anonymous no matter how many times a user clicked
  // connect — history, archive and Pro quota were unreachable from it.
  //
  // We postMessage to our own origin only. The extension's web-bridge.js
  // content script is the sole listener, and it (plus the service worker)
  // re-verifies the origin, so this never exposes the token to another site.
  useEffect(() => {
    const wantsExtensionConnect =
      new URLSearchParams(window.location.search).get('connect_extension') === '1';
    if (!wantsExtensionConnect) return;
    if (!session?.access_token) return;   // wait until sign-in completes

    window.postMessage({
      __vg_source: 'webapp',
      type: 'VG_AUTH_TOKEN_FROM_WEB',
      token: session.access_token,
      email: session.user?.email || user?.email || '',
    }, window.location.origin);
  }, [session, user]);

  const fetchPreferences = useCallback(async () => {
    if (!session) return;
    try {
      const apiBase = localStorage.getItem('vg_api_base') || '';
      const res = await fetch(`${apiBase}/api/v1/user/preferences`, {
        headers: { Authorization: `Bearer ${session.access_token}` },
      });
      if (res.ok) setPreferences(await res.json());
    } catch { /* non-critical */ }
  }, [session]);

  // ── Auth actions ──────────────────────────────────────────────────
  const signUp = useCallback(async (email, password, displayName) => {
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
      },
    });
    return { data, error };
  }, []);

  const signIn = useCallback(async (email, password) => {
    const { data, error } = await supabase.auth.signInWithPassword({ email, password });
    return { data, error };
  }, []);

  const signOut = useCallback(async () => {
    await supabase.auth.signOut();
    setPreferences(null);
    // Don't leave a signed-out session live inside the extension.
    window.postMessage(
      { __vg_source: 'webapp', type: 'VG_AUTH_LOGOUT_FROM_WEB' },
      window.location.origin,
    );
  }, []);

  const resetPassword = useCallback(async (email) => {
    const { data, error } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: `${window.location.origin}/reset-password`,
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
      const apiBase = localStorage.getItem('vg_api_base') || '';
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
  const withAuth = useCallback((opts = {}) => {
    if (!session?.access_token) return opts;
    return {
      ...opts,
      headers: {
        ...(opts.headers || {}),
        Authorization: `Bearer ${session.access_token}`,
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

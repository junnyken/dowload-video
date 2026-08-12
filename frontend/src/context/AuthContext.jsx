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
    const { data, error } = await supabase.auth.signUp({
      email,
      password,
      options: { data: { display_name: displayName } },
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
  }, []);

  const resetPassword = useCallback(async (email) => {
    const { data, error } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: `${window.location.origin}/reset-password`,
    });
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

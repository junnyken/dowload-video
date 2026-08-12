import { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { useAuth } from './AuthContext';

const WorkspaceContext = createContext(null);

const API = import.meta.env.VITE_API_URL || '';
const LS_KEY = 'vg_active_workspace_id';

export function WorkspaceProvider({ children }) {
  const { session, isAuthenticated } = useAuth();
  const [workspaces,       setWorkspaces]       = useState([]);
  const [activeWorkspace,  setActiveWorkspace]  = useState(null);  // full workspace object
  const [myRole,           setMyRole]           = useState(null);  // 'owner'|'admin'|'editor'|'viewer'
  const [loading,          setLoading]          = useState(false);

  const headers = useCallback(() => ({
    Authorization: `Bearer ${session?.access_token}`,
    'Content-Type': 'application/json',
  }), [session]);

  // ── Load workspace list when authenticated ─────────────────────────
  const fetchWorkspaces = useCallback(async () => {
    if (!session?.access_token) return;
    setLoading(true);
    try {
      const r = await fetch(`${API}/api/v1/workspaces`, { headers: headers() });
      if (!r.ok) return;
      const d = await r.json();
      const list = d.workspaces || [];
      setWorkspaces(list);

      if (list.length === 0) return;

      // Restore last active workspace or default to personal
      const savedId = localStorage.getItem(LS_KEY);
      const saved   = savedId ? list.find(w => w.id === savedId) : null;
      const personal = list.find(w => w.type === 'personal');
      const target   = saved || personal || list[0];

      setActiveWorkspace(target);
      setMyRole(target?.role || null);
    } catch { /* non-critical */ }
    finally { setLoading(false); }
  }, [session, headers]);

  useEffect(() => {
    if (isAuthenticated) fetchWorkspaces();
    else { setWorkspaces([]); setActiveWorkspace(null); setMyRole(null); }
  }, [isAuthenticated, fetchWorkspaces]);

  // ── Switch active workspace ────────────────────────────────────────
  const switchWorkspace = useCallback((workspace) => {
    setActiveWorkspace(workspace);
    setMyRole(workspace?.role || null);
    localStorage.setItem(LS_KEY, workspace?.id || '');
  }, []);

  // ── Role helpers ──────────────────────────────────────────────────
  const HIERARCHY = { viewer: 1, editor: 2, admin: 3, owner: 4 };
  const hasRole = useCallback((minimum) => {
    return (HIERARCHY[myRole] || 0) >= (HIERARCHY[minimum] || 99);
  }, [myRole]);

  const can = useCallback((capability) => {
    const CAPS = {
      'archive.write':      ['editor', 'admin', 'owner'],
      'archive.delete':     ['admin', 'owner'],
      'schedule.write':     ['editor', 'admin', 'owner'],
      'members.invite':     ['admin', 'owner'],
      'workspace.settings': ['admin', 'owner'],
      'audit.read':         ['admin', 'owner'],
      'approvals.manage':   ['admin', 'owner'],
      'exports.create':     ['admin', 'owner'],
    };
    return (CAPS[capability] || ['owner']).includes(myRole);
  }, [myRole]);

  return (
    <WorkspaceContext.Provider value={{
      workspaces,
      activeWorkspace,
      myRole,
      loading,
      switchWorkspace,
      refetchWorkspaces: fetchWorkspaces,
      hasRole,
      can,
      isPersonal: activeWorkspace?.type === 'personal',
    }}>
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace() {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error('useWorkspace must be used inside WorkspaceProvider');
  return ctx;
}

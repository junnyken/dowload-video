import { useState, useRef, useEffect } from 'react';
import { Building2, ChevronDown, Check, Plus, User, Users, Briefcase } from 'lucide-react';
import { useWorkspace } from '../context/WorkspaceContext';
import { useAuth } from '../context/AuthContext';

const TYPE_ICON = {
  personal:   <User    className="w-3.5 h-3.5" />,
  team:       <Users   className="w-3.5 h-3.5" />,
  enterprise: <Briefcase className="w-3.5 h-3.5" />,
};

const ROLE_BADGE = {
  owner:  { label: 'Owner',  cls: 'text-amber-300 bg-amber-900/30 border-amber-700/40' },
  admin:  { label: 'Admin',  cls: 'text-blue-300  bg-blue-900/30  border-blue-700/40'  },
  editor: { label: 'Editor', cls: 'text-teal-300  bg-teal-900/30  border-teal-700/40'  },
  viewer: { label: 'Viewer', cls: 'text-slate-300 bg-slate-700/30 border-slate-600/40' },
};

export default function WorkspaceSwitcher({ onNavigate }) {
  const { workspaces, activeWorkspace, myRole, switchWorkspace, loading } = useWorkspace();
  const { session } = useAuth();
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [creating_busy, setCreatingBusy] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  if (!activeWorkspace || workspaces.length === 0) {
    if (loading) return <div className="h-7 w-28 rounded-md bg-slate-700/40 animate-pulse" />;
    return null;
  }

  const handleCreate = async () => {
    const name = newName.trim();
    if (!name || !session?.access_token) return;
    setCreatingBusy(true);
    try {
      const API = import.meta.env.VITE_API_URL || '';
      const r = await fetch(`${API}/api/v1/workspaces`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${session.access_token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, type: 'team' }),
      });
      if (r.ok) {
        const d = await r.json();
        switchWorkspace({ ...d.workspace, role: 'owner' });
        setCreating(false);
        setNewName('');
        setOpen(false);
      }
    } finally { setCreatingBusy(false); }
  };

  const badge = ROLE_BADGE[myRole] || ROLE_BADGE.viewer;
  const isPersonal = activeWorkspace.type === 'personal';

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-slate-600/50 bg-slate-800/40 hover:bg-slate-700/50 hover:border-[#FBBF24]/30 transition-colors text-sm text-slate-200 cursor-pointer max-w-[160px]"
      >
        <Building2 className="w-3.5 h-3.5 text-slate-400 flex-shrink-0" />
        <span className="truncate font-medium text-xs">
          {isPersonal ? 'Cá nhân' : activeWorkspace.name}
        </span>
        <ChevronDown className={`w-3 h-3 text-slate-400 flex-shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="absolute left-0 top-full mt-2 w-64 bg-[#021f1c] border border-slate-700/60 rounded-xl shadow-2xl z-50 overflow-hidden">
          {/* Header */}
          <div className="px-3 py-2.5 border-b border-slate-700/40">
            <p className="text-xs text-slate-400 font-medium uppercase tracking-wider">Workspace</p>
          </div>

          {/* List */}
          <div className="py-1 max-h-60 overflow-y-auto">
            {workspaces.map(ws => {
              const isActive = ws.id === activeWorkspace.id;
              const wsRole = ws.role || 'viewer';
              const rb = ROLE_BADGE[wsRole] || ROLE_BADGE.viewer;
              return (
                <button
                  key={ws.id}
                  onClick={() => { switchWorkspace(ws); setOpen(false); }}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 text-left hover:bg-slate-700/40 transition-colors cursor-pointer
                    ${isActive ? 'bg-slate-700/25' : ''}`}
                >
                  <div className={`w-7 h-7 rounded-lg flex items-center justify-center text-xs font-bold flex-shrink-0
                    ${ws.type === 'personal' ? 'bg-[#FBBF24]/15 text-[#FBBF24]' : 'bg-teal-900/40 text-teal-300'}`}>
                    {TYPE_ICON[ws.type] || <Building2 className="w-3.5 h-3.5" />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className={`text-sm font-medium truncate ${isActive ? 'text-white' : 'text-slate-200'}`}>
                      {ws.type === 'personal' ? 'Cá nhân' : ws.name}
                    </p>
                    <span className={`inline-flex items-center text-[10px] font-semibold px-1.5 py-0.5 rounded border leading-none ${rb.cls}`}>
                      {rb.label}
                    </span>
                  </div>
                  {isActive && <Check className="w-3.5 h-3.5 text-[#FBBF24] flex-shrink-0" />}
                </button>
              );
            })}
          </div>

          {/* Create new */}
          <div className="border-t border-slate-700/40 p-2">
            {creating ? (
              <div className="flex gap-1.5">
                <input
                  autoFocus
                  value={newName}
                  onChange={e => setNewName(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') handleCreate(); if (e.key === 'Escape') setCreating(false); }}
                  placeholder="Tên workspace..."
                  className="flex-1 bg-slate-800/60 border border-slate-600/50 rounded-lg px-2.5 py-1.5 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-[#FBBF24]/50"
                />
                <button
                  onClick={handleCreate}
                  disabled={creating_busy || !newName.trim()}
                  className="px-2.5 py-1.5 rounded-lg bg-[#FBBF24]/20 text-[#FBBF24] text-xs font-bold disabled:opacity-40 hover:bg-[#FBBF24]/30 transition-colors cursor-pointer"
                >
                  {creating_busy ? '...' : 'Tạo'}
                </button>
              </div>
            ) : (
              <button
                onClick={() => setCreating(true)}
                className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg text-slate-400 hover:text-white hover:bg-slate-700/40 text-xs transition-colors cursor-pointer"
              >
                <Plus className="w-3.5 h-3.5" />
                Tạo workspace mới
              </button>
            )}
          </div>

          {/* Workspace settings link */}
          {onNavigate && (
            <div className="border-t border-slate-700/40 p-2">
              <button
                onClick={() => { setOpen(false); onNavigate('workspace-settings', '/workspace-settings'); }}
                className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg text-slate-400 hover:text-white hover:bg-slate-700/40 text-xs transition-colors cursor-pointer"
              >
                <Building2 className="w-3.5 h-3.5" />
                Cài đặt Workspace
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

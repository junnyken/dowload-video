import { useState, useEffect, useCallback } from 'react';
import { Building2, Users, Mail, Shield, Trash2, X, Check, AlertCircle } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { useWorkspace } from '../context/WorkspaceContext';

const API = import.meta.env.VITE_API_URL || '';

const TABS = ['Tổng quan', 'Thành viên', 'Lời mời'];

const ROLE_LABELS = { owner: 'Owner', admin: 'Admin', editor: 'Editor', viewer: 'Viewer' };
const ROLE_COLORS = {
  owner:  'text-amber-300 bg-amber-900/30 border-amber-700/40',
  admin:  'text-blue-300 bg-blue-900/30 border-blue-700/40',
  editor: 'text-teal-300 bg-teal-900/30 border-teal-700/40',
  viewer: 'text-slate-300 bg-slate-700/30 border-slate-600/40',
};

export default function WorkspaceSettingsPage() {
  const { session } = useAuth();
  const { activeWorkspace, myRole, refetchWorkspaces, can } = useWorkspace();
  const [tab, setTab]     = useState(0);
  const [members, setMembers]   = useState([]);
  const [invites, setInvites]   = useState([]);
  const [loading, setLoading]   = useState(false);
  const [saving, setSaving]     = useState(false);
  const [saveDone, setSaveDone] = useState(false);
  const [error, setError]       = useState('');

  // General tab state
  const [wsName, setWsName] = useState('');

  // Invite form state
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole]   = useState('editor');
  const [inviting, setInviting]       = useState(false);

  const wsId = activeWorkspace?.id;
  const headers = useCallback(() => ({
    Authorization: `Bearer ${session?.access_token}`,
    'Content-Type': 'application/json',
  }), [session]);

  useEffect(() => {
    if (activeWorkspace) setWsName(activeWorkspace.name || '');
  }, [activeWorkspace]);

  const fetchMembers = useCallback(async () => {
    if (!wsId || !session?.access_token) return;
    setLoading(true);
    try {
      const r = await fetch(`${API}/api/v1/workspaces/${wsId}/members`, { headers: headers() });
      if (r.ok) { const d = await r.json(); setMembers(d.members || []); }
    } finally { setLoading(false); }
  }, [wsId, session, headers]);

  const fetchInvites = useCallback(async () => {
    if (!wsId || !session?.access_token) return;
    try {
      const r = await fetch(`${API}/api/v1/workspaces/${wsId}/invites`, { headers: headers() });
      if (r.ok) { const d = await r.json(); setInvites(d.invites || []); }
    } catch {}
  }, [wsId, session, headers]);

  useEffect(() => {
    if (tab === 1) fetchMembers();
    if (tab === 2) fetchInvites();
  }, [tab, fetchMembers, fetchInvites]);

  const handleSaveName = async () => {
    if (!wsId || !wsName.trim()) return;
    setSaving(true);
    setError('');
    try {
      const r = await fetch(`${API}/api/v1/workspaces/${wsId}`, {
        method: 'PATCH',
        headers: headers(),
        body: JSON.stringify({ name: wsName.trim() }),
      });
      if (r.ok) { setSaveDone(true); refetchWorkspaces(); setTimeout(() => setSaveDone(false), 2500); }
      else { const d = await r.json(); setError(d.detail?.user_message || 'Lỗi khi lưu.'); }
    } finally { setSaving(false); }
  };

  const handleRoleChange = async (memberId, newRole) => {
    if (!wsId) return;
    const r = await fetch(`${API}/api/v1/workspaces/${wsId}/members/${memberId}`, {
      method: 'PATCH',
      headers: headers(),
      body: JSON.stringify({ role: newRole }),
    });
    if (r.ok) fetchMembers();
  };

  const handleRemoveMember = async (memberId) => {
    if (!wsId) return;
    const r = await fetch(`${API}/api/v1/workspaces/${wsId}/members/${memberId}`, {
      method: 'DELETE',
      headers: headers(),
    });
    if (r.ok) fetchMembers();
  };

  const handleInvite = async () => {
    const email = inviteEmail.trim();
    if (!email || !wsId) return;
    setInviting(true);
    setError('');
    try {
      const r = await fetch(`${API}/api/v1/workspaces/${wsId}/invites`, {
        method: 'POST',
        headers: headers(),
        body: JSON.stringify({ email, role: inviteRole }),
      });
      if (r.ok) { setInviteEmail(''); fetchInvites(); }
      else { const d = await r.json(); setError(d.detail?.user_message || 'Lỗi khi gửi lời mời.'); }
    } finally { setInviting(false); }
  };

  const handleRevokeInvite = async (inviteId) => {
    if (!wsId) return;
    const r = await fetch(`${API}/api/v1/workspaces/${wsId}/invites/${inviteId}`, {
      method: 'DELETE', headers: headers(),
    });
    if (r.ok) fetchInvites();
  };

  if (!activeWorkspace) {
    return (
      <div className="max-w-2xl mx-auto px-4 py-12 text-center text-slate-400">
        Chưa có workspace nào.
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto px-4 md:px-8 py-8 md:py-12">
      <div className="flex items-center gap-3 mb-6">
        <Building2 className="w-5 h-5 text-[#FBBF24]" />
        <h1 className="text-xl font-bold text-white">Cài đặt Workspace</h1>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-6 border-b border-slate-700/50">
        {TABS.map((t, i) => (
          <button
            key={t}
            onClick={() => setTab(i)}
            className={`px-4 py-2.5 text-sm font-medium transition-colors cursor-pointer -mb-px border-b-2
              ${tab === i ? 'border-[#FBBF24] text-[#FBBF24]' : 'border-transparent text-slate-400 hover:text-slate-200'}`}
          >
            {t}
          </button>
        ))}
      </div>

      {error && (
        <div className="flex items-center gap-2 mb-4 px-3 py-2 rounded-lg bg-red-900/30 border border-red-700/40 text-red-300 text-sm">
          <AlertCircle className="w-4 h-4 flex-shrink-0" />
          {error}
        </div>
      )}

      {/* ── Tab: Tổng quan ─────────────────────────────── */}
      {tab === 0 && (
        <div className="space-y-6">
          <div className="bg-slate-800/30 rounded-xl border border-slate-700/50 p-5">
            <label className="block text-sm font-medium text-slate-300 mb-2">Tên Workspace</label>
            {can('workspace.settings') ? (
              <div className="flex gap-2">
                <input
                  value={wsName}
                  onChange={e => setWsName(e.target.value)}
                  className="flex-1 bg-slate-900/60 border border-slate-600/50 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-[#FBBF24]/60 placeholder-slate-500"
                />
                <button
                  onClick={handleSaveName}
                  disabled={saving || wsName.trim() === activeWorkspace.name}
                  className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[#FBBF24]/20 text-[#FBBF24] text-sm font-semibold hover:bg-[#FBBF24]/30 disabled:opacity-40 transition-colors cursor-pointer"
                >
                  {saveDone ? <Check className="w-4 h-4" /> : saving ? '...' : 'Lưu'}
                </button>
              </div>
            ) : (
              <p className="text-white text-sm">{activeWorkspace.name}</p>
            )}
          </div>

          <div className="bg-slate-800/30 rounded-xl border border-slate-700/50 p-5 space-y-3">
            <h3 className="text-sm font-medium text-slate-300">Thông tin</h3>
            <InfoRow label="Loại" value={activeWorkspace.type === 'personal' ? 'Cá nhân' : activeWorkspace.type === 'team' ? 'Team' : 'Enterprise'} />
            <InfoRow label="Vai trò của bạn">
              <span className={`text-xs font-semibold px-2 py-0.5 rounded border ${ROLE_COLORS[myRole] || ROLE_COLORS.viewer}`}>
                {ROLE_LABELS[myRole] || myRole}
              </span>
            </InfoRow>
            <InfoRow label="Slug" value={activeWorkspace.slug || '—'} />
            <InfoRow label="ID" value={<span className="font-mono text-xs text-slate-400">{activeWorkspace.id}</span>} />
          </div>
        </div>
      )}

      {/* ── Tab: Thành viên ────────────────────────────── */}
      {tab === 1 && (
        <div>
          {loading ? (
            <div className="space-y-2">
              {[...Array(3)].map((_, i) => <div key={i} className="h-14 rounded-xl bg-slate-800/30 animate-pulse" />)}
            </div>
          ) : (
            <div className="space-y-2">
              {members.map(m => (
                <div key={m.user_id} className="flex items-center gap-3 px-4 py-3 rounded-xl bg-slate-800/30 border border-slate-700/40">
                  <div className="w-8 h-8 rounded-full bg-gradient-to-br from-[#FBBF24] to-[#FB923C] flex items-center justify-center text-[#012622] text-xs font-bold flex-shrink-0">
                    {(m.display_name || m.email || '?')[0].toUpperCase()}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-white font-medium truncate">{m.display_name || m.email}</p>
                    <p className="text-xs text-slate-400 truncate">{m.email}</p>
                  </div>
                  {can('workspace.settings') && m.role !== 'owner' ? (
                    <select
                      value={m.role}
                      onChange={e => handleRoleChange(m.user_id, e.target.value)}
                      className="text-xs bg-slate-900/60 border border-slate-600/50 rounded-lg px-2 py-1 text-slate-300 focus:outline-none cursor-pointer"
                    >
                      {Object.entries(ROLE_LABELS).filter(([k]) => k !== 'owner').map(([k, v]) => (
                        <option key={k} value={k}>{v}</option>
                      ))}
                    </select>
                  ) : (
                    <span className={`text-xs font-semibold px-2 py-0.5 rounded border ${ROLE_COLORS[m.role] || ROLE_COLORS.viewer}`}>
                      {ROLE_LABELS[m.role] || m.role}
                    </span>
                  )}
                  {can('workspace.settings') && m.role !== 'owner' && (
                    <button
                      onClick={() => handleRemoveMember(m.user_id)}
                      className="p-1.5 rounded-lg text-slate-500 hover:text-red-400 hover:bg-red-900/20 transition-colors cursor-pointer"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Tab: Lời mời ──────────────────────────────── */}
      {tab === 2 && (
        <div className="space-y-5">
          {can('members.invite') && (
            <div className="bg-slate-800/30 rounded-xl border border-slate-700/50 p-5">
              <h3 className="text-sm font-medium text-slate-300 mb-3 flex items-center gap-2">
                <Mail className="w-4 h-4" /> Gửi lời mời
              </h3>
              <div className="flex gap-2">
                <input
                  value={inviteEmail}
                  onChange={e => setInviteEmail(e.target.value)}
                  placeholder="email@example.com"
                  onKeyDown={e => e.key === 'Enter' && handleInvite()}
                  className="flex-1 bg-slate-900/60 border border-slate-600/50 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-[#FBBF24]/60 placeholder-slate-500"
                />
                <select
                  value={inviteRole}
                  onChange={e => setInviteRole(e.target.value)}
                  className="bg-slate-900/60 border border-slate-600/50 rounded-lg px-2 py-2 text-slate-300 text-sm focus:outline-none cursor-pointer"
                >
                  <option value="viewer">Viewer</option>
                  <option value="editor">Editor</option>
                  <option value="admin">Admin</option>
                </select>
                <button
                  onClick={handleInvite}
                  disabled={inviting || !inviteEmail.trim()}
                  className="px-4 py-2 rounded-lg bg-[#FBBF24]/20 text-[#FBBF24] text-sm font-semibold hover:bg-[#FBBF24]/30 disabled:opacity-40 transition-colors cursor-pointer"
                >
                  {inviting ? '...' : 'Mời'}
                </button>
              </div>
            </div>
          )}

          {/* Pending invites */}
          <div className="space-y-2">
            {invites.length === 0 ? (
              <p className="text-center text-slate-500 text-sm py-8">Không có lời mời nào đang chờ.</p>
            ) : invites.map(inv => (
              <div key={inv.id} className="flex items-center gap-3 px-4 py-3 rounded-xl bg-slate-800/30 border border-slate-700/40">
                <Mail className="w-4 h-4 text-slate-400 flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-white truncate">{inv.email}</p>
                  <p className="text-xs text-slate-400">
                    {ROLE_LABELS[inv.role] || inv.role} · Hết hạn {new Date(inv.expires_at).toLocaleDateString('vi-VN')}
                  </p>
                </div>
                {can('members.invite') && (
                  <button
                    onClick={() => handleRevokeInvite(inv.id)}
                    className="p-1.5 rounded-lg text-slate-500 hover:text-red-400 hover:bg-red-900/20 transition-colors cursor-pointer"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function InfoRow({ label, value, children }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-xs text-slate-400 flex-shrink-0">{label}</span>
      <span className="text-xs text-slate-200 text-right">{children || value}</span>
    </div>
  );
}

import { useState, useRef, useEffect } from 'react';
import { History, Settings2, BarChart2, LogOut, ChevronDown, Crown, ListVideo, Archive, Calendar, Building2, Shield, ClipboardCheck, Languages, Mic } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { useWorkspace } from '../context/WorkspaceContext';

export default function AccountMenu({ onNavigate }) {
  const { user, session, signOut, isAuthenticated } = useAuth();
  const { can } = useWorkspace();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  // Quota state
  const [usage, setUsage] = useState(null); // { used: number, limit: number, tier: 'free'|'pro' }

  // Close on outside click
  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  // Fetch quota when dropdown opens
  useEffect(() => {
    if (!open || !session?.access_token) return;
    const apiBase = localStorage.getItem('vg_api_base') || '';
    fetch(`${apiBase}/api/v1/user/usage`, {
      headers: { Authorization: `Bearer ${session.access_token}` },
    })
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(data => {
        // Accept either { used, limit, tier } or { downloads_today, daily_limit, tier }
        const tier = (data.tier || data.plan || 'free').toLowerCase();
        const rawLimit = data.limit ?? data.daily_limit ?? 10;
        setUsage({
          used:      data.used ?? data.downloads_today ?? 0,
          limit:     rawLimit,
          unlimited: rawLimit === -1 || ['enterprise', 'team'].includes(tier),
          tier,
        });
      })
      .catch(() => setUsage(null));
  }, [open, session]);

  if (!isAuthenticated) return null;

  const displayName = user?.user_metadata?.display_name
    || user?.email?.split('@')[0]
    || 'Tài khoản';

  const avatar = user?.user_metadata?.avatar_url;

  const handleSignOut = async () => {
    setOpen(false);
    await signOut();
  };

  const nav = (view, path) => { setOpen(false); onNavigate(view, path); };

  return (
    <div className="relative" ref={ref}>
      {/* Trigger button */}
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg border border-slate-600/50 hover:border-[#FBBF24]/40 bg-slate-800/40 hover:bg-slate-700/50 transition-colors cursor-pointer"
      >
        {avatar ? (
          <img src={avatar} alt="" className="w-6 h-6 rounded-full object-cover" />
        ) : (
          <div className="w-6 h-6 rounded-full bg-gradient-to-br from-[#FBBF24] to-[#FB923C] flex items-center justify-center text-[#012622] text-xs font-bold">
            {displayName[0].toUpperCase()}
          </div>
        )}
        <span className="text-sm text-white font-medium hidden sm:block max-w-[100px] truncate">
          {displayName}
        </span>
        <ChevronDown className={`w-3.5 h-3.5 text-slate-400 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {/* Dropdown */}
      {open && (
        <div className="absolute right-0 top-full mt-2 w-52 bg-[#021f1c] border border-slate-700/60 rounded-xl shadow-2xl overflow-hidden z-50">
          {/* User info */}
          <div className="px-4 py-3 border-b border-slate-700/40">
            <div className="flex items-center gap-2">
              <p className="text-white text-sm font-semibold truncate">{displayName}</p>
              {usage?.tier === 'pro' && (
                <span className="flex-shrink-0 text-[10px] font-bold px-1.5 py-0.5 rounded bg-violet-600/30 text-violet-300 border border-violet-500/40 leading-none">PRO</span>
              )}
              {(usage?.tier === 'enterprise' || usage?.tier === 'team') && (
                <span className="flex-shrink-0 text-[10px] font-bold px-1.5 py-0.5 rounded bg-amber-600/30 text-amber-300 border border-amber-500/40 leading-none">
                  {usage.tier === 'enterprise' ? 'ENTERPRISE' : 'TEAM'}
                </span>
              )}
            </div>
            <p className="text-slate-400 text-xs truncate">{user?.email}</p>
          </div>

          {/* Quota bar */}
          {usage && (
            <div className="px-4 py-2.5 border-b border-slate-700/40">
              <p className="text-xs text-slate-400 mb-1.5">
                <span className="text-white font-medium">{usage.used}</span>
                {usage.unlimited
                  ? <span className="text-amber-400 font-medium"> / Không giới hạn</span>
                  : <> / {usage.limit} lượt hôm nay</>
                }
              </p>
              <div className="h-1 w-full rounded-full bg-zinc-700 overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${
                    usage.tier === 'enterprise' ? 'bg-amber-400' :
                    usage.tier === 'team' ? 'bg-green-500' :
                    usage.tier === 'pro' ? 'bg-violet-500' : 'bg-blue-500'
                  }`}
                  style={{ width: usage.unlimited ? '100%' : `${Math.min(100, Math.round((usage.used / usage.limit) * 100))}%` }}
                />
              </div>
            </div>
          )}

          {/* Nav items */}
          <div className="py-1">
            <MenuItem icon={<History className="w-4 h-4" />} label="Lịch sử tải"
              onClick={() => nav('history', '/history')} />
            <MenuItem icon={<Archive className="w-4 h-4" />} label="Archive"
              onClick={() => nav('archive', '/archive')} />
            <MenuItem icon={<Calendar className="w-4 h-4" />} label="Lịch Tải"
              onClick={() => nav('schedule', '/schedule')} />
            <MenuItem icon={<ListVideo className="w-4 h-4" />} label="Playlists"
              onClick={() => nav('playlists', '/playlists')} />
            <MenuItem icon={<BarChart2 className="w-4 h-4" />} label="Analytics"
              onClick={() => nav('analytics', '/analytics')} />
            <MenuItem icon={<Languages className="w-4 h-4" />} label="Dịch Phụ Đề"
              onClick={() => nav('transcript-translate', '/transcript-translate')} />
            <MenuItem icon={<Mic className="w-4 h-4" />} label="Tạo Phụ Đề (AI)"
              onClick={() => nav('transcript-asr', '/transcript-asr')} />
          </div>
          <div className="py-1 border-t border-slate-700/40">
            <MenuItem icon={<Settings2 className="w-4 h-4" />} label="Preferences"
              onClick={() => nav('preferences', '/preferences')} />
            <MenuItem icon={<BarChart2 className="w-4 h-4" />} label="Quota & Usage"
              onClick={() => nav('usage', '/usage')} />
          </div>
          {(can('workspace.settings') || can('audit.read') || can('approvals.manage')) && (
            <div className="py-1 border-t border-slate-700/40">
              {can('workspace.settings') && (
                <MenuItem icon={<Building2 className="w-4 h-4" />} label="Workspace"
                  onClick={() => nav('workspace-settings', '/workspace-settings')} />
              )}
              {can('audit.read') && (
                <MenuItem icon={<Shield className="w-4 h-4" />} label="Audit Log"
                  onClick={() => nav('audit', '/audit')} />
              )}
              {can('approvals.manage') && (
                <MenuItem icon={<ClipboardCheck className="w-4 h-4" />} label="Phê duyệt"
                  onClick={() => nav('approvals', '/approvals')} />
              )}
            </div>
          )}

          {/* Upgrade hint — chỉ hiện cho free users */}
          {(!usage || (usage.tier === 'free')) && (
            <div className="px-3 py-2 border-t border-slate-700/40">
              <button
                onClick={() => nav('upgrade', '/upgrade')}
                className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg bg-gradient-to-r from-[#FBBF24]/10 to-[#FB923C]/10 border border-[#FBBF24]/20 hover:border-[#FBBF24]/40 transition-colors text-left"
              >
                <Crown className="w-3.5 h-3.5 text-[#FBBF24]" />
                <span className="text-[#FBBF24] text-xs font-semibold">Nâng cấp Pro</span>
              </button>
            </div>
          )}

          {/* Sign out */}
          <div className="py-1 border-t border-slate-700/40">
            <MenuItem icon={<LogOut className="w-4 h-4" />} label="Đăng xuất"
              onClick={handleSignOut} danger />
          </div>
        </div>
      )}
    </div>
  );
}

function MenuItem({ icon, label, onClick, danger }) {
  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-3 px-4 py-2.5 text-sm transition-colors text-left cursor-pointer
        ${danger
          ? 'text-red-400 hover:bg-red-500/10'
          : 'text-slate-300 hover:bg-slate-700/40 hover:text-white'
        }`}
    >
      {icon}
      {label}
    </button>
  );
}

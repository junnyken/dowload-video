import {
  LayoutDashboard,
  History,
  Download,
  Settings,
  ChevronLeft,
  ChevronRight,
  Video,
  Layers,
  Shield,
  Archive,
} from 'lucide-react';

const navItems = [
  { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { id: 'bulk', label: 'Bulk Download', icon: Layers },
  { id: 'history', label: 'History', icon: History },
  { id: 'archive', label: 'Archive', icon: Archive },
  { id: 'settings', label: 'Settings', icon: Settings },
  { id: 'admin', label: 'Admin Panel', icon: Shield },
];

export default function Sidebar({ activeTab, onTabChange, collapsed, onCollapse }) {
  return (
    <aside
      className={`
        hidden md:flex flex-col
        fixed top-0 left-0 h-screen z-40
        bg-surface-2 border-r border-line
        transition-all duration-300 ease-in-out
        ${collapsed ? 'w-[72px]' : 'w-[260px]'}
      `}
    >
      {/* ── Logo / Brand ──────────────────────────────── */}
      <div className="flex items-center gap-3 px-5 py-6 border-b border-line">
        <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-accent shadow-lg flex-shrink-0">
          <Video className="w-5 h-5 text-accent-fg" />
        </div>
        {!collapsed && (
          <div className="overflow-hidden transition-all duration-300">
            <h1 className="text-lg font-bold text-fg tracking-tight">
              VidGrab
            </h1>
            <p className="text-[11px] text-fg-muted leading-none">
              Video Downloader
            </p>
          </div>
        )}
      </div>

      {/* ── Navigation ────────────────────────────────── */}
      <nav className="flex-1 px-3 py-4 space-y-1">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = activeTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onTabChange(item.id)}
              title={collapsed ? item.label : undefined}
              className={`
                group flex items-center gap-3 w-full
                px-3 py-2.5 rounded-xl
                text-sm font-medium
                transition-all duration-200 cursor-pointer
                ${
                  isActive
                    ? 'bg-accent/15 text-primary-light shadow-sm'
                    : 'text-fg-2 hover:bg-surface-2/60 hover:text-fg'
                }
              `}
            >
              <Icon
                className={`w-5 h-5 flex-shrink-0 transition-colors duration-200 ${
                  isActive
                    ? 'text-primary-light'
                    : 'text-fg-muted group-hover:text-fg-2'
                }`}
              />
              {!collapsed && (
                <span className="transition-all duration-300">{item.label}</span>
              )}
              {isActive && !collapsed && (
                <div className="ml-auto w-1.5 h-1.5 rounded-full bg-primary-light animate-pulse" />
              )}
            </button>
          );
        })}
      </nav>

      {/* ── Download Stats (Bottom) ───────────────────── */}
      {!collapsed && (
        <div className="mx-3 mb-4 p-4 rounded-xl bg-accent-soft border border-accent/20">
          <div className="flex items-center gap-2 mb-2">
            <Download className="w-4 h-4 text-accent-text" />
            <span className="text-xs font-semibold text-fg-2 uppercase tracking-wider">
              Quick Stats
            </span>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <p className="text-xl font-bold text-fg">0</p>
              <p className="text-[10px] text-fg-muted">Total</p>
            </div>
            <div>
              <p className="text-xl font-bold text-success">0</p>
              <p className="text-[10px] text-fg-muted">Success</p>
            </div>
          </div>
        </div>
      )}

      {/* ── Collapse Toggle ───────────────────────────── */}
      <button
        onClick={() => onCollapse(!collapsed)}
        className="
          flex items-center justify-center
          mx-3 mb-4 py-2 rounded-xl
          border border-line
          text-fg-muted hover:text-fg-2
          hover:bg-surface-2/50
          transition-all duration-200 cursor-pointer
        "
      >
        {collapsed ? (
          <ChevronRight className="w-4 h-4" />
        ) : (
          <ChevronLeft className="w-4 h-4" />
        )}
      </button>
    </aside>
  );
}

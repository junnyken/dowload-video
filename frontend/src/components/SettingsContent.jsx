import { Settings, Server, Palette } from 'lucide-react';

export default function SettingsContent() {
  return (
    <div className="space-y-6">
      {/* ── Header ────────────────────────────────────── */}
      <div>
        <div className="flex items-center gap-3 mb-1">
          <Settings className="w-5 h-5 text-accent-light" />
          <h2 className="text-2xl font-bold text-text-primary tracking-tight">
            Settings
          </h2>
        </div>
        <p className="text-sm text-text-muted ml-8">
          Configure your download preferences
        </p>
      </div>

      {/* ── Settings Cards ────────────────────────────── */}
      <div className="space-y-4">
        {/* API Connection */}
        <div className="p-6 rounded-2xl bg-surface-card border border-border shadow-lg">
          <div className="flex items-center gap-3 mb-4">
            <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-gradient-to-br from-success to-emerald-600">
              <Server className="w-4 h-4 text-white" />
            </div>
            <div>
              <h3 className="text-base font-semibold text-text-primary">
                API Connection
              </h3>
              <p className="text-xs text-text-muted">
                Backend server status
              </p>
            </div>
            <div className="ml-auto flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-text-muted animate-pulse" />
              <span className="text-xs font-medium text-text-muted">
                Not Connected
              </span>
            </div>
          </div>
          <p className="text-xs text-text-muted italic">
            Connection settings will be available in a future update.
          </p>
        </div>

        {/* Appearance */}
        <div className="p-6 rounded-2xl bg-surface-card border border-border shadow-lg">
          <div className="flex items-center gap-3 mb-4">
            <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-gradient-to-br from-primary to-accent">
              <Palette className="w-4 h-4 text-white" />
            </div>
            <div>
              <h3 className="text-base font-semibold text-text-primary">
                Appearance
              </h3>
              <p className="text-xs text-text-muted">
                Customize the look and feel
              </p>
            </div>
          </div>
          <p className="text-xs text-text-muted italic">
            Theme customization will be available in a future update.
          </p>
        </div>
      </div>
    </div>
  );
}

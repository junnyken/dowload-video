import { useState, useEffect } from 'react';
import {
  Settings, Server, Palette, Bell, BellOff, Shield,
  Clock, Database, FileDown, Key, Info, ExternalLink,
  CheckCircle2, XCircle, AlertCircle,
} from 'lucide-react';
import { getPermission, requestPermission, isSupported } from '../utils/notifications.js';

const API_BASE = import.meta.env.VITE_API_URL || '';

const RETENTION_RULES = [
  { label: 'File tải về (chưa ghim)', value: '~60 phút', note: 'Tự xóa sau khi tải xong. Ghim vào Archive để giữ lại.' },
  { label: 'File đã ghim (Archive)', value: 'Vô hạn', note: 'Giữ nguyên cho đến khi bạn xóa.' },
  { label: 'Metadata lịch sử (miễn phí)', value: '30 ngày', note: 'Xuất CSV để giữ bản ghi lâu hơn.' },
  { label: 'Metadata lịch sử (Pro)', value: '365 ngày', note: 'Tự động xóa sau 1 năm.' },
  { label: 'Metadata Archive', value: 'Vô hạn', note: 'Dữ liệu mô tả giữ nguyên dù file đã hết hạn.' },
];

export default function SettingsContent() {
  const [appVersion, setAppVersion] = useState(null);
  const [apiOk, setApiOk] = useState(null);
  const [notifPerm, setNotifPerm] = useState(getPermission());
  const [apiKey, setApiKey] = useState(null);
  const [apiKeyLoading, setApiKeyLoading] = useState(false);

  useEffect(() => {
    fetch(`${API_BASE}/health`)
      .then(r => r.json())
      .then(d => { setApiOk(true); setAppVersion(d.app_version || '—'); })
      .catch(() => setApiOk(false));
  }, []);

  useEffect(() => {
    fetch(`${API_BASE}/api/v1/user/api-key/status`, { credentials: 'include' })
      .then(r => r.json())
      .then(d => { if (d.has_key) setApiKey('••••••••' + (d.key_suffix || '')); })
      .catch(() => {});
  }, []);

  const handleNotifToggle = async () => {
    if (!isSupported()) return;
    if (notifPerm === 'granted') {
      setNotifPerm('denied-by-user');
      return;
    }
    const perm = await requestPermission();
    setNotifPerm(perm);
  };

  const handleGenerateKey = async () => {
    setApiKeyLoading(true);
    try {
      const r = await fetch(`${API_BASE}/api/v1/user/api-key/generate`, {
        method: 'POST', credentials: 'include',
      });
      const d = await r.json();
      if (d.api_key) setApiKey(d.api_key);
    } catch { /* ignore */ }
    setApiKeyLoading(false);
  };

  const handleRevokeKey = async () => {
    if (!window.confirm('Hủy API key? Các integration đang dùng key này sẽ bị ngắt.')) return;
    await fetch(`${API_BASE}/api/v1/user/api-key/revoke`, { method: 'DELETE', credentials: 'include' });
    setApiKey(null);
  };

  const notifLabel = () => {
    if (!isSupported()) return 'Trình duyệt không hỗ trợ';
    if (notifPerm === 'granted') return 'Đã bật';
    if (notifPerm === 'denied') return 'Đã từ chối (cài đặt trình duyệt)';
    return 'Chưa bật';
  };

  const notifEnabled = notifPerm === 'granted';

  return (
    <div className="space-y-6">
      {/* ── Header ────────────────────────────────────────── */}
      <div>
        <div className="flex items-center gap-3 mb-1">
          <Settings className="w-5 h-5 text-accent-light" />
          <h2 className="text-2xl font-bold text-text-primary tracking-tight">Settings</h2>
        </div>
        <p className="text-sm text-text-muted ml-8">Cài đặt kết nối, thông báo, dữ liệu, và API</p>
      </div>

      <div className="space-y-4">

        {/* ── API Connection ─────────────────────────────── */}
        <div className="p-6 rounded-2xl bg-surface-card border border-border shadow-lg">
          <div className="flex items-center gap-3 mb-4">
            <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-gradient-to-br from-success to-emerald-600">
              <Server className="w-4 h-4 text-white" />
            </div>
            <div className="flex-1">
              <h3 className="text-base font-semibold text-text-primary">API Connection</h3>
              <p className="text-xs text-text-muted">Trạng thái kết nối backend</p>
            </div>
            <div className="flex items-center gap-2">
              {apiOk === null && <div className="w-2 h-2 rounded-full bg-text-muted animate-pulse" />}
              {apiOk === true && <CheckCircle2 className="w-4 h-4 text-success" />}
              {apiOk === false && <XCircle className="w-4 h-4 text-error" />}
              <span className={`text-xs font-medium ${apiOk === true ? 'text-success' : apiOk === false ? 'text-error' : 'text-text-muted'}`}>
                {apiOk === null ? 'Đang kiểm tra…' : apiOk ? 'Kết nối tốt' : 'Không kết nối được'}
              </span>
            </div>
          </div>
          {appVersion && (
            <div className="flex items-center gap-2 px-3 py-2 rounded-xl bg-surface border border-border">
              <Info className="w-3.5 h-3.5 text-text-muted flex-shrink-0" />
              <span className="text-xs text-text-muted">Phiên bản backend: <span className="text-text-primary font-mono font-semibold">{appVersion}</span></span>
            </div>
          )}
        </div>

        {/* ── Notifications ──────────────────────────────── */}
        <div className="p-6 rounded-2xl bg-surface-card border border-border shadow-lg">
          <div className="flex items-center gap-3 mb-4">
            <div className={`flex items-center justify-center w-9 h-9 rounded-lg bg-gradient-to-br ${notifEnabled ? 'from-primary to-accent' : 'from-slate-600 to-slate-700'}`}>
              {notifEnabled ? <Bell className="w-4 h-4 text-white" /> : <BellOff className="w-4 h-4 text-white" />}
            </div>
            <div className="flex-1">
              <h3 className="text-base font-semibold text-text-primary">Thông báo trình duyệt</h3>
              <p className="text-xs text-text-muted">Nhận thông báo khi tải xong hoặc thất bại</p>
            </div>
            <button
              onClick={handleNotifToggle}
              disabled={!isSupported() || notifPerm === 'denied'}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed ${notifEnabled ? 'bg-primary' : 'bg-slate-600'}`}
            >
              <span className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${notifEnabled ? 'translate-x-6' : 'translate-x-1'}`} />
            </button>
          </div>
          <div className="space-y-2 text-xs text-text-muted">
            <p>Trạng thái: <span className={`font-semibold ${notifEnabled ? 'text-success' : 'text-text-secondary'}`}>{notifLabel()}</span></p>
            <p className="flex items-start gap-1.5">
              <AlertCircle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
              Thông báo Telegram cho operator được cấu hình qua biến môi trường server.
            </p>
            {notifPerm === 'denied' && (
              <p className="text-yellow-500">Để bật lại, mở cài đặt trình duyệt → Site Permissions → Notifications → cho phép trang này.</p>
            )}
          </div>
        </div>

        {/* ── Data & Retention ───────────────────────────── */}
        <div className="p-6 rounded-2xl bg-surface-card border border-border shadow-lg">
          <div className="flex items-center gap-3 mb-4">
            <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-gradient-to-br from-amber-500 to-orange-600">
              <Clock className="w-4 h-4 text-white" />
            </div>
            <div>
              <h3 className="text-base font-semibold text-text-primary">Dữ liệu & Lưu trữ</h3>
              <p className="text-xs text-text-muted">Thời hạn lưu giữ từng loại dữ liệu</p>
            </div>
          </div>
          <div className="space-y-2">
            {RETENTION_RULES.map((rule, i) => (
              <div key={i} className="flex items-start gap-3 px-3 py-2.5 rounded-xl bg-surface border border-border/60">
                <Database className="w-3.5 h-3.5 text-text-muted flex-shrink-0 mt-0.5" />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-medium text-text-secondary">{rule.label}</span>
                    <span className="text-xs font-bold text-text-primary flex-shrink-0">{rule.value}</span>
                  </div>
                  <p className="text-[11px] text-text-muted mt-0.5">{rule.note}</p>
                </div>
              </div>
            ))}
          </div>
          <div className="mt-3 flex items-center gap-2">
            <FileDown className="w-3.5 h-3.5 text-primary flex-shrink-0" />
            <span className="text-xs text-text-muted">
              Xuất lịch sử: đăng nhập → History → nút <strong>Xuất CSV/JSON</strong>.
            </span>
          </div>
        </div>

        {/* ── API Access ─────────────────────────────────── */}
        <div className="p-6 rounded-2xl bg-surface-card border border-border shadow-lg">
          <div className="flex items-center gap-3 mb-4">
            <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-gradient-to-br from-violet-500 to-purple-600">
              <Key className="w-4 h-4 text-white" />
            </div>
            <div>
              <h3 className="text-base font-semibold text-text-primary">API Access</h3>
              <p className="text-xs text-text-muted">Tích hợp VidGrab vào công cụ của bạn</p>
            </div>
            <a
              href="/api-docs"
              className="ml-auto inline-flex items-center gap-1 px-2.5 py-1 rounded-lg border border-violet-500/30 text-violet-400 text-xs hover:bg-violet-500/10 transition-colors cursor-pointer"
            >
              <ExternalLink className="w-3 h-3" /> API Docs
            </a>
          </div>
          {apiKey ? (
            <div className="space-y-2">
              <div className="flex items-center gap-2 px-3 py-2 rounded-xl bg-surface border border-border font-mono text-xs text-text-primary">
                <Shield className="w-3.5 h-3.5 text-text-muted flex-shrink-0" />
                <span className="flex-1 truncate">{apiKey}</span>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={handleGenerateKey}
                  disabled={apiKeyLoading}
                  className="px-3 py-1.5 rounded-lg bg-primary/10 text-primary border border-primary/30 text-xs font-semibold hover:bg-primary/20 transition-colors cursor-pointer disabled:opacity-50"
                >
                  Tạo key mới
                </button>
                <button
                  onClick={handleRevokeKey}
                  className="px-3 py-1.5 rounded-lg bg-error/10 text-error border border-error/20 text-xs font-semibold hover:bg-error/20 transition-colors cursor-pointer"
                >
                  Hủy key
                </button>
              </div>
              <p className="text-[11px] text-text-muted">Dùng header <code className="bg-surface px-1 rounded">Authorization: Bearer &lt;key&gt;</code> trong mọi API request.</p>
            </div>
          ) : (
            <div className="space-y-2">
              <p className="text-xs text-text-muted">Chưa có API key. Tạo để dùng Public API.</p>
              <button
                onClick={handleGenerateKey}
                disabled={apiKeyLoading}
                className="px-4 py-2 rounded-xl bg-violet-500/10 border border-violet-500/30 text-violet-400 text-xs font-semibold hover:bg-violet-500/20 transition-colors cursor-pointer disabled:opacity-50"
              >
                {apiKeyLoading ? 'Đang tạo…' : 'Tạo API Key'}
              </button>
              <p className="text-[11px] text-text-muted">Cần đăng nhập để tạo API key.</p>
            </div>
          )}
        </div>

        {/* ── App Info ───────────────────────────────────── */}
        <div className="p-6 rounded-2xl bg-surface-card border border-border shadow-lg">
          <div className="flex items-center gap-3 mb-4">
            <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-gradient-to-br from-primary to-accent">
              <Palette className="w-4 h-4 text-white" />
            </div>
            <div>
              <h3 className="text-base font-semibold text-text-primary">Thông tin ứng dụng</h3>
              <p className="text-xs text-text-muted">Phiên bản, tương thích, liên hệ</p>
            </div>
          </div>
          <div className="space-y-2 text-xs">
            <div className="flex items-center justify-between px-3 py-2 rounded-xl bg-surface border border-border/60">
              <span className="text-text-muted">Frontend</span>
              <span className="font-mono font-semibold text-text-primary">v1.6.0</span>
            </div>
            <div className="flex items-center justify-between px-3 py-2 rounded-xl bg-surface border border-border/60">
              <span className="text-text-muted">Backend</span>
              <span className="font-mono font-semibold text-text-primary">{appVersion || '—'}</span>
            </div>
            <div className="flex items-center justify-between px-3 py-2 rounded-xl bg-surface border border-border/60">
              <span className="text-text-muted">Tương thích</span>
              <span className={`font-semibold ${appVersion ? 'text-success' : 'text-text-muted'}`}>
                {appVersion ? '✓ Frontend ↔ Backend' : 'Chưa xác định'}
              </span>
            </div>
            <div className="flex items-center justify-between px-3 py-2 rounded-xl bg-surface border border-border/60">
              <span className="text-text-muted">Hỗ trợ</span>
              <a
                href="https://t.me/vidgrab_support"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-primary hover:text-primary-light transition-colors"
              >
                Telegram <ExternalLink className="w-3 h-3" />
              </a>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}

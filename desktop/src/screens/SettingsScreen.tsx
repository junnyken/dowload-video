import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { FolderOpen, LogIn, LogOut, RefreshCw, ShieldCheck } from 'lucide-react';
import { Badge, Button, ScreenHeader, Select, Spinner, Toggle } from '../components/ui';
import { SyncStatus } from '../components/SyncStatus';
import { CopyLink } from '../components/CopyLink';
import { settings, updateSettings, ensureOutDir, type Quality, type Theme } from '../lib/settings';
import { QUALITY_LABEL } from '../lib/quality';
import { auth, signOut } from '../lib/auth';
import { api } from '../lib/tauri';
import { apiFetch } from '../lib/http';
import { openSignIn, toast } from '../lib/ui';
import { versionLess } from '../lib/format';
import { errorMessage, toAppError } from '../lib/errors';
import { WEBSITE_URL } from '../lib/config';
import type { ClientVersionInfo, ToolVersions } from '../lib/types';

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-line bg-surface shadow-card">
      <h2 className="border-b border-line px-4 py-2.5 text-[13px] font-semibold text-fg-2">{title}</h2>
      <div className="divide-y divide-line">{children}</div>
    </section>
  );
}

function Row({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 px-4 py-3">
      <div className="min-w-0">
        <p className="text-sm font-medium text-fg">{label}</p>
        {hint && <p className="mt-0.5 text-xs text-fg-muted">{hint}</p>}
      </div>
      <div className="flex min-w-0 shrink-0 items-center justify-end gap-2">{children}</div>
    </div>
  );
}

export function SettingsScreen() {
  const st = settings.use();
  const a = auth.use();
  const [version, setVersion] = useState<string | null>(null);
  const [tools, setTools] = useState<ToolVersions | null>(null);
  const [latest, setLatest] = useState<ClientVersionInfo | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkMsg, setCheckMsg] = useState<string | null>(null);

  useEffect(() => {
    void ensureOutDir();
    api.getVersion().then(setVersion).catch(() => setVersion(null));
    api.toolVersions().then(setTools).catch(() => setTools(null));
  }, []);

  const check = useCallback(async () => {
    setChecking(true);
    setCheckMsg(null);
    try {
      const res = await apiFetch<ClientVersionInfo>('/api/v1/client/version');
      if (res.status !== 200 || !res.data?.latest) throw { code: 'server' };
      setLatest(res.data);
    } catch (e) {
      setLatest(null);
      setCheckMsg(errorMessage(toAppError(e).code));
    } finally {
      setChecking(false);
    }
  }, []);
  useEffect(() => { void check(); }, [check]);

  const [autostart, setAutostart] = useState<boolean | null>(null);
  useEffect(() => { api.autostartGet().then(setAutostart).catch(() => setAutostart(false)); }, []);
  function setTray(v: boolean) {
    updateSettings({ closeToTray: v });
    api.setCloseToTray(v).catch((e) => toast('error', errorMessage(toAppError(e).code)));
  }
  async function setAuto(v: boolean) {
    setAutostart(v);
    try {
      await api.autostartSet(v);
      setAutostart(await api.autostartGet());
    } catch (e) {
      setAutostart(!v);
      toast('error', errorMessage(toAppError(e).code));
    }
  }

  const newer = !!(latest && version && versionLess(version, latest.latest));

  async function pick() {
    try {
      const d = await api.pickFolder();
      if (d) updateSettings({ outDir: d });
    } catch (e) { toast('error', errorMessage(toAppError(e).code)); }
  }

  return (
    <div className="flex h-full flex-col">
      <ScreenHeader title="Cài đặt" />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-6">
        <div className="flex flex-col gap-4">
          <Section title="Tải xuống">
            <Row label="Thư mục lưu mặc định">
              <span className="max-w-[260px] truncate text-[13px] text-fg-2 select-text" title={st.outDir ?? ''}>{st.outDir ?? '…'}</span>
              <Button size="sm" icon={<FolderOpen size={14} />} onClick={pick}>Đổi</Button>
            </Row>
            <Row label="Số lượt tải cùng lúc" hint="Các video còn lại sẽ chờ trong hàng đợi.">
              <input
                type="range" min={1} max={4} step={1} value={st.concurrency} aria-label="Số lượt tải cùng lúc"
                onChange={(e) => updateSettings({ concurrency: Number(e.target.value) })}
                className="w-32 accent-[var(--vg-accent)]"
              />
              <span className="w-4 text-center text-sm font-semibold text-fg">{st.concurrency}</span>
            </Row>
            <Row label="Chất lượng mặc định" hint="Nếu video không có đúng mức này, ứng dụng chọn mức gần nhất thấp hơn.">
              <Select label="Chất lượng mặc định" value={st.defaultQuality} onChange={(v) => updateSettings({ defaultQuality: v as Quality })} className="w-[170px]">
                {(Object.keys(QUALITY_LABEL) as Quality[]).map((q) => <option key={q} value={q}>{QUALITY_LABEL[q]}</option>)}
              </Select>
            </Row>
          </Section>

          <Section title="Chạy nền">
            <Row label="Thu nhỏ xuống khay khi đóng cửa sổ" hint="Bấm X chỉ ẩn cửa sổ, ứng dụng vẫn chạy để theo dõi kênh. Chọn Thoát ở biểu tượng khay để tắt hẳn.">
              <Toggle checked={st.closeToTray} onChange={setTray} label="Thu nhỏ xuống khay khi đóng cửa sổ" />
            </Row>
            <Row label="Khởi động cùng Windows" hint="App mở ẩn dưới khay đồng hồ.">
              {autostart == null ? <Spinner /> : <Toggle checked={autostart} onChange={(v) => void setAuto(v)} label="Khởi động cùng Windows" />}
            </Row>
          </Section>

          <Section title="Giao diện">
            <Row label="Chủ đề màu">
              <Select label="Chủ đề màu" value={st.theme} onChange={(v) => updateSettings({ theme: v as Theme })} className="w-[170px]">
                <option value="system">Theo hệ thống</option>
                <option value="light">Sáng</option>
                <option value="dark">Tối</option>
              </Select>
            </Row>
          </Section>

          <Section title="Tài khoản và đồng bộ">
            <Row label={a.status === 'in' ? (a.email ?? 'Đã đăng nhập') : 'Chưa đăng nhập'} hint={a.status === 'in' ? 'Đang đăng nhập trên máy này.' : 'Đăng nhập để đồng bộ lịch sử với web.'}>
              {a.status === 'in'
                ? <Button size="sm" icon={<LogOut size={14} />} onClick={() => void signOut().then(() => toast('info', 'Đã đăng xuất.'))}>Đăng xuất</Button>
                : <Button size="sm" variant="primary" icon={<LogIn size={14} />} onClick={() => openSignIn()}>Đăng nhập</Button>}
            </Row>
            <Row label="Tự đồng bộ lịch sử" hint="Gửi lịch sử tải của máy này lên tài khoản của bạn.">
              <SyncStatus showButton={false} />
              <Toggle checked={st.autoSync} onChange={(v) => updateSettings({ autoSync: v })} label="Tự đồng bộ lịch sử" />
            </Row>
            <div className="flex items-start gap-3 px-4 py-3 text-[13px] text-fg-2">
              <ShieldCheck size={18} className="mt-0.5 shrink-0 text-success" aria-hidden />
              <p>
                <span className="font-medium text-fg">Quyền riêng tư.</span> Khi đồng bộ, ứng dụng chỉ gửi: liên kết, tiêu đề, nền tảng, dung lượng, trạng thái và mã nhận diện thiết bị.
                Không bao giờ gửi cookie, mật khẩu hay đường dẫn tệp trên máy bạn.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2 px-4 py-3 text-[13px] text-fg-muted">
              Tạo tài khoản hoặc đặt lại mật khẩu tại website: <CopyLink url={WEBSITE_URL} />
            </div>
          </Section>

          <Section title="Thông tin phiên bản">
            <Row label="VidGrab" hint={newer ? undefined : latest ? 'Bạn đang dùng bản mới nhất.' : undefined}>
              <span className="text-sm text-fg-2">{version ?? '—'}</span>
              {newer && <Badge tone="accent">Có bản mới {latest!.latest}</Badge>}
              <Button size="sm" icon={checking ? <Spinner /> : <RefreshCw size={14} />} disabled={checking} onClick={() => void check()}>Kiểm tra bản mới</Button>
            </Row>
            {newer && (
              <div className="flex flex-wrap items-center gap-2 bg-accent-soft px-4 py-3 text-[13px] text-fg-2">
                {latest!.notes && <span>{latest!.notes}</span>}
                {latest!.downloadUrl && <><span>Tải bản mới tại:</span><CopyLink url={latest!.downloadUrl} /></>}
              </div>
            )}
            {checkMsg && <p className="px-4 py-3 text-[13px] text-fg-muted">Không kiểm tra được bản mới: {checkMsg}</p>}
            <Row label="yt-dlp"><span className="text-sm text-fg-2">{tools?.ytdlp ?? '—'}</span></Row>
            <Row label="ffmpeg"><span className="text-sm text-fg-2">{tools?.ffmpeg ?? '—'}</span></Row>
            <Row label="deno"><span className="text-sm text-fg-2">{tools?.deno ?? '—'}</span></Row>
          </Section>
        </div>
      </div>
    </div>
  );
}

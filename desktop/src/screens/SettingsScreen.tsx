import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { Check, Copy, Download, FolderOpen, LogIn, LogOut, RefreshCw, ShieldCheck } from 'lucide-react';
import { Badge, Button, ProgressBar, ScreenHeader, Select, Spinner, Toggle } from '../components/ui';
import { SyncStatus } from '../components/SyncStatus';
import { deviceInfo, type DeviceInfo } from '../lib/device';
import { CopyLink } from '../components/CopyLink';
import { PlatformAccounts } from '../components/PlatformAccounts';
import { settings, updateSettings, ensureOutDir, cleanCleanup, AUDIO_KBPS, FOLDER_STYLES, NAME_STYLES, SUBTITLE_MODES, type AudioKbps, type FolderStyle, type NameStyle, type Quality, type SubtitleMode, type Theme } from '../lib/settings';
import { cleanupArgs, runCleanup } from '../lib/cleanup';
import { QUALITY_LABEL } from '../lib/quality';
import { auth, signOut } from '../lib/auth';
import { api, onUpdateProgress } from '../lib/tauri';
import { apiFetch } from '../lib/http';
import { openSignIn, toast } from '../lib/ui';
import { formatBytes, versionLess } from '../lib/format';
import { errorMessage, toAppError } from '../lib/errors';
import { WEBSITE_URL } from '../lib/config';
import { inAppVersion, progressPercent, updaterMissing, ytdlpLabel, type InAppProgress } from '../lib/update-core';
import type { ClientVersionInfo, ToolVersions } from '../lib/types';
import type { ClipMode } from '../lib/clipboard-core';
import { PRESET_PLATFORMS, type PlatformPreset } from '../lib/presets-core';
import { platformLabel } from '../lib/format';

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
  const [device, setDevice] = useState<DeviceInfo | null>(null);
  // In-app update (task #6205). `inApp` = the version Rust offers to install,
  // null when there is none or this build cannot update itself.
  const [inApp, setInApp] = useState<string | null>(null);
  const [installing, setInstalling] = useState(false);
  const [installProgress, setInstallProgress] = useState<InAppProgress | null>(null);
  const [installError, setInstallError] = useState<string | null>(null);
  const [codeCopied, setCodeCopied] = useState(false);
  async function copyCode() {
    if (!device) return;
    try {
      await navigator.clipboard.writeText(device.code);
      setCodeCopied(true);
      setTimeout(() => setCodeCopied(false), 1800);
    } catch { toast('error', 'Không sao chép được. Hãy bôi đen mã và nhấn Ctrl+C.'); } // wording: BA review
  }

  useEffect(() => {
    void ensureOutDir();
    void deviceInfo().then(setDevice);
    api.getVersion().then(setVersion).catch(() => setVersion(null));
    api.toolVersions().then(setTools).catch(() => setTools(null));
  }, []);

  const checkInApp = useCallback(async () => {
    try {
      const [offer, current] = await Promise.all([api.updateCheck(), api.getVersion()]);
      setInApp(inAppVersion(current, offer));
    } catch (e) {
      // updater_unavailable / not_in_app: behave as before 0.11 (manual link).
      // Any other failure (offline, endpoint down) also leaves only the link.
      const code = toAppError(e).code;
      if (!updaterMissing(code)) console.warn('update_check failed:', code);
      setInApp(null);
    }
  }, []);

  async function installNow() {
    setInstalling(true);
    setInstallError(null);
    setInstallProgress(null);
    const off = await onUpdateProgress(setInstallProgress);
    try {
      // On success the app exits and the installer starts the new version.
      await api.updateInstall();
    } catch (e) {
      setInstallError(errorMessage(toAppError(e).code));
      setInstalling(false);
    } finally {
      off();
    }
  }

  const check = useCallback(async () => {
    setChecking(true);
    setCheckMsg(null);
    void checkInApp();
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
  const offered = inApp ?? (newer ? latest!.latest : null);
  const percent = progressPercent(installProgress);

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
            {/* wording: BA review — quality #8 (0.21.0) */}
            <Row label="Kiểu tệp video" hint={st.codecPref === 'easy'
              ? 'Dễ mở: đến 1080p dùng H.264, mở được bằng trình phát có sẵn của Windows, Mac, iPhone. 2K/4K dùng VP9/AV1 vì nguồn không có H.264 ở mức đó.'
              : 'Chất lượng gốc: codec tốt nhất nguồn có (VP9/AV1/Opus), tệp nhỏ hơn hoặc nét hơn, máy cũ có thể cần VLC để mở.'}>
              <Select label="Kiểu tệp video" value={st.codecPref} onChange={(v) => updateSettings({ codecPref: v === 'original' ? 'original' : 'easy' })} className="w-[170px]">
                <option value="easy">Dễ mở</option>
                <option value="original">Chất lượng gốc</option>
              </Select>
            </Row>
            {/* wording: BA review — quality #5 (0.21.0) */}
            <Row label="Âm thanh khi tải MP3" hint={st.audioKbps === 'orig'
              ? 'Giữ âm gốc: không chuyển sang MP3, giữ nguyên tệp âm thanh của nguồn (thường là Opus hoặc M4A). Không mất chất lượng.'
              : st.audioKbps === 'best'
                ? 'Tốt nhất: MP3 chất lượng cao nhất, dung lượng tự điều chỉnh theo bài.'
                : `MP3 ${st.audioKbps} kbps cố định. Số càng cao càng hay, tệp càng lớn.`}>
              <Select label="Âm thanh khi tải MP3" value={st.audioKbps} onChange={(v) => updateSettings({ audioKbps: AUDIO_KBPS.includes(v as AudioKbps) ? (v as AudioKbps) : 'best' })} className="w-[170px]">
                <option value="best">MP3 tốt nhất</option>
                <option value="320">MP3 320 kbps</option>
                <option value="256">MP3 256 kbps</option>
                <option value="192">MP3 192 kbps</option>
                <option value="128">MP3 128 kbps</option>
                <option value="orig">Giữ âm gốc</option>
              </Select>
            </Row>
            {/* wording: BA review — quality #7 (0.21.0) */}
            <Row label="Phụ đề" hint={st.subtitles === 'off'
              ? 'Không tải phụ đề.'
              : `Phụ đề tiếng Việt và tiếng Anh do người đăng tải lên (không lấy phụ đề tự động). ${st.subtitles === 'embed' ? 'Nhúng vào video, bật/tắt được trong trình phát.' : 'Lưu thành tệp .srt cạnh video.'}`}>
              <Select label="Phụ đề" value={st.subtitles} onChange={(v) => updateSettings({ subtitles: SUBTITLE_MODES.includes(v as SubtitleMode) ? (v as SubtitleMode) : 'off' })} className="w-[170px]">
                <option value="off">Không tải</option>
                <option value="file">Tệp .srt riêng</option>
                <option value="embed">Nhúng vào video</option>
              </Select>
            </Row>
          </Section>

          <PlatformPresetsSection />

          <NamingSection />

          <CleanupSection />

          {/* wording: BA review — clipboard link watch (14/10) */}
          <Section title="Tự nhận link">
            <Row label="Khi sao chép link video" hint="VidGrab nhận ra link video bạn vừa sao chép (Ctrl+C) ở bất kỳ đâu. Chỉ đọc link của các nền tảng VidGrab hỗ trợ; mọi nội dung khác trong bộ nhớ tạm được bỏ qua và không rời khỏi máy.">
              <Select label="Khi sao chép link video" value={st.clipboardMode} onChange={(v) => updateSettings({ clipboardMode: v as ClipMode })} className="w-[170px]">
                <option value="ask">Hỏi trước khi tải</option>
                <option value="auto">Tự tải ngay</option>
                <option value="off">Tắt</option>
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

          <PlatformAccounts />

          <Section title="Máy này">{/* wording: BA review */}
            <Row label={device ? `Mã máy: ${device.code}` : 'Mã máy'} hint={device?.displayName}>
              {device && (
                <Button size="sm" icon={codeCopied ? <Check size={14} /> : <Copy size={14} />} onClick={() => void copyCode()}>
                  {codeCopied ? 'Đã chép' : 'Sao chép mã'}
                </Button>
              )}
            </Row>
            {/* wording: BA review */}
            <p className="px-4 py-3 text-[13px] text-fg-muted">
              Mã gắn với máy này, không gắn với tài khoản. Cài lại ứng dụng không làm đổi mã; cài lại Windows hoặc đổi máy thì mã đổi theo. Đọc mã này cho bộ phận hỗ trợ khi cần.
            </p>
          </Section>

          <Section title="Thông tin phiên bản">
            <Row label="VidGrab" hint={offered ? undefined : latest ? 'Bạn đang dùng bản mới nhất.' : undefined}>
              <span className="text-sm text-fg-2">{version ?? '—'}</span>
              {offered && <Badge tone="accent">Có bản mới {offered}</Badge>}
              {inApp && (
                // wording: BA review
                <Button size="sm" variant="primary" icon={installing ? <Spinner /> : <Download size={14} />} disabled={installing} onClick={() => void installNow()}>
                  Cập nhật ngay
                </Button>
              )}
              <Button size="sm" icon={checking ? <Spinner /> : <RefreshCw size={14} />} disabled={checking || installing} onClick={() => void check()}>Kiểm tra bản mới</Button>
            </Row>
            {installing && (
              // wording: BA review
              <div className="space-y-2 px-4 py-3 text-[13px] text-fg-2">
                <p>
                  {percent === 100
                    ? 'Đã tải xong. Đang cài đặt, VidGrab sẽ tự đóng và mở lại sau ít giây…'
                    : `Đang tải bản cập nhật ${inApp ?? ''}${percent !== null ? ` — ${percent}%` : '…'}`}
                </p>
                <ProgressBar percent={percent} indeterminate={percent === null} />
              </div>
            )}
            {installError && (
              // wording: BA review
              <div className="flex flex-wrap items-center gap-2 bg-danger-soft px-4 py-3 text-[13px] text-fg-2">
                <span>Không cập nhật được: {installError}</span>
                {latest?.downloadUrl && <><span>Tải bản mới thủ công tại:</span><CopyLink url={latest.downloadUrl} /></>}
              </div>
            )}
            {offered && (
              <div className="flex flex-wrap items-center gap-2 bg-accent-soft px-4 py-3 text-[13px] text-fg-2">
                {latest?.notes && <span>{latest.notes}</span>}
                {/* wording: BA review */}
                {inApp && !installing && <span>Bấm “Cập nhật ngay”: VidGrab tự tải, kiểm tra chữ ký rồi cài và mở lại; lượt tải đang chạy sẽ được tạm dừng.</span>}
                {latest?.downloadUrl && <><span>Tải bản mới tại:</span><CopyLink url={latest.downloadUrl} /></>}
              </div>
            )}
            {checkMsg && <p className="px-4 py-3 text-[13px] text-fg-muted">Không kiểm tra được bản mới: {checkMsg}</p>}
            {/* wording: BA review — names and versions remain in diagnostics for support. */}
            <Row label="Bộ xử lý tải"><span className="text-sm text-fg-2">{tools?.ytdlp ? 'Sẵn sàng' : 'Chưa sẵn sàng'}</span></Row>
            <Row label="Xử lý âm thanh và video"><span className="text-sm text-fg-2">{tools?.ffmpeg ? 'Sẵn sàng' : 'Chưa sẵn sàng'}</span></Row>
            <Row label="Hỗ trợ tương thích"><span className="text-sm text-fg-2">{tools?.deno ? 'Sẵn sàng' : 'Chưa sẵn sàng'}</span></Row>
            <details className="px-4 py-3 text-[13px] text-fg-2">
              <summary>Thông tin chẩn đoán dành cho hỗ trợ</summary>
              <p className="mt-2">yt-dlp: {ytdlpLabel(tools)} · ffmpeg: {tools?.ffmpeg ?? '—'} · deno: {tools?.deno ?? '—'}</p>
            </details>
          </Section>
        </div>
      </div>
    </div>
  );
}


/** Per-platform quality/folder for links (14/10, presets-core.ts). wording: BA review */
function PlatformPresetsSection() {
  const st = settings.use();
  const presets = st.platformPresets;
  const set = (k: string, patch: PlatformPreset | null) => {
    const next = { ...presets };
    const merged = patch ? { ...next[k], ...patch } : {};
    if (!merged.quality) delete merged.quality;
    if (!merged.outDir) delete merged.outDir;
    if (merged.quality || merged.outDir) next[k] = merged; else delete next[k];
    updateSettings({ platformPresets: next });
  };
  async function pickFor(k: string) {
    try {
      const d = await api.pickFolder();
      if (d) set(k, { outDir: d });
    } catch (e) { toast('error', errorMessage(toAppError(e).code)); }
  }
  return (
    <Section title="Mặc định theo nền tảng">
      <p className="px-4 pt-3 text-xs text-fg-muted">
        Chọn chất lượng và thư mục riêng cho từng nền tảng khi tải link lẻ. Để “Mặc định chung” thì dùng cài đặt ở mục Tải xuống. Kênh theo dõi có cài đặt riêng trong mục Kênh.
      </p>
      {PRESET_PLATFORMS.map((k) => {
        const p = presets[k] ?? {};
        return (
          <Row key={k} label={platformLabel(k)}>
            <Select label={`Chất lượng ${platformLabel(k)}`} value={p.quality ?? ''} onChange={(v) => set(k, { quality: (v || undefined) as Quality | undefined })} className="w-[170px]">
              <option value="">Mặc định chung</option>
              {(Object.keys(QUALITY_LABEL) as Quality[]).map((q) => <option key={q} value={q}>{QUALITY_LABEL[q]}</option>)}
            </Select>
            <span className="max-w-[160px] truncate text-[13px] text-fg-2 select-text" title={p.outDir ?? ''}>{p.outDir ?? 'Thư mục chung'}</span>
            <Button size="sm" icon={<FolderOpen size={14} />} onClick={() => void pickFor(k)}>Đổi</Button>
            {(p.quality || p.outDir) && <Button size="sm" variant="ghost" onClick={() => set(k, null)}>Bỏ</Button>}
          </Row>
        );
      })}
    </Section>
  );
}

const NAME_EXAMPLE: Record<NameStyle, string> = {
  title: 'Tên video [id].mp4',
  date_title: '2026-10-15 Tên video [id].mp4',
  date_channel_title: '2026-10-15_Tên kênh_Tên video [id].mp4',
};
const FOLDER_EXAMPLE: Record<FolderStyle, string> = {
  none: 'VidGrab\\Tên video.mp4',
  platform: 'VidGrab\\TikTok\\…',
  month: 'VidGrab\\2026-10\\…',
  platform_month: 'VidGrab\\TikTok\\2026-10\\…',
};
const NAME_LABEL: Record<NameStyle, string> = { title: 'Chỉ tiêu đề', date_title: 'Ngày + tiêu đề', date_channel_title: 'Ngày + kênh + tiêu đề' }; // wording: BA review
const FOLDER_LABEL: Record<FolderStyle, string> = { none: 'Không chia thư mục con', platform: 'Theo nền tảng', month: 'Theo tháng', platform_month: 'Theo nền tảng, rồi theo tháng' }; // wording: BA review

/** File name / sub-folder layout of main-folder downloads (0.17.0). wording: BA review */
function NamingSection() {
  const st = settings.use();
  return (
    <Section title="Tên tệp & thư mục">
      <Row label="Cách đặt tên tệp" hint={`Ví dụ: ${NAME_EXAMPLE[st.nameStyle]}`}>
        <Select label="Cách đặt tên tệp" value={st.nameStyle} onChange={(v) => updateSettings({ nameStyle: v as NameStyle })} className="w-[220px]">
          {NAME_STYLES.map((k) => <option key={k} value={k}>{NAME_LABEL[k]}</option>)}
        </Select>
      </Row>
      <Row label="Chia thư mục con" hint={`Ví dụ: ${FOLDER_EXAMPLE[st.folderStyle]}`}>
        <Select label="Chia thư mục con" value={st.folderStyle} onChange={(v) => updateSettings({ folderStyle: v as FolderStyle })} className="w-[220px]">
          {FOLDER_STYLES.map((k) => <option key={k} value={k}>{FOLDER_LABEL[k]}</option>)}
        </Select>
      </Row>
      <p className="px-4 py-3 text-xs text-fg-muted">Áp dụng cho video tải vào thư mục chính; video của kênh theo dõi vẫn vào thư mục riêng của kênh.</p>
    </Section>
  );
}

/** Auto cleanup of old downloads → Recycle Bin (0.17.0). wording: BA review */
function CleanupSection() {
  const st = settings.use();
  const c = st.cleanup;
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const args = cleanupArgs();
  const setC = (patch: Partial<typeof c>) => { updateSettings({ cleanup: cleanCleanup({ ...c, ...patch }) }); setMsg(null); setConfirming(false); };
  const numOrNull = (v: string) => (v.trim() === '' ? null : Number(v));
  // Draft text so a half-typed number is not rewritten by cleanCleanup while typing.
  const [days, setDays] = useState(c.olderThanDays == null ? '' : String(c.olderThanDays));
  const [gb, setGb] = useState(c.minFreeGb == null ? '' : String(c.minFreeGb));

  async function preview() {
    if (!args) return;
    setBusy(true); setMsg(null); setConfirming(false);
    try {
      const r = await api.cleanupPreview(args);
      setMsg(`Sẽ chuyển ${r.count} video (${formatBytes(r.bytes)}) vào Thùng rác`);
    } catch (e) { toast('error', errorMessage(toAppError(e).code)); } finally { setBusy(false); }
  }
  async function run() {
    setBusy(true); setMsg(null); setConfirming(false);
    try {
      const r = await runCleanup();
      if (r) setMsg(`Đã chuyển ${r.moved} video (${formatBytes(r.bytes)}) vào Thùng rác${r.failed ? ` · ${r.failed} video không chuyển được` : ''}`);
    } catch (e) { toast('error', errorMessage(toAppError(e).code)); } finally { setBusy(false); }
  }

  const numInput = 'h-9 w-24 rounded-[10px] border border-line-strong bg-surface px-2.5 text-right text-sm text-fg placeholder:text-fg-muted';
  return (
    <Section title="Tự dọn ổ đĩa">
      <Row label="Tự chuyển video cũ vào Thùng rác" hint="Kiểm tra mỗi 6 giờ khi VidGrab đang chạy.">
        <Toggle checked={c.enabled} onChange={(v) => setC({ enabled: v })} label="Tự chuyển video cũ vào Thùng rác" />
      </Row>
      <Row label="Video cũ hơn … ngày" hint="Để trống = không dọn theo tuổi video.">
        <input
          type="number" min={1} max={3650} step={1} inputMode="numeric" aria-label="Video cũ hơn (ngày)" placeholder="Tắt" className={numInput} value={days}
          onChange={(e) => { setDays(e.target.value); setC({ olderThanDays: numOrNull(e.target.value) }); }}
        />
        <span className="text-[13px] text-fg-2">ngày</span>
      </Row>
      <Row label="Khi ổ còn dưới … GB" hint="Để trống = không dọn theo dung lượng trống. Dọn video cũ nhất trước.">
        <input
          type="number" min={0.5} step="any" inputMode="decimal" aria-label="Khi ổ còn dưới (GB)" placeholder="Tắt" className={numInput} value={gb}
          onChange={(e) => { setGb(e.target.value); setC({ minFreeGb: numOrNull(e.target.value) }); }}
        />
        <span className="text-[13px] text-fg-2">GB</span>
      </Row>
      <div className="px-4 py-3">
        <p className="text-xs text-fg-muted">Chỉ đụng tới video do VidGrab tải. Video vào Thùng rác của Windows, lấy lại được.</p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button size="sm" icon={busy ? <Spinner /> : undefined} disabled={!args || busy} onClick={() => void preview()}>Xem trước</Button>
          {!confirming ? (
            <Button size="sm" variant="danger" disabled={!args || busy} onClick={() => setConfirming(true)}>Dọn ngay</Button>
          ) : (
            <span className="flex flex-wrap items-center gap-2 rounded-[10px] bg-danger-soft px-2.5 py-1" role="alertdialog" aria-label="Xác nhận dọn ngay">
              <span className="text-[13px] text-fg">Chuyển các video đủ điều kiện vào Thùng rác ngay bây giờ?</span>
              <Button size="sm" variant="danger" disabled={busy} onClick={() => void run()}>Đồng ý, dọn ngay</Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>Huỷ</Button>
            </span>
          )}
        </div>
        {!args && <p className="mt-2 text-xs text-fg-muted">Nhập số ngày hoặc số GB để dùng tính năng này.</p>}
        {msg && <p role="status" className="mt-2 text-[13px] text-fg-2">{msg}</p>}
      </div>
    </Section>
  );
}

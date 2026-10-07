// Settings card "Tài khoản nền tảng" (PLAN-32D §3, P1): one row per platform the
// server allows (features.cookiePlatforms). All wording here: BA review.
import { useEffect } from 'react';
import { Check, Link2, Trash2 } from 'lucide-react';
import { Badge, Button, Spinner } from './ui';
import { clearPlatform, connect, cookieState, finish, loadFeatures, offeredPlatforms, refreshCookieStatus } from '../lib/cookies';
import { rowState } from '../lib/cookies-core';
import { settings, updateSettings } from '../lib/settings';

function savedAtText(sec: number | null | undefined): string {
  if (!sec) return '';
  const d = new Date(sec * 1000);
  return d.toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit', year: 'numeric' });
}

export function PlatformAccounts() {
  const st = cookieState.use();
  const debugWin = settings.use().douyinDebugWindow;
  useEffect(() => { void loadFeatures(); void refreshCookieStatus(); }, []);
  const rows = offeredPlatforms(st.enabled);

  return (
    <section className="rounded-xl border border-line bg-surface shadow-card">
      <h2 className="border-b border-line px-4 py-2.5 text-[13px] font-semibold text-fg-2">Tài khoản nền tảng</h2>
      <div className="divide-y divide-line">
        {rows.length === 0 && (
          <p className="px-4 py-3 text-[13px] text-fg-muted">
            {st.enabled == null ? 'Đang kiểm tra…' : 'Hiện chưa có nền tảng nào cần kết nối tài khoản.'}
          </p>
        )}
        {rows.map((p) => {
          const s = st.status[p.slug];
          const state = rowState(s);
          const busy = st.busy === p.slug;
          const pending = st.pending === p.slug;
          const label = state === 'none' ? 'Chưa kết nối' : state === 'suspect' ? 'Có thể đã hết hạn' : `Đã kết nối lúc ${savedAtText(s?.savedAt)}`;
          return (
            <div key={p.slug} className="flex items-center justify-between gap-4 px-4 py-3">
              <div className="min-w-0">
                <p className="flex items-center gap-2 text-sm font-medium text-fg">
                  {p.label}
                  {state === 'saved' && <Badge tone="success">{label}</Badge>}
                  {state === 'suspect' && <Badge tone="warning">{label}</Badge>}
                  {state === 'none' && <Badge tone="neutral">{label}</Badge>}
                </p>
                {pending && <p className="mt-0.5 text-xs text-accent-text">{p.hint ?? `Đăng nhập trong cửa sổ ${p.label} vừa mở, rồi bấm Xong.`}</p>}
                {!pending && p.hint && state === 'none' && <p className="mt-0.5 text-xs text-fg-muted">{p.hint}</p>}
                {p.slug === 'douyin' && state !== 'none' && (
                  // wording: BA review
                  <label className="mt-1 flex items-center gap-2 text-xs text-fg-muted">
                    <input type="checkbox" checked={debugWin} onChange={(e) => updateSettings({ douyinDebugWindow: e.target.checked })} />
                    Gỡ lỗi: hiện cửa sổ Douyin khi tải (để xem Douyin hiển thị gì)
                  </label>
                )}
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {busy && <Spinner />}
                {pending ? (
                  <Button size="sm" variant="primary" icon={<Check size={14} />} disabled={busy} onClick={() => void finish(p.slug)}>Xong</Button>
                ) : (
                  <Button size="sm" variant={state === 'none' ? 'primary' : 'secondary'} icon={<Link2 size={14} />} disabled={busy} onClick={() => void connect(p.slug)}>
                    {state === 'none' ? 'Kết nối' : 'Kết nối lại'}
                  </Button>
                )}
                {(state !== 'none' || pending) && (
                  <Button size="sm" variant="danger" icon={<Trash2 size={14} />} disabled={busy} onClick={() => void clearPlatform(p.slug)}>Xoá</Button>
                )}
              </div>
            </div>
          );
        })}
        {rows.length > 0 && (
          <p className="px-4 py-3 text-[13px] text-fg-muted">
            Cookie chỉ nằm trên máy này, được mã hoá bằng Windows và chỉ dùng khi tải video của đúng nền tảng đó. VidGrab không gửi cookie lên máy chủ.
          </p>
        )}
      </div>
    </section>
  );
}

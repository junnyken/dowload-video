import { useEffect, useState } from 'react';
import { Download, Loader2, RefreshCw, Monitor } from 'lucide-react';
import { apiUrl } from '../lib/apiBase';

// Public page (no login) for the VidGrab Windows app — task #6125.
// The installer is ~100 MB, so the button is a plain <a href>, never a fetch.

function safeHttpsUrl(value) {
  if (typeof value !== 'string' || !value.trim()) return null;
  try {
    const u = new URL(value.trim());
    return u.protocol === 'https:' ? u.href : null;
  } catch {
    return null;
  }
}

export default function DownloadPage() {
  const [state, setState] = useState('loading'); // loading | ready | error
  const [info, setInfo] = useState(null);

  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    fetch(apiUrl('/api/v1/client/version'))
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => {
        if (cancelled) return;
        setInfo(data && typeof data === 'object' ? data : {});
        setState('ready');
      })
      .catch(() => { if (!cancelled) setState('error'); });
    return () => { cancelled = true; };
  }, [attempt]);

  const retry = () => { setState('loading'); setAttempt((n) => n + 1); };

  const url = safeHttpsUrl(info?.downloadUrl);
  const notes = typeof info?.notes === 'string' ? info.notes.trim() : '';

  return (
    <div className="max-w-2xl mx-auto px-4 md:px-8 py-8 md:py-12 text-fg">
      <div className="flex items-center gap-3 mb-3">
        <div className="w-10 h-10 shrink-0 rounded-xl bg-accent flex items-center justify-center">
          <Monitor size={20} className="text-accent-fg" aria-hidden="true" />
        </div>
        {/* wording: BA review */}
        <h1 className="text-2xl md:text-3xl font-extrabold">Tải VidGrab cho Windows</h1>
      </div>
      {/* wording: BA review */}
      <p className="text-fg-2 mb-6">
        Ứng dụng VidGrab cho máy tính: tải video từ TikTok, YouTube, Facebook, Instagram ngay trên Windows.
      </p>

      <section className="rounded-xl border border-line bg-surface p-4 md:p-6 mb-6" aria-live="polite">
        {state === 'loading' && (
          <p className="flex items-center gap-2 text-fg-muted">
            <Loader2 size={18} className="animate-spin" aria-hidden="true" />
            {/* wording: BA review */}
            Đang kiểm tra phiên bản mới nhất...
          </p>
        )}

        {state === 'error' && (
          <div role="alert">
            {/* wording: BA review */}
            <p className="text-danger mb-3">Không lấy được thông tin phiên bản. Vui lòng thử lại.</p>
            <button
              type="button"
              onClick={retry}
              className="inline-flex items-center gap-2 rounded-lg border border-line px-4 py-2 text-sm font-semibold hover:bg-surface-2 transition-colors"
            >
              <RefreshCw size={16} aria-hidden="true" />
              {/* wording: BA review */}
              Thử lại
            </button>
          </div>
        )}

        {state === 'ready' && (
          <div>
            {info?.latest && (
              // wording: BA review
              <p className="font-semibold mb-1">Phiên bản mới nhất: {String(info.latest)}</p>
            )}
            {notes && <p className="text-sm text-fg-muted whitespace-pre-line mb-3">{notes}</p>}
            {url ? (
              <a
                href={url}
                rel="noopener noreferrer"
                className="mt-2 inline-flex w-full sm:w-auto items-center justify-center gap-2 px-5 py-3 rounded-xl bg-accent text-accent-fg font-bold text-sm hover:bg-accent-hover transition-colors"
              >
                <Download size={18} aria-hidden="true" />
                {/* wording: BA review */}
                Tải bộ cài (.exe)
              </a>
            ) : (
              // wording: BA review
              <p className="mt-2 text-fg-muted">Bộ cài đang được cập nhật, vui lòng quay lại sau.</p>
            )}
          </div>
        )}
      </section>

      <section className="mb-6">
        {/* wording: BA review */}
        <h2 className="text-lg font-bold mb-2">Yêu cầu hệ thống</h2>
        {/* wording: BA review */}
        <p className="text-fg-2">Windows 10/11 64-bit.</p>
      </section>

      <section className="mb-6">
        {/* wording: BA review */}
        <h2 className="text-lg font-bold mb-2">Lưu ý khi cài đặt</h2>
        <ul className="list-disc list-inside space-y-2 text-fg-2">
          {/* wording: BA review */}
          <li>
            Windows SmartScreen có thể hiện cảnh báo &ldquo;Windows protected your PC&rdquo; vì ứng dụng chưa được
            ký số. Hãy bấm &ldquo;More info&rdquo; &rarr; &ldquo;Run anyway&rdquo; để tiếp tục cài đặt.
          </li>
          {/* wording: BA review */}
          <li>Cài bản mới đè lên bản cũ vẫn giữ nguyên video đã tải và các kênh đã lưu.</li>
        </ul>
      </section>

      {/* wording: BA review */}
      <p className="text-sm text-fg-muted">
        Đăng nhập trong ứng dụng dùng chung tài khoản và hạn mức hằng ngày với website.
      </p>
    </div>
  );
}

import { useEffect, useState } from 'react';
import { API_BASE } from '../lib/apiBase';

// wording: BA review
const LINKS = [
  ['Bảng giá', 'pricing', '/pricing'],
  ['Điều khoản', 'terms', '/terms'],
  ['Chính sách riêng tư', 'privacy', '/privacy'],
  ['Gỡ nội dung', 'takedown', '/takedown'],
  ['Câu hỏi thường gặp', 'faq', '/faq'],
];

// Task #6331: external status page set in Admin → Công tắc khẩn cấp. It lives
// outside our server, so it still opens when the server is down. Unset → hidden.
function useStatusPageUrl() {
  const [url, setUrl] = useState(null);
  useEffect(() => {
    let alive = true;
    fetch(`${API_BASE}/api/v1/site-links`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        const u = d && d.status_page_url;
        if (alive && typeof u === 'string' && u.startsWith('https://')) setUrl(u);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, []);
  return url;
}

export default function SiteFooter({ onNavigate }) {
  const statusUrl = useStatusPageUrl();
  return (
    <footer className="mt-12 border-t border-line px-4 py-6 text-xs text-fg-muted">
      <nav aria-label="Liên kết pháp lý và trợ giúp"
        className="mx-auto flex max-w-5xl flex-wrap items-center justify-center gap-x-2 gap-y-2">
        {LINKS.map(([label, view, path], i) => (
          <span key={view} className="flex items-center gap-2">
            {i > 0 && <span aria-hidden="true">·</span>}
            <a href={path}
              onClick={(e) => { e.preventDefault(); onNavigate(view, path); window.scrollTo(0, 0); }}
              className="py-2 hover:text-fg underline-offset-2 hover:underline">
              {label}
            </a>
          </span>
        ))}
        {statusUrl && (
          <span className="flex items-center gap-2">
            <span aria-hidden="true">·</span>
            <a href={statusUrl} target="_blank" rel="noopener noreferrer"
              className="py-2 hover:text-fg underline-offset-2 hover:underline">
              Trạng thái hệ thống
            </a>
          </span>
        )}
      </nav>
    </footer>
  );
}

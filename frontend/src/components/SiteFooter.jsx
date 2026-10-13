// wording: BA review
const LINKS = [
  ['Điều khoản', 'terms', '/terms'],
  ['Chính sách riêng tư', 'privacy', '/privacy'],
  ['Gỡ nội dung', 'takedown', '/takedown'],
  ['Câu hỏi thường gặp', 'faq', '/faq'],
];

export default function SiteFooter({ onNavigate }) {
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
      </nav>
    </footer>
  );
}

import { useState } from 'react';
import { X, MessageSquare, Send, CheckCircle } from 'lucide-react';

// VITE_API_BASE/VITE_API_BASE_URL are not defined in any build —
// this fell back to '' and became a relative (502) URL.
import { API_BASE } from '../lib/apiBase';

export default function FeedbackModal({ onClose }) {
  const [name, setName]       = useState('');
  const [email, setEmail]     = useState('');
  const [content, setContent] = useState('');
  const [sending, setSending] = useState(false);
  const [done, setDone]       = useState(false);
  const [error, setError]     = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!content.trim()) { setError('Vui lòng nhập nội dung góp ý.'); return; }
    setSending(true);
    setError('');
    try {
      const res = await fetch(`${API_BASE}/api/v1/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name.trim(),
          email: email.trim(),
          content: content.trim(),
          page: window.location.pathname,
        }),
      });
      if (res.ok) {
        setDone(true);
        setTimeout(onClose, 2500);
      } else {
        setError('Gửi thất bại, vui lòng thử lại.');
      }
    } catch {
      setError('Lỗi kết nối, vui lòng thử lại.');
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[80] flex items-end sm:items-center justify-center px-4 pb-4 sm:pb-0">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="relative w-full max-w-md bg-[#0d3b35] border border-slate-700/60 rounded-2xl shadow-2xl p-6 z-10">
        {/* Header */}
        <div className="flex items-center justify-between mb-5">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-[#FBBF24]/15 flex items-center justify-center">
              <MessageSquare className="w-4 h-4 text-[#FBBF24]" />
            </div>
            <h2 className="text-base font-bold text-white">Góp ý & Phản hồi</h2>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-700/50 transition-colors cursor-pointer"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {done ? (
          <div className="flex flex-col items-center gap-3 py-6 text-center">
            <CheckCircle className="w-12 h-12 text-emerald-400" />
            <p className="text-white font-semibold">Gửi thành công!</p>
            <p className="text-slate-400 text-sm">Cảm ơn bạn đã góp ý. Chúng tôi sẽ xem xét và cải thiện.</p>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <p className="text-slate-400 text-sm">
              Ý kiến của bạn giúp VidGrab trở nên tốt hơn. Chúng tôi đọc từng phản hồi.
            </p>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1.5">Tên (tuỳ chọn)</label>
                <input
                  type="text"
                  value={name}
                  onChange={e => setName(e.target.value)}
                  placeholder="Nguyễn Văn A"
                  className="w-full bg-slate-800/60 border border-slate-700/50 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-[#FBBF24]/50 transition-colors"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1.5">Email (tuỳ chọn)</label>
                <input
                  type="email"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  placeholder="example@gmail.com"
                  className="w-full bg-slate-800/60 border border-slate-700/50 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-[#FBBF24]/50 transition-colors"
                />
              </div>
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5">
                Nội dung góp ý <span className="text-red-400">*</span>
              </label>
              <textarea
                value={content}
                onChange={e => { setContent(e.target.value); setError(''); }}
                placeholder="Mô tả vấn đề bạn gặp, tính năng muốn có, hoặc bất kỳ phản hồi nào..."
                rows={4}
                className="w-full bg-slate-800/60 border border-slate-700/50 rounded-lg px-3 py-2.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-[#FBBF24]/50 transition-colors resize-none"
              />
            </div>

            {error && (
              <p className="text-red-400 text-xs flex items-center gap-1.5">
                <span className="w-1 h-1 rounded-full bg-red-400 inline-block" />
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={sending || !content.trim()}
              className="w-full flex items-center justify-center gap-2 bg-gradient-to-r from-[#FBBF24] to-[#FB923C] text-[#012622] font-bold py-2.5 rounded-xl text-sm hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
            >
              {sending ? (
                <>
                  <span className="w-4 h-4 border-2 border-[#012622]/40 border-t-[#012622] rounded-full animate-spin" />
                  Đang gửi...
                </>
              ) : (
                <>
                  <Send className="w-4 h-4" />
                  Gửi góp ý
                </>
              )}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

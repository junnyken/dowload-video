import { useState } from 'react';
import { X, Crown, Check, Zap } from 'lucide-react';
import { API_BASE } from '../lib/apiBase';

const ERROR_COPY = {
  quota_exceeded_daily: {
    title: 'Đã đạt giới hạn hôm nay',
    subtitle: 'Free: 10 lượt/ngày. Nâng cấp Pro để tải 100 lượt mỗi ngày.',
  },
  tier_limit_quality: {
    title: '4K chỉ dành cho Pro',
    subtitle: 'Gói Free giới hạn 1080p. Nâng cấp để tải 4K.',
  },
  tier_limit_batch: {
    title: 'Batch limit: 5 video',
    subtitle: 'Pro cho phép tải tới 100 video cùng lúc.',
  },
  tier_required_feature: {
    title: 'Tính năng Pro',
    subtitle: 'Tính năng này chỉ dành cho thành viên Pro.',
  },
  youtube_pro_only: {
    title: 'YouTube video yêu cầu Pro',
    subtitle: 'Free chỉ tải được MP3 từ YouTube. Nâng cấp để tải video 720p / 1080p.',
  },
  spotify_artist_full: {
    title: 'Spotify Artist full catalog — Pro only',
    subtitle: 'Free chỉ tải được top tracks (10 bài). Nâng cấp để tải album / all_tracks.',
  },
  bulk_zip_pro: {
    title: 'ZIP download yêu cầu Pro',
    subtitle: 'Đóng gói toàn bộ batch thành một file ZIP chỉ dành cho thành viên Pro.',
  },
};

const FEATURES = [
  { label: 'Lượt tải/ngày',  free: '10',            pro: '100'             },
  { label: 'Batch tối đa',   free: '5',             pro: '100'             },
  { label: 'YouTube',        free: 'MP3 only',      pro: 'MP3 + Video'     },
  { label: 'Spotify Artist', free: 'Top 10 tracks', pro: 'Full catalog'    },
  { label: 'ZIP Download',   free: false,            pro: true              },
  { label: 'Chất lượng',    free: 'tối đa 1080p',  pro: '4K'              },
  { label: 'Lịch sử',       free: '7 ngày',         pro: '90 ngày'         },
  { label: 'Cloud Save',    free: false,             pro: true              },
  { label: 'Chapters',      free: false,             pro: true              },
  { label: 'Xoá Logo',      free: false,             pro: true              },
  { label: 'API Key',       free: false,             pro: true              },
];

function CellValue({ value }) {
  if (value === true)  return <Check className="w-4 h-4 text-violet-400 mx-auto" aria-label="Có" />;
  if (value === false) return <X     className="w-4 h-4 text-zinc-600   mx-auto" aria-label="Không" />;
  return <span>{value}</span>;
}

export default function UpgradeModal({ isOpen, onClose, errorCode = null, authToken = null }) {
  const [loading, setLoading]   = useState(false);
  const [ctaError, setCtaError] = useState('');

  if (!isOpen) return null;

  const copy = ERROR_COPY[errorCode] ?? {
    title:    'Nâng cấp Pro',
    subtitle: 'Mở khoá toàn bộ tính năng.',
  };

  const handleBackdrop = (e) => {
    if (e.target === e.currentTarget) onClose();
  };

  const handleUpgrade = async () => {
    setCtaError('');
    setLoading(true);
    try {
      const apiBase = API_BASE;
      const res = await fetch(`${apiBase}/api/v1/payments/create-checkout-session`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
        },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data?.checkout_url) {
        window.location.href = data.checkout_url;
      } else {
        throw new Error('Không nhận được checkout_url từ server.');
      }
    } catch (err) {
      setCtaError(err.message || 'Đã có lỗi xảy ra. Vui lòng thử lại.');
    } finally {
      setLoading(false);
    }
  };

  return (
    /* Backdrop */
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
      onClick={handleBackdrop}
      aria-modal="true"
      role="dialog"
      aria-labelledby="upgrade-modal-title"
    >
      {/* Panel */}
      <div className="relative w-full max-w-lg bg-zinc-900 border border-zinc-700 rounded-2xl shadow-2xl overflow-hidden">

        {/* Top accent bar */}
        <div className="h-1 w-full bg-gradient-to-r from-violet-500 to-indigo-500" />

        {/* Close */}
        <button
          onClick={onClose}
          aria-label="Đóng"
          className="absolute top-4 right-4 p-1.5 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors"
        >
          <X className="w-4 h-4" />
        </button>

        <div className="px-6 pt-6 pb-7">
          {/* Header */}
          <div className="flex items-center gap-3 mb-1">
            <div className="flex-shrink-0 w-9 h-9 rounded-xl bg-gradient-to-br from-violet-500 to-indigo-500 flex items-center justify-center shadow-lg">
              <Crown className="w-4 h-4 text-white" />
            </div>
            <h2
              id="upgrade-modal-title"
              className="text-lg font-bold text-white leading-snug"
            >
              {copy.title}
            </h2>
          </div>
          <p className="text-sm text-zinc-400 mb-5 pl-12">{copy.subtitle}</p>

          {/* Comparison table */}
          <div className="rounded-xl border border-zinc-700 overflow-hidden mb-5">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-zinc-800">
                  <th className="text-left px-4 py-2.5 text-zinc-400 font-medium w-1/2">Tính năng</th>
                  <th className="text-center px-3 py-2.5 text-zinc-400 font-medium w-1/4">Free</th>
                  <th className="text-center px-3 py-2.5 text-violet-300 font-semibold w-1/4">Pro</th>
                </tr>
              </thead>
              <tbody>
                {FEATURES.map((row, i) => (
                  <tr
                    key={row.label}
                    className={i % 2 === 0 ? 'bg-zinc-900' : 'bg-zinc-800/40'}
                  >
                    <td className="px-4 py-2 text-zinc-300">{row.label}</td>
                    <td className="px-3 py-2 text-center text-zinc-500">
                      <CellValue value={row.free} />
                    </td>
                    <td className="px-3 py-2 text-center text-white font-medium">
                      <CellValue value={row.pro} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* CTA error */}
          {ctaError && (
            <p className="text-xs text-red-400 mb-3 text-center">{ctaError}</p>
          )}

          {/* Action buttons */}
          {authToken ? (
            <button
              onClick={handleUpgrade}
              disabled={loading}
              className="w-full flex items-center justify-center gap-2 py-3 px-6 rounded-xl bg-gradient-to-r from-violet-500 to-indigo-500 text-white font-semibold text-sm shadow-lg hover:opacity-90 active:scale-[0.98] transition-all disabled:opacity-60 disabled:cursor-not-allowed mb-2"
            >
              {loading ? (
                <>
                  <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  Đang xử lý…
                </>
              ) : (
                <>
                  <Zap className="w-4 h-4" />
                  Nâng cấp Pro →
                </>
              )}
            </button>
          ) : (
            <p className="text-center text-sm text-zinc-400 mb-2">
              <span className="text-white font-medium">Đăng nhập để nâng cấp</span>
            </p>
          )}

          <button
            onClick={onClose}
            className="w-full py-2.5 px-6 rounded-xl text-zinc-400 hover:text-white hover:bg-zinc-800 text-sm transition-colors"
          >
            Để sau
          </button>
        </div>
      </div>
    </div>
  );
}

import { X, Smartphone, Download, Share2 } from 'lucide-react';

export default function PWAInstallBanner({ isIOS, onInstall, onDismiss }) {
  return (
    <div className="fixed bottom-4 left-4 right-4 z-40 sm:left-auto sm:right-4 sm:w-80">
      <div className="bg-surface-2 border border-line rounded-2xl p-4 shadow-2xl shadow-black/40 relative">
        <button
          onClick={onDismiss}
          className="absolute top-3 right-3 text-fg-muted hover:text-fg-2 transition-colors"
          aria-label="Đóng"
        >
          <X className="w-4 h-4" />
        </button>

        <div className="flex items-start gap-3 pr-6">
          <div className="w-10 h-10 rounded-xl bg-surface-2 flex items-center justify-center flex-shrink-0">
            <Smartphone className="w-5 h-5 text-fg-2" />
          </div>
          <div>
            <p className="text-sm font-semibold text-fg">Thêm vào màn hình chính</p>
            <p className="text-xs text-fg-muted mt-0.5 leading-relaxed">
              Mở VidGrab nhanh hơn, không cần trình duyệt
            </p>
          </div>
        </div>

        {isIOS ? (
          <div className="mt-3 space-y-1.5">
            <div className="flex items-center gap-2 text-xs text-fg-2">
              <span className="w-5 h-5 rounded-full bg-surface-2 flex items-center justify-center text-[10px] font-bold text-fg flex-shrink-0">
                1
              </span>
              <span>
                Nhấn nút <Share2 className="w-3 h-3 inline" /> ở thanh dưới Safari
              </span>
            </div>
            <div className="flex items-center gap-2 text-xs text-fg-2">
              <span className="w-5 h-5 rounded-full bg-surface-2 flex items-center justify-center text-[10px] font-bold text-fg flex-shrink-0">
                2
              </span>
              <span>Kéo xuống → chọn "Thêm vào màn hình chính"</span>
            </div>
            <div className="flex items-center gap-2 text-xs text-fg-2">
              <span className="w-5 h-5 rounded-full bg-surface-2 flex items-center justify-center text-[10px] font-bold text-fg flex-shrink-0">
                3
              </span>
              <span>Nhấn "Thêm" → Xong!</span>
            </div>
            <button
              onClick={onDismiss}
              className="mt-2 w-full text-xs text-fg-muted text-center py-1 hover:text-fg-muted transition-colors"
            >
              Bỏ qua
            </button>
          </div>
        ) : (
          <button
            onClick={onInstall}
            className="mt-3 w-full flex items-center justify-center gap-2 bg-accent hover:bg-accent-hover text-accent-fg text-sm font-semibold py-2.5 rounded-xl transition-colors"
          >
            <Download className="w-4 h-4" />
            Cài đặt ngay — miễn phí
          </button>
        )}
      </div>
    </div>
  );
}

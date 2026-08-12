import { Component } from 'react';
import { AlertTriangle, RefreshCw, Home } from 'lucide-react';

/**
 * React error boundary — catches unhandled render/lifecycle errors in the
 * component subtree and shows a recovery UI instead of crashing the whole app.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error('[ErrorBoundary] Unhandled error:', error, errorInfo);
    this.setState({ errorInfo });
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
  };

  handleReload = () => {
    window.location.reload();
  };

  render() {
    if (!this.state.hasError) return this.props.children;

    const { error } = this.state;
    const message = error?.message || 'Lỗi không xác định';
    const isNetworkError = message.toLowerCase().includes('fetch') ||
                           message.toLowerCase().includes('network');

    return (
      <div className="min-h-[400px] flex items-center justify-center px-4 py-12">
        <div className="max-w-md w-full space-y-6 text-center">
          <div className="flex justify-center">
            <div className="w-16 h-16 rounded-2xl bg-error/10 border border-error/20 flex items-center justify-center">
              <AlertTriangle className="w-8 h-8 text-error" />
            </div>
          </div>

          <div>
            <h2 className="text-xl font-bold text-text-primary mb-2">
              Đã xảy ra lỗi không mong đợi
            </h2>
            <p className="text-sm text-text-muted">
              {isNetworkError
                ? 'Không thể kết nối đến server. Kiểm tra kết nối mạng và thử lại.'
                : 'Thành phần gặp lỗi và không thể hiển thị. Thử làm mới trang.'}
            </p>
          </div>

          {message && (
            <div className="px-4 py-2.5 rounded-xl bg-surface border border-border text-left">
              <p className="text-xs font-mono text-text-muted break-all">{message}</p>
            </div>
          )}

          <div className="flex items-center justify-center gap-3">
            <button
              onClick={this.handleReset}
              className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-primary/10 border border-primary/30 text-primary text-sm font-semibold hover:bg-primary/20 transition-colors cursor-pointer"
            >
              <RefreshCw className="w-4 h-4" /> Thử lại
            </button>
            <button
              onClick={this.handleReload}
              className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-surface border border-border text-text-secondary text-sm font-semibold hover:text-text-primary hover:border-primary/30 transition-colors cursor-pointer"
            >
              <Home className="w-4 h-4" /> Tải lại trang
            </button>
          </div>

          <p className="text-xs text-text-muted">
            Nếu lỗi tiếp tục, liên hệ hỗ trợ tại{' '}
            <a
              href="https://t.me/vidgrab_support"
              className="text-primary hover:text-primary-light transition-colors"
              target="_blank"
              rel="noopener noreferrer"
            >
              Telegram
            </a>.
          </p>
        </div>
      </div>
    );
  }
}

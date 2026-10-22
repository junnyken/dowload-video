import { Component } from 'react';
import { AlertTriangle, RefreshCw, Home } from 'lucide-react';
import { Trans } from 'react-i18next';
import i18n from '../i18n';

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
    const t = (key) => i18n.t(key, { ns: 'common' });
    const message = error?.message || t('errorBoundary.unknown');
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
            <h2 className="text-xl font-bold text-fg mb-2">
              {t('errorBoundary.title')}
            </h2>
            <p className="text-sm text-fg-muted">
              {isNetworkError
                ? t('errorBoundary.network')
                : t('errorBoundary.render')}
            </p>
          </div>

          {message && (
            <div className="px-4 py-2.5 rounded-xl bg-surface border border-line text-left">
              <p className="text-xs font-mono text-fg-muted break-all">{message}</p>
            </div>
          )}

          <div className="flex items-center justify-center gap-3">
            <button
              onClick={this.handleReset}
              className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-accent/10 border border-accent/30 text-accent-text text-sm font-semibold hover:bg-accent/20 transition-colors cursor-pointer"
            >
              <RefreshCw className="w-4 h-4" /> {t('errorBoundary.retry')}
            </button>
            <button
              onClick={this.handleReload}
              className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-surface border border-line text-fg-2 text-sm font-semibold hover:text-fg hover:border-accent/30 transition-colors cursor-pointer"
            >
              <Home className="w-4 h-4" /> {t('errorBoundary.reload')}
            </button>
          </div>

          <p className="text-xs text-fg-muted">
            <Trans
              i18n={i18n}
              t={i18n.getFixedT(null, 'common')}
              i18nKey="errorBoundary.support"
              components={{
                tg: (
                  <a
                    href="https://t.me/vidgrab_support"
                    className="text-accent-text hover:text-primary-light transition-colors"
                    target="_blank"
                    rel="noopener noreferrer"
                  />
                ),
              }}
            />
          </p>
        </div>
      </div>
    );
  }
}

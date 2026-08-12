import { useState } from 'react';
import { X, Mail, Lock, User, Eye, EyeOff, Loader2, CheckCircle } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';

const VIEWS = { SIGNIN: 'signin', SIGNUP: 'signup', RESET: 'reset' };

export default function AuthModal({ onClose }) {
  const { signIn, signUp, resetPassword } = useAuth();
  const [view, setView]       = useState(VIEWS.SIGNIN);
  const [email, setEmail]     = useState('');
  const [password, setPassword] = useState('');
  const [name, setName]       = useState('');
  const [showPw, setShowPw]   = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState('');
  const [success, setSuccess] = useState('');

  const reset = (nextView) => {
    setError(''); setSuccess(''); setPassword('');
    setView(nextView);
  };

  const handleSignIn = async (e) => {
    e.preventDefault();
    setLoading(true); setError('');
    const { error } = await signIn(email, password);
    setLoading(false);
    if (error) { setError(error.message); return; }
    onClose();
  };

  const handleSignUp = async (e) => {
    e.preventDefault();
    if (password.length < 6) { setError('Mật khẩu ít nhất 6 ký tự'); return; }
    setLoading(true); setError('');
    const { error } = await signUp(email, password, name);
    setLoading(false);
    if (error) { setError(error.message); return; }
    setSuccess('Kiểm tra email để xác nhận tài khoản!');
  };

  const handleReset = async (e) => {
    e.preventDefault();
    setLoading(true); setError('');
    const { error } = await resetPassword(email);
    setLoading(false);
    if (error) { setError(error.message); return; }
    setSuccess('Đã gửi link đặt lại mật khẩu. Kiểm tra email nhé!');
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />

      {/* Modal */}
      <div className="relative w-full max-w-sm bg-[#021f1c] border border-slate-700/60 rounded-2xl shadow-2xl p-6">
        {/* Close */}
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-slate-400 hover:text-white transition-colors"
        >
          <X className="w-5 h-5" />
        </button>

        {/* Logo mark */}
        <div className="flex justify-center mb-5">
          <div className="w-11 h-11 rounded-xl bg-gradient-to-br from-[#FBBF24] to-[#FB923C] flex items-center justify-center shadow-md shadow-[#FBBF24]/20">
            <span className="text-[#012622] font-extrabold text-lg">V</span>
          </div>
        </div>

        {/* Title */}
        <h2 className="text-center text-white font-bold text-lg mb-1">
          {view === VIEWS.SIGNIN && 'Đăng nhập'}
          {view === VIEWS.SIGNUP && 'Tạo tài khoản'}
          {view === VIEWS.RESET  && 'Quên mật khẩu'}
        </h2>
        <p className="text-center text-slate-400 text-xs mb-5">
          {view === VIEWS.SIGNIN && 'Lịch sử & preferences được lưu theo tài khoản'}
          {view === VIEWS.SIGNUP && 'Miễn phí — không cần thẻ tín dụng'}
          {view === VIEWS.RESET  && 'Nhập email để nhận link đặt lại mật khẩu'}
        </p>

        {/* Success */}
        {success && (
          <div className="mb-4 flex items-center gap-2 bg-green-500/10 border border-green-500/30 rounded-lg px-3 py-2.5 text-green-400 text-sm">
            <CheckCircle className="w-4 h-4 shrink-0" />
            {success}
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="mb-4 bg-red-500/10 border border-red-500/30 rounded-lg px-3 py-2.5 text-red-400 text-sm">
            {error}
          </div>
        )}

        {/* Forms */}
        {view === VIEWS.SIGNIN && (
          <form onSubmit={handleSignIn} className="space-y-3">
            <InputField icon={<Mail />} type="email" placeholder="Email" value={email} onChange={setEmail} />
            <PasswordField value={password} onChange={setPassword} show={showPw} toggle={() => setShowPw(p => !p)} />
            <SubmitBtn loading={loading} label="Đăng nhập" />
            <button type="button" onClick={() => reset(VIEWS.RESET)}
              className="w-full text-center text-slate-400 hover:text-[#FBBF24] text-xs transition-colors">
              Quên mật khẩu?
            </button>
          </form>
        )}

        {view === VIEWS.SIGNUP && (
          <form onSubmit={handleSignUp} className="space-y-3">
            <InputField icon={<User />} type="text" placeholder="Tên hiển thị" value={name} onChange={setName} />
            <InputField icon={<Mail />} type="email" placeholder="Email" value={email} onChange={setEmail} />
            <PasswordField value={password} onChange={setPassword} show={showPw} toggle={() => setShowPw(p => !p)} />
            <SubmitBtn loading={loading} label="Tạo tài khoản" />
          </form>
        )}

        {view === VIEWS.RESET && !success && (
          <form onSubmit={handleReset} className="space-y-3">
            <InputField icon={<Mail />} type="email" placeholder="Email" value={email} onChange={setEmail} />
            <SubmitBtn loading={loading} label="Gửi link đặt lại" />
          </form>
        )}

        {/* Toggle view */}
        {!success && (
          <p className="mt-4 text-center text-slate-400 text-xs">
            {view === VIEWS.SIGNIN ? (
              <>Chưa có tài khoản?{' '}
                <button onClick={() => reset(VIEWS.SIGNUP)}
                  className="text-[#FBBF24] hover:underline font-semibold">Đăng ký</button>
              </>
            ) : (
              <>Đã có tài khoản?{' '}
                <button onClick={() => reset(VIEWS.SIGNIN)}
                  className="text-[#FBBF24] hover:underline font-semibold">Đăng nhập</button>
              </>
            )}
          </p>
        )}

        {/* Guest benefit hint */}
        {view === VIEWS.SIGNIN && (
          <div className="mt-4 pt-4 border-t border-slate-700/40 grid grid-cols-3 gap-2 text-center">
            {['Lịch sử cá nhân', 'Lưu preferences', 'Quota riêng'].map(t => (
              <div key={t} className="text-[10px] text-slate-500">{t}</div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function InputField({ icon, type, placeholder, value, onChange }) {
  return (
    <div className="relative">
      <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 w-4 h-4">
        {icon}
      </span>
      <input
        type={type}
        placeholder={placeholder}
        value={value}
        onChange={e => onChange(e.target.value)}
        required
        className="w-full bg-slate-800/60 border border-slate-600/50 rounded-lg pl-9 pr-4 py-2.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-[#FBBF24]/60 focus:ring-1 focus:ring-[#FBBF24]/30 transition"
      />
    </div>
  );
}

function PasswordField({ value, onChange, show, toggle }) {
  return (
    <div className="relative">
      <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 w-4 h-4">
        <Lock className="w-4 h-4" />
      </span>
      <input
        type={show ? 'text' : 'password'}
        placeholder="Mật khẩu"
        value={value}
        onChange={e => onChange(e.target.value)}
        required
        className="w-full bg-slate-800/60 border border-slate-600/50 rounded-lg pl-9 pr-10 py-2.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-[#FBBF24]/60 focus:ring-1 focus:ring-[#FBBF24]/30 transition"
      />
      <button type="button" onClick={toggle}
        className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-white transition-colors">
        {show ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
      </button>
    </div>
  );
}

function SubmitBtn({ loading, label }) {
  return (
    <button
      type="submit"
      disabled={loading}
      className="w-full bg-gradient-to-r from-[#FBBF24] to-[#FB923C] text-[#012622] font-bold py-2.5 rounded-lg hover:opacity-90 active:scale-[0.98] transition disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
    >
      {loading && <Loader2 className="w-4 h-4 animate-spin" />}
      {label}
    </button>
  );
}

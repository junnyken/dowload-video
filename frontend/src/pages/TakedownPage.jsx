import { useState } from 'react';
import { API_BASE } from '../lib/apiBase';
import { SUPPORT_EMAIL } from '../lib/contact';

// wording: BA review
const EMPTY = {
  name: '', email: '', capacity: '', content_url: '', related: '',
  description: '', declaration: false, website: '',
};
const MAX = 2000;
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s.]{2,}$/;
const HTTPS_RE = /^https:\/\/[^\s/$.?#]\S*$/i;

const INPUT =
  'w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-sm text-fg placeholder:text-fg-muted ' +
  'focus:outline-none focus:ring-2 focus:ring-accent';

function validate(f) {
  const e = {};
  if (!f.name.trim()) e.name = 'Vui lòng nhập họ tên';
  if (!EMAIL_RE.test(f.email.trim())) e.email = 'Email không hợp lệ';
  if (!f.capacity) e.capacity = 'Vui lòng chọn tư cách của bạn';
  if (!HTTPS_RE.test(f.content_url.trim())) e.content_url = 'Link phải bắt đầu bằng https://';
  if (f.related.trim().length > MAX) e.related = `Tối đa ${MAX} ký tự`;
  if (!f.description.trim()) e.description = 'Vui lòng mô tả yêu cầu';
  else if (f.description.length > MAX) e.description = `Tối đa ${MAX} ký tự`;
  if (!f.declaration) e.declaration = 'Bạn cần xác nhận thông tin là đúng';
  return e;
}

function Field({ id, label, error, hint, children }) {
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium text-fg mb-1">{label}</label>
      {children}
      {hint && !error && <p className="mt-1 text-xs text-fg-muted">{hint}</p>}
      {error && <p id={`${id}-err`} role="alert" className="mt-1 text-xs text-danger">{error}</p>}
    </div>
  );
}

export default function TakedownPage() {
  const [form, setForm] = useState(EMPTY);
  const [errors, setErrors] = useState({});
  const [sending, setSending] = useState(false);
  const [done, setDone] = useState(false);
  const [serverError, setServerError] = useState('');

  const set = (k) => (e) => {
    const v = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    setForm((f) => ({ ...f, [k]: v }));
    if (errors[k]) setErrors((x) => ({ ...x, [k]: undefined }));
  };

  async function submit(e) {
    e.preventDefault();
    if (sending) return;
    setServerError('');
    const errs = validate(form);
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setSending(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/takedown`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...form,
          name: form.name.trim(), email: form.email.trim(),
          content_url: form.content_url.trim(), related: form.related.trim(),
          description: form.description.trim(),
        }),
      });
      if (res.ok) { setDone(true); return; }
      if (res.status === 429) {
        setServerError('Bạn đã gửi quá nhiều yêu cầu. Vui lòng thử lại sau 1 giờ.');
      } else if (res.status === 422) {
        setServerError('Thông tin chưa hợp lệ. Vui lòng kiểm tra lại các ô đã nhập.');
      } else {
        setServerError('Chưa gửi được yêu cầu. Vui lòng thử lại sau ít phút.');
      }
    } catch {
      setServerError('Không kết nối được máy chủ. Vui lòng kiểm tra mạng và thử lại.');
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="text-fg">
      <div className="mb-8 border-b border-line pb-6">
        <h1 className="text-3xl font-extrabold text-fg mb-2">Yêu cầu gỡ nội dung</h1>
        <p className="text-fg-muted text-sm">Dành cho chủ sở hữu nội dung hoặc người được ủy quyền.</p>
      </div>

      <section className="mb-8 space-y-3 text-fg-2 leading-relaxed">
        <p>
          VidGrab không lưu video của người dùng lâu dài: tệp chỉ tồn tại tạm thời để bạn tải về rồi
          tự hết hạn. Nội dung gốc vẫn nằm trên nền tảng đăng tải, nên nếu muốn gỡ hẳn, bạn cũng cần
          báo cho nền tảng đó.
        </p>
        <p>Mọi yêu cầu đều được xem xét thủ công. Để xử lý nhanh, vui lòng cung cấp:</p>
        <ul className="list-disc list-inside space-y-1 pl-2">
          <li>Link nội dung trên nền tảng gốc và cho biết bạn là chủ sở hữu hay người đại diện.</li>
          <li>Link hoặc mô tả liên quan đến VidGrab (nếu có), ví dụ link tải đã chia sẻ.</li>
          <li>Mô tả rõ yêu cầu của bạn và email để chúng tôi phản hồi.</li>
        </ul>
        <p className="text-sm text-fg-muted">
          Cần hỗ trợ khác? Gửi email về{' '}
          <a href={`mailto:${SUPPORT_EMAIL}`} className="text-accent-text underline break-all">{SUPPORT_EMAIL}</a>.
        </p>
      </section>

      {done ? (
        <div role="status" className="rounded-xl border border-success bg-success-soft p-5">
          <p className="font-semibold text-fg mb-1">Đã nhận yêu cầu của bạn</p>
          <p className="text-sm text-fg-2">
            Chúng tôi sẽ xem xét và phản hồi qua email bạn đã cung cấp. Cảm ơn bạn.
          </p>
        </div>
      ) : (
        <form onSubmit={submit} noValidate className="space-y-5">
          {/* Honeypot: hidden from people and assistive tech; bots fill it. */}
          <div aria-hidden="true" className="absolute -left-[9999px] h-0 w-0 overflow-hidden">
            <label>Website
              <input type="text" tabIndex={-1} autoComplete="off" value={form.website} onChange={set('website')} />
            </label>
          </div>

          <Field id="td-name" label="Họ tên" error={errors.name}>
            <input id="td-name" className={INPUT} value={form.name} onChange={set('name')}
              maxLength={MAX} autoComplete="name" aria-invalid={!!errors.name} />
          </Field>
          <Field id="td-email" label="Email" error={errors.email}>
            <input id="td-email" type="email" className={INPUT} value={form.email} onChange={set('email')}
              maxLength={MAX} autoComplete="email" aria-invalid={!!errors.email} />
          </Field>
          <Field id="td-capacity" label="Tư cách của bạn" error={errors.capacity}>
            <select id="td-capacity" className={INPUT} value={form.capacity} onChange={set('capacity')}
              aria-invalid={!!errors.capacity}>
              <option value="">Chọn…</option>
              <option value="owner">Chủ sở hữu nội dung</option>
              <option value="agent">Đại diện / được ủy quyền</option>
            </select>
          </Field>
          <Field id="td-url" label="Link nội dung trên nền tảng gốc" error={errors.content_url}
            hint="Bắt đầu bằng https://">
            <input id="td-url" type="url" className={INPUT} value={form.content_url} onChange={set('content_url')}
              maxLength={MAX} placeholder="https://" aria-invalid={!!errors.content_url} />
          </Field>
          <Field id="td-related" label="Link / mô tả liên quan đến VidGrab (không bắt buộc)" error={errors.related}>
            <input id="td-related" className={INPUT} value={form.related} onChange={set('related')}
              maxLength={MAX} aria-invalid={!!errors.related} />
          </Field>
          <Field id="td-desc" label="Mô tả yêu cầu" error={errors.description}
            hint={`${form.description.length}/${MAX} ký tự`}>
            <textarea id="td-desc" rows={5} className={INPUT} value={form.description} onChange={set('description')}
              maxLength={MAX} aria-invalid={!!errors.description} />
          </Field>

          <div>
            <label className="flex items-start gap-3 text-sm text-fg-2">
              <input type="checkbox" className="mt-1 h-4 w-4 shrink-0 accent-[var(--vg-accent)]"
                checked={form.declaration} onChange={set('declaration')} aria-invalid={!!errors.declaration} />
              <span>Tôi cam kết các thông tin trên là đúng và tôi là chủ sở hữu hoặc người được ủy quyền hợp pháp.</span>
            </label>
            {errors.declaration && <p role="alert" className="mt-1 text-xs text-danger">{errors.declaration}</p>}
          </div>

          {serverError && (
            <p role="alert" className="rounded-lg border border-danger bg-danger-soft px-3 py-2 text-sm text-danger">
              {serverError}
            </p>
          )}

          <button type="submit" disabled={sending}
            className="w-full sm:w-auto rounded-lg bg-accent px-6 py-3 text-sm font-semibold text-accent-fg hover:bg-accent-hover disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer">
            {sending ? 'Đang gửi…' : 'Gửi yêu cầu'}
          </button>
        </form>
      )}
    </div>
  );
}

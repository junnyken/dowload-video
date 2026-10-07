# Runbook — Anti-spam for sign-up (AI Factory #6039)

Three parts, shipped together on branch `feat/antispam-signup`:

1. **Cloudflare Turnstile captcha** on the web forms Đăng ký / Đăng nhập /
   Quên mật khẩu, checked by Supabase ("captcha protection").
2. **Disposable / temporary email domains blocked** in the database
   (migration `036_block_disposable_email_signup.sql`, trigger on `auth.users`),
   plus an early message in the sign-up form.
3. **Windows app 0.4.0 signs in through the browser** ("Đăng nhập qua trình
   duyệt"), because once captcha is on, Supabase refuses a password sign-in
   that has no captcha token — the 0.3.0 app's email + password form would
   stop working.

## Where each key goes

| Key | Public? | Where it goes | Never |
|---|---|---|---|
| Turnstile **Site key** | yes (it is in the page) | Vibe Host **frontend** project env `VITE_TURNSTILE_SITE_KEY` (build-time, same mechanism as `VITE_API_URL`) | — |
| Turnstile **Secret key** | **no** | **only** Supabase dashboard → Authentication → Attack Protection → Captcha secret | git, `.env` files in the repo, Vibe Host env, chat, tickets, screenshots |

The backend does not need either key: Supabase Auth itself calls Cloudflare's
siteverify with the secret.

## Rollout — do it in THIS order

Each step is safe on its own; the order keeps every client working at every
moment.

1. **Create the Turnstile widget** (Cloudflare dashboard → Turnstile → Add
   widget). Hostname: `dvid.vibe1.tinhgon.xyz` (add any other domain the
   frontend is served from, e.g. a future custom domain). Widget mode:
   *Managed* (recommended). Copy the **Site key**; keep the **Secret key**
   for step 6 only.
2. **Set `VITE_TURNSTILE_SITE_KEY`** = the Site key on the Vibe Host
   **frontend** project (`dvid`, gateway `vays`).
3. **Deploy the frontend** (push branch to `github/main`, redeploy the frontend
   project). Check: open https://dvid.vibe1.tinhgon.xyz → Đăng nhập → the
   "Cloudflare" box appears and turns to "Thành công!". Sign in once with a
   real account — it must work (Supabase still ignores the token at this
   point). Until step 6 the widget is cosmetic; with the env empty the site
   shows no widget and sends no token, exactly as before.
   After every Vibe Host redeploy check `get_project` → `url` did not change
   (see the 02-10 domain move); if it did, update the Turnstile hostname list.
4. **Run migration 036** in the Supabase SQL editor (as `postgres`). It is
   idempotent. Verify:
   ```sql
   SELECT count(*) FROM public.blocked_email_domains;     -- 9205
   SELECT tgname FROM pg_trigger
    WHERE tgrelid = 'auth.users'::regclass AND NOT tgisinternal;  -- includes on_auth_user_block_disposable
   ```
   Then try to sign up on the website with `test@mailinator.com`: the form
   says *"Không nhận đăng ký bằng email tạm thời…"* before sending anything.
   (The server-side block answers HTTP 500 "Database error saving new user";
   the form shows *"Không tạo được tài khoản với email này…"*.)
5. **Publish the Windows app 0.4.0** (installer built by the main session),
   then set backend env `DESKTOP_LATEST_VERSION=0.4.0` (and
   `DESKTOP_DOWNLOAD_URL` / `DESKTOP_RELEASE_NOTES`) so 0.3.0 users see the
   update. Check on a Windows PC: Cài đặt → Đăng nhập → "Đăng nhập qua trình
   duyệt" → the browser opens `/desktop-login`, sign in, the tab shows "Đã đăng
   nhập, quay lại ứng dụng", the app shows the account. Consider
   `DESKTOP_MIN_VERSION=0.4.0` only after step 6 (it forces the update).
6. **Only now enable captcha in Supabase**: Dashboard → Authentication →
   Attack Protection → *Enable Captcha protection* → provider **Turnstile by
   Cloudflare** → paste the **Secret key** → Save.
   Check right after: sign up, sign in and "Quên mật khẩu" on the website all
   work; sign-in from app 0.4.0 works.

What still works after step 6 without a captcha: token refresh (existing
sessions on the website, extension and app 0.3.0 stay signed in — Supabase
skips the captcha for `grant_type=refresh_token`), email confirmation links,
the password-reset page (`updateUser`), the backend's service-role calls.
What stops: email + password sign-in from app **0.3.0** (users must update to
0.4.0), and any script that signs up / signs in with the anon key.

## Rollback

| Problem | Undo |
|---|---|
| Users cannot sign in / sign up because of the captcha | Supabase → Attack Protection → turn **off** captcha. Immediate; nothing else to change. |
| Widget broken on the site | Set `VITE_TURNSTILE_SITE_KEY` empty and redeploy the frontend — **but turn captcha off in Supabase first**, or every sign-in fails. |
| A legitimate domain is blocked | `DELETE FROM public.blocked_email_domains WHERE domain = 'example.com';` (also remove it from `frontend/public/disposable-domains.json` in the next deploy, or the form keeps warning). |
| One person must use a blocked address | `INSERT INTO public.signup_email_allowlist (email, note) VALUES (lower('a@b.example'), 'why');` — the trigger lets that exact address in (the form's early warning still shows; register them via the dashboard/admin API, which also passes through the allowlist). |
| Remove the email block entirely | `DROP TRIGGER IF EXISTS on_auth_user_block_disposable ON auth.users;` (full cleanup SQL is in the migration header). |
| App 0.4.0 browser sign-in fails | Turn captcha off in Supabase so 0.3.0 works again, then fix. |

## The service role / admin API

Supabase's admin API (`auth.admin.createUser`, invites) goes through the same
Auth server and database role as a normal sign-up, so the database cannot tell
them apart: the disposable-domain block applies there too. The allowlist table
above is the override. The admin API does skip the captcha (Supabase checks for
the service role before verifying the captcha).

## Updating the disposable-domain list

`python3 scripts/gen-disposable-domains.py --commit <new sha> --sha256 <hash>`
regenerates the SQL seed and `frontend/public/disposable-domains.json` from
https://github.com/disposable-email-domains/disposable-email-domains (CC0).
Ship the new rows as a **new** migration (037…) — 036 must stay as it was run.
Seeded: commit `1aac72a07cd89792016dd5d2056bc418d85b8f14` (main, 2026-10-05),
file `disposable_email_blocklist.conf`, 9205 domains, all included.

## UI wording (for BA review)

Web — auth forms (`frontend/src/lib/authErrors.js`, `AuthModal.jsx`):
- Vui lòng hoàn tất bước xác minh chống spam bên dưới rồi thử lại.
- Xác minh chống spam không thành công hoặc đã hết hạn. Vui lòng xác minh lại rồi thử lần nữa.
- Không tải được bước xác minh chống spam. Hãy kiểm tra kết nối, tắt chặn quảng cáo cho trang này rồi tải lại trang.
- Không nhận đăng ký bằng email tạm thời (email dùng một lần). Vui lòng dùng email thật như Gmail, Outlook hoặc email công ty.
- Không tạo được tài khoản với email này. Nếu đây là email tạm thời (dùng một lần), hãy đăng ký bằng email thật như Gmail, Outlook hoặc email công ty.
- Email hoặc mật khẩu không đúng.
- Email chưa được xác nhận. Hãy mở thư xác nhận trong hộp thư trước khi đăng nhập.
- Bạn thao tác quá nhanh. Vui lòng chờ một lát rồi thử lại.
- Lỗi kết nối mạng. Hãy kiểm tra Internet rồi thử lại.
- Đã xảy ra lỗi. Vui lòng thử lại.
- (Turnstile's own text — "Thành công!", "Đang xác minh…" — comes from Cloudflare, language `vi`.)

Web — `/desktop-login` (`DesktopLoginPage.jsx`):
- Đăng nhập ứng dụng VidGrab
- Đăng nhập để kết nối ứng dụng VidGrab trên máy tính với tài khoản của bạn.
- Chỉ dùng trang này khi bạn vừa bấm “Đăng nhập qua trình duyệt” trong ứng dụng VidGrab. Sau khi đăng nhập, trình duyệt sẽ chuyển thông tin đăng nhập thẳng về ứng dụng trên chính máy này.
- Email / Mật khẩu / ban@example.com
- Đăng nhập và quay lại ứng dụng / Đang đăng nhập…
- Chưa có tài khoản? Đăng ký / Quên mật khẩu?
- Liên kết đăng nhập không hợp lệ hoặc đã bị sửa. Hãy mở ứng dụng VidGrab trên máy tính và bấm “Đăng nhập qua trình duyệt” lần nữa.
- Đang chuyển về ứng dụng VidGrab…
- Nếu trình duyệt báo không kết nối được, hãy mở lại ứng dụng VidGrab và bấm “Đăng nhập qua trình duyệt” lần nữa.

App — sign-in dialog (`desktop/src/components/SignInModal.tsx`, `lib/errors.ts`):
- Đăng nhập VidGrab
- Đăng nhập để xem lịch sử tải trên web và đồng bộ lịch sử từ máy này.
- Ứng dụng sẽ mở trang đăng nhập VidGrab trong trình duyệt. Đăng nhập xong, trình duyệt tự chuyển phiên đăng nhập về ứng dụng — ứng dụng không nhận mật khẩu của bạn.
- Đăng nhập qua trình duyệt
- Đang chờ bạn đăng nhập trong trình duyệt…
- Mở lại trình duyệt / Huỷ
- Hết thời gian chờ đăng nhập trong trình duyệt (5 phút). Hãy thử lại.
- Phiên đăng nhập từ trình duyệt không dùng được hoặc đã hết hạn. Hãy bấm “Đăng nhập qua trình duyệt” lần nữa.

App — page served by the app on `127.0.0.1` (`browser_login.rs`):
- Đã đăng nhập, quay lại ứng dụng — Bạn đã đăng nhập VidGrab. Hãy quay lại ứng dụng VidGrab; có thể đóng thẻ này.
- Liên kết đăng nhập không khớp — Liên kết này không thuộc lần đăng nhập đang chờ trong ứng dụng. Hãy bấm “Đăng nhập qua trình duyệt” trong ứng dụng VidGrab lần nữa.
- Không tìm thấy / Yêu cầu không hợp lệ — Hãy quay lại ứng dụng VidGrab.

## References

- Supabase captcha guide: https://supabase.com/docs/guides/auth/auth-captcha
- Which endpoints check the captcha: supabase/auth `internal/api/api.go`
  (`verifyCaptcha` on `/signup`, `/recover`, `/resend`, `/magiclink`, `/otp`,
  `/token`, SSO, passkey) and `internal/api/middleware.go`
  (`isIgnoreCaptchaRoute`: `/token` with `grant_type` `refresh_token`, `pkce`,
  `id_token` is exempt; `password` is not), master @ ce9a8ee.
- Turnstile client rendering / no caching of api.js:
  https://developers.cloudflare.com/turnstile/get-started/client-side-rendering/
- Turnstile CSP: https://developers.cloudflare.com/turnstile/reference/content-security-policy/
  (the website sets no CSP today; if one is added: `script-src` and `frame-src`
  must allow `https://challenges.cloudflare.com`).
- Turnstile test keys: https://developers.cloudflare.com/turnstile/troubleshooting/testing/
- Refresh-token reuse: https://supabase.com/docs/guides/auth/sessions

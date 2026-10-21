# Chuyển cơ sở dữ liệu VidGrab từ Supabase sang Postgres của Vibe Host

Bắt đầu: 21/10/2026. Lý do: Supabase báo sắp hết ngân sách đọc/ghi đĩa (Disk IO
Budget), cả API treo khi Supabase chậm. Chủ dự án chọn phương án (b): tự quản
dữ liệu và đăng nhập trên Postgres của Vibe Host.

## Hiện trạng (đo 21/10)

- Backend gọi Supabase qua supabase-py ở 576 chỗ trên 57 bảng; chỉ dùng khoảng
  20 kiểu lệnh: select/insert/update/upsert/delete, eq/neq/gt/gte/lt/lte/in_/
  is_/ilike/or_/contains/not_, order/limit/range, single/maybe_single,
  count="exact" (36 chỗ), rpc (2 hàm).
- Không dùng Realtime. Storage chỉ dùng cho bản sao lưu hằng đêm.
- Đăng nhập: chỉ email + mật khẩu (không OAuth). Web gom ở
  `frontend/src/context/AuthContext.jsx` (signUp, signInWithPassword, signOut,
  resetPasswordForEmail, updateUser, getSession, onAuthStateChange).
- **App Windows đã cài gọi thẳng Supabase Auth** (`desktop/src/lib/config.ts`
  ghi cứng URL + anon key, dùng supabase-js). App cũ không đổi được từ máy chủ.
- Sơ đồ bảng trong git (`database/schema.sql` + 40 migration) dùng `auth.users`
  (23 chỗ), `auth.uid()` (30), `auth.role()` (11) cho RLS, cùng 5 trigger
  (tạo profile khi đăng ký, chặn email dùng một lần, bảo vệ cột thanh toán...).
- Vibe Host đã cấp sẵn một Postgres cho backend (`DATABASE_URL`, `PG*`).

## Giai đoạn

### 0. Cầm máu trên Supabase (XONG 21/10, commit 2fa1cbb)
- Dọn tệp 5 phút/lần: bỏ một UPDATE quét cả bảng cho mỗi tệp → gộp `in_()`.
- Migration 040: index `local_file_path`, `local_mp3_path`, `(user_id, created_at)`.
  **Cần chạy tay trong Supabase SQL Editor.**
- `/health` nhớ số đếm 60 s, chạy ở luồng riêng có hạn chờ.
- Kiểm tra phiên đăng nhập chạy ở luồng riêng, hạn chờ 8 s, nhớ 5 phút.
- Bỏ lịch `flush_event_buffer` trùng; kiểm tra lệch lịch chỉ đọc lịch đã trễ.

### 1. Dữ liệu sang Postgres Vibe Host (đăng nhập vẫn ở Supabase)
1. Lấy sơ đồ thật: `pg_dump --schema-only` từ Supabase (cần chuỗi kết nối DB).
   Bỏ RLS/`auth.*`; khoá ngoại tới `auth.users` thay bằng bảng `app_users`
   (id, email, email_confirmed_at, created_at) đồng bộ từ Supabase Auth.
2. Lớp tương thích `app/core/pg_client.py`: cùng giao diện
   `table().select()...execute()` / `rpc()` của supabase-py, chạy SQL tham số
   hoá qua psycopg (pool). `get_supabase_client()` / `get_service_client()`
   trả lớp này khi `DB_BACKEND=postgres` → 576 chỗ gọi giữ nguyên.
   Bộ test so kết quả hai phía cho từng kiểu lệnh.
3. Kiểm tra phiên: xác thực JWT của Supabase tại chỗ bằng khoá công khai
   (JWKS) thay vì gọi `auth.get_user` mỗi request.
4. Chép dữ liệu (admin → Cơ sở dữ liệu → "Chép dữ liệu sang Vibe Host",
   `app/core/db_migration.copy_all`): lớp tương thích `auth`
   (`app/core/supabase_compat.sql`) → chép `auth.users` (kèm băm bcrypt) →
   `pg_dump -Fc --schema=public` → `pg_restore -f -` bỏ dòng
   `SET transaction_timeout` (Supabase 17 → đích 16) → `psql
   --single-transaction` → đếm chính xác từng bảng hai bên. Không tạo được
   vai anon/authenticated/service_role thì bỏ policy RLS (backend là chủ bảng,
   RLS không áp). Đã thử 17→16, kể cả đích là người dùng thường: 68 bảng khớp.
   Chép KHÔNG chuyển website; được chạy nhiều lần ("chép đè").
5. Chuyển (cần bảo trì ngắn): chép đè lần cuối → đặt `DB_BACKEND=postgres` →
   deploy. Trước đó phải làm: người đăng ký mới vẫn tạo tài khoản ở Supabase →
   khi gặp JWT hợp lệ mà `auth.users` ở đích chưa có, backend thêm dòng đó và
   gắn lại trigger `handle_new_user` để có `profiles`. Giữ Supabase nguyên để
   quay lui (bỏ biến).
6. Sao lưu: pg_dump hằng đêm của Postgres Vibe Host (thay bản xuất qua API).

### 2. Đăng nhập trên Vibe Host
1. API đăng nhập riêng (`/api/v1/auth/*`): đăng ký, đăng nhập, làm mới phiên,
   đăng xuất, quên/đổi mật khẩu (email), xác nhận email. Mật khẩu bcrypt —
   băm của Supabase (`auth.users.encrypted_password`, bcrypt) dùng lại được,
   người dùng KHÔNG phải đặt lại mật khẩu (cần chuỗi kết nối DB để đọc).
2. Web: thay supabase-js trong `AuthContext.jsx` + 4 hook bằng API trên.
3. App Windows: bản mới dùng API trên; `DESKTOP_MIN_VERSION` buộc cập nhật.
   Trong thời gian chuyển, backend nhận cả phiên Supabase lẫn phiên mới.
4. Tắt Supabase Auth khi số app cũ còn dùng về 0.

## Cần từ chủ dự án
- **Chuỗi kết nối Postgres của Supabase** (Settings → Database → Connection
  string, URI, có mật khẩu). Đặt thành biến bí mật `SUPABASE_DB_URL` trên
  backend Vibe Host — KHÔNG dán vào chat. Không có nó: sơ đồ phải dựng từ git
  (có thể lệch bản thật) và mọi người dùng phải đặt lại mật khẩu.
- Dịch vụ gửi email (SMTP) cho quên mật khẩu / xác nhận email ở giai đoạn 2
  (Supabase đang tự gửi).
- Một khung giờ bảo trì ngắn (~15–30 phút) để chép dữ liệu ở bước 1.4.

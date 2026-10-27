# MINI-SPEC P1-PAY — Thanh toán tự động: VietQR (SePay) + thẻ (Stripe) cho Pro tháng/năm, Trọn đời, Team

| | |
|---|---|
| ID | `P1-PAY` |
| Name | Thanh toán tự động hai kênh (VND VietQR+SePay, USD Stripe) — một mô hình quyền lợi, một công tắc |
| Parent phase | Gói & thanh toán (tiếp `docs/PLANS-FREE-PRO.md` mục 3 phương án C) |
| Author / Date | Claude (lập kế hoạch) cho chủ dự án Triều — 27/10/2026 |
| Nhánh đọc | `feat/native-link` @ `3a1aa99` (27/10) · **CHỈ LÀ KẾ HOẠCH, chưa sửa code** |
| Quy ước | Theo `MINI_SPEC_PLAYBOOK.md` v2. Mọi số dòng dưới đây là ở commit trên. "Chưa kiểm" = chưa xác minh. |

> **Đã làm trước kế hoạch (27/10, commit 6effd1d):** đóng lỗ webhook Stripe nhận sự kiện không chữ ký khi thiếu
> `STRIPE_WEBHOOK_SECRET` (production đang thiếu; đo bằng body hỏng → "Invalid JSON"). Thiếu secret giờ trả 404.
> Bước 4 bên dưới không cần làm lại phần (a).

## 1. Context

**Tài liệu phải đọc trước:** `docs/PLANS-FREE-PRO.md` (quyết định 13/10: giá 49.000đ/399.000đ, cấp Pro tay 30/365 ngày), `docs/MIGRATION-DB-VIBEHOST.md` (DB đã sang Postgres Vibe Host 21/10, migration SQL chạy tay), `docs/ARCH.md`, `backend/tests/conftest.py`.

**Tài liệu nhà cung cấp đã đọc (27/10):**
- SePay — trường dữ liệu webhook, phản hồi mong đợi: https://developer.sepay.vn/en/sepay-webhooks/tich-hop-webhook
- SePay — xác thực webhook (API Key / HMAC-SHA256 / OAuth2): https://developer.sepay.vn/en/sepay-webhooks/xac-thuc
- SePay — retry/timeout: https://developer.sepay.vn/en/sepay-webhooks/xu-ly-loi
- SePay — Test mode: https://developer.sepay.vn/en/tien-ich-khac/test-mode/tao-webhook (trang cũ https://docs.sepay.vn/tich-hop-webhooks.html, IP ở https://docs.sepay.vn/lap-trinh-webhooks.html)
- Stripe — fulfillment Checkout: https://docs.stripe.com/checkout/fulfillment?ui=full-page
- VietQR quick link: `vietqr.io` không mở được từ workspace; định dạng lấy từ nguồn cộng đồng (https://github.com/vietqr/vietqr-node, https://viblo.asia/p/tao-ma-qrcode-chuyen-tien-ngan-hang-7ymJXnd5Vkq): `https://img.vietqr.io/image/<BANK_BIN>-<SO_TK>-compact2.png?amount=<VND>&addInfo=<NOI_DUNG>&accountName=<TEN>` → hàm tạo ảnh QR phải **thay được**.

**Trạng thái hiện tại (đã kiểm, chi tiết mục 5):**
- VND = chuyển khoản tay: mã `VG`+8 hex theo tài khoản (`backend/app/core/plans.py:99-102`), admin tìm theo mã (`backend/app/api/plans.py:82-95`), cấp Pro 30/365 ngày (`backend/app/api/admin.py:920-990`, `billing_status='manual'`, hết hạn về Free 00:30 UTC `backend/app/tasks/video_tasks.py:1920-1961`).
- Stripe: có mã (`backend/app/api/payments.py`) nhưng web không gọi tạo checkout (chỉ portal `frontend/src/pages/BillingPage.jsx:199`); webhook chỉ xử lý subscription.
- Quyền lợi thật chỉ do `resolve_effective_tier` (`backend/app/core/entitlements.py:225-290`); web, tiện ích, app Windows đều qua đó (`backend/app/core/quotas.py:119-157, 341, 952-954`).
- Công tắc khẩn cấp có sẵn: `backend/app/core/admin_switches.py` + `backend/app/api/admin_switches.py` + `frontend/src/admin/pages/SwitchesPage.tsx`.

**Quyết định kiến trúc giữ nguyên:**
1. `resolve_effective_tier(profile)` là nơi duy nhất đổi `profiles` thành gói thật.
2. Giá và thông tin ngân hàng là cài đặt admin (Redis `plans:settings`, `plans.py:11-12, 22`); bí mật chỉ ở env.
3. Migration = `database/migrations/NNN_*.sql` idempotent, chạy tay (`docs/MIGRATION-DB-VIBEHOST.md:28-29`); backend qua `app/core/pg_client.py`.
4. Luồng cấp Pro tay (`VGxxxxxxxx` → "Tìm theo mã chuyển khoản" → Cấp Pro 30/365) không đổi.
5. Subscription Pro Stripe hiện có (webhook `customer.subscription.*`, portal) không đổi.

**Quyết định của owner (27/10):** VietQR + SePay tự kích hoạt (VND) cho khách Việt; thẻ qua Stripe (USD) cho khách nước ngoài; cả hai kênh bán 4 gói: Pro tháng 49.000đ, Pro năm 399.000đ, **Trọn đời 999.000đ**, **Team theo ghế** (giá trong admin; chủ team mời thành viên qua email; thành viên là Pro khi team còn hạn); ẩn sau một công tắc mặc định TẮT, admin xem thử được; đơn cả hai kênh xem ở một chỗ; trang thanh toán gợi ý kênh theo ngôn ngữ nhưng khách đổi được; câu chữ vi+en, BA duyệt.

## 2. Goal

Khách Việt hay nước ngoài trả tiền cho bất kỳ gói nào bằng VietQR hoặc thẻ và **được bật gói đúng, một lần, không cần admin**, trong khi mọi thứ vẫn ẩn sau một công tắc cho tới khi owner mở.

## 3. Constraints

1. Không viết lại máy tính quyền lợi: chỉ thêm 2 `billing_status` mới (`lifetime`, `team`) vào `resolve_effective_tier`.
2. Không bật gói khi chưa có bằng chứng tiền về đã xác thực. Thiếu khoá → webhook 404.
3. Một giao dịch nhà cung cấp xử lý đúng một lần: UNIQUE `payment_events(provider, provider_event_id)` (`database/migrations/015_phase20_billing.sql:104-116`) + compare-and-set trạng thái đơn.
4. Thiếu tiền / sai mã / trễ / không khớp → không tự kích hoạt; chờ admin (có audit).
5. Công tắc TẮT: khách không thấy gì; endpoint công khai mới 404; admin chạy được trọn luồng; webhook vẫn nhận.
6. Không ghi PII vào log; bí mật không vào DB/response/audit/log/bundle.
7. Mọi hành động admin ghi `log_admin_action` (`backend/app/core/audit.py:94-117`).
8. Không xoá cứng đơn/sự kiện.
9. Additive-first; chỉ sửa tối thiểu `payments.py`, `entitlements.py`, `video_tasks.py`, `admin_switches.py`, `invites.py`, `plans.py`.
10. Chuỗi mới đánh dấu `// wording: BA review`, nằm trong `locales/{vi,en}/checkout.json`.

## 4. Scope

### A. Domain model (`database/migrations/041_payment_orders.sql`)

Bảng mới `payment_orders` (một bản ghi đơn cho cả hai kênh): `id` UUID PK; `user_id` TEXT FK profiles; `workspace_id` UUID NULL (Team); `plan` IN ('pro_month','pro_year','lifetime','team'); `rail` IN ('vietqr','stripe'); `seats` INT DEFAULT 1; `currency` IN ('VND','USD'); `amount` BIGINT (VND đồng / USD cents); `code` TEXT UNIQUE (`VGP`+8); `status` IN ('pending','paid','underpaid','paid_late','expired','canceled','failed'); `paid_amount`; `paid_at`, `activated_at`, `expires_at` (+24 h), `created_at`, `updated_at`; `provider_ref`; `stripe_subscription_id`; `evidence` JSONB (đã lược PII); `admin_note`. Index `(user_id, created_at DESC)`, `(status, expires_at)`.

`payment_events` (có sẵn): thêm `order_id`, `match` ('matched','unmatched','duplicate','underpaid','late','simulated'), `amount`; `provider` thêm `'sepay'`.

`workspaces` (có sẵn, `009_phase11.sql:9-40`): thêm `seats_paid`, `paid_until`, `billing_rail`, `stripe_subscription_id`, `last_order_id`.

`profiles`: không thêm cột; dùng `billing_status` `lifetime` (expiry NULL) và `team` (expiry = `workspaces.paid_until`). CHECK tier đã bỏ (`019:64-65`); trigger `035` chỉ chặn vai authenticated/anon — **kiểm `auth.role()` compat trên Postgres Vibe Host** trước khi chạy 041.

Cài đặt admin `plans:settings` thêm: `price_lifetime_vnd` (999000), `price_team_seat_month_vnd`, `price_team_seat_year_vnd` (0 = chưa bán); `usd_pro_month_cents`, `usd_pro_year_cents`, `usd_lifetime_cents`, `usd_team_seat_month_cents`, `usd_team_seat_year_cents`; Stripe price id `stripe_price_pro_month|pro_year|lifetime|team_seat_month|team_seat_year` (trống → env cũ `STRIPE_PRO_PRICE_ID`… và mới `STRIPE_LIFETIME_PRICE_ID`); `bank_bin`, `sepay_auth_mode` ('hmac'|'apikey'), `order_ttl_hours` (24), `max_pending_orders` (3).

Công tắc `payments_auto` (env `PAYMENTS_AUTO_ENABLED`, mặc định off) trong `OVERRIDABLE` (`admin_switches.py:43`).

### B. Service / engine

- **Mới `core/payments_auto.py`**: `enabled()`; `visible(request)` = enabled hoặc phiên admin (`quotas.is_admin_request`, `quotas.py:289-300`).
- **Mới `core/payment_orders.py`**: `catalog()`, `on_sale()`; `new_code()` = `VGP` + 8 ký tự Crockford base32 (không trùng mã tay `VG`+hex); `create_order()` (kiểm bán, tối đa đơn chờ, Team `seats ≥ thành viên`); `qr_image_url()` một hàm thay được (thiếu `bank_bin` → None); `activate_order()` compare-and-set rồi áp quyền lợi: Pro tháng/năm → tách `_manual_pro_fields` (`admin.py:897-917`) ra core dùng chung, `billing_status='manual'` nối hạn; Trọn đời → `tier='pro', billing_status='lifetime', expiry NULL`; Team → `team_seats.apply_order`; đồng bộ `user_usage.plan` như `admin.py:949-958`; audit. `expire_pending()` mỗi giờ.
- **Mới `core/sepay_webhook.py`**: `verify()` theo `sepay_auth_mode` — HMAC (`X-SePay-Signature: sha256=<hex>`, `X-SePay-Timestamp`, chuỗi ký `"{ts}.{raw_body}"`, env `SEPAY_WEBHOOK_SECRET`, lệch > 300 s từ chối) hoặc `Authorization: Apikey <SEPAY_WEBHOOK_API_KEY>`; allowlist IP tuỳ chọn `SEPAY_WEBHOOK_IPS`. `parse()` lấy `id`, `code` (null → regex `VGP…` trong `content`), `transferAmount`, `transferType`, `transactionDate` (giờ VN → UTC). `handle()` insert-bắt-UNIQUE; `out` → unmatched; đủ tiền → activate; thiếu → underpaid; đơn hết hạn → paid_late; luôn trả 200 `{"success": true}` cho sự kiện đã xác thực (SePay retry 7 lần ~33 phút nếu khác).
- **Sửa `api/payments.py` (Stripe)**: session cho đơn (`metadata.order_id`, `client_reference_id`; Trọn đời `mode=payment`; Team `mode=subscription` + `quantity=seats`); webhook: idempotency insert-bắt-UNIQUE, DB lỗi → 500 cho Stripe retry; `checkout.session.completed` / `async_payment_succeeded` có `order_id` + `payment_status=='paid'` → `activate_order`; `async_payment_failed` → failed; không `order_id` → đường cũ; `customer.subscription.updated/deleted` của Team → cập nhật `paid_until`/`seats_paid` rồi sync. `create_checkout_session_v2`: `plan in ('team','api')` → 400 (hoàn tất lỗi `docs/PLANS-FREE-PRO.md:60`). `_upgrade_user` ghi cứng `tier='pro'` (`:91-105`) — chỉ dùng cho đường không có đơn.
- **Mới `core/team_seats.py`**: `apply_order()` tạo/gia hạn workspace team; `sync()` ghi thành viên active `tier='pro', billing_status='team', expiry=paid_until`, không đè `manual/active/lifetime/canceling/past_due`; `can_invite()`.
- **Sửa `api/invites.py`**: tạo lời mời kiểm ghế (402 `team_seats_full`); nhận lời mời so email khi workspace trả tiền (403) — hiện `:175-215` không so email; hook sync khi thêm/xoá thành viên.
- **Sửa `core/entitlements.py:272-290`**: `lifetime` → declared; `team` → declared nếu còn hạn, không thì free.
- **Sửa `tasks/video_tasks.py:1936`**: thêm `team` vào danh sách hết hạn.
- **Mới `tasks/payment_tasks.py`** (+ `celery_app.py` include/beat): `expire_payment_orders` (phút 20 mỗi giờ), `sync_team_entitlements` (00:35).
- **Sửa `core/admin_switches.py`, `api/admin_switches.py:63-118, 189-191`**: khoá `payments_auto` + nhãn (wording BA).
- **Sửa `core/limiter_routes.py:33-60`**: miễn giới hạn mặc định cho webhook Stripe + SePay (đã có xác thực + idempotency).
- **Sửa `core/plans.py:131-212`**: `build_plans(visible)` thêm `lifetime`, `team` (VND/USD) và `payment.auto` chỉ khi visible.

### C. API contract (mọi route công khai mới trả 404 khi `payments_auto.visible` là False)

| Route | Yêu cầu | Trả về |
|---|---|---|
| `POST /api/v1/checkout/orders` (Bearer, 10/phút) | `{plan, rail, seats?, workspace_id?}` | 201 `{order, vietqr: {bank_bin, bank_name, bank_account, bank_holder, content, qr_url\|null}\|null, stripe: {checkout_url, session_id}\|null}`; 400 `plan_not_on_sale`/`seats_below_members`; 409 `too_many_pending`; 503 `rail_not_configured` |
| `GET /api/v1/checkout/orders/{id}` (chủ đơn) | — | đơn + `entitlement {tier, billing_status, expiry}`; web poll 5 s |
| `GET /api/v1/checkout/orders` | — | 20 đơn mới nhất |
| `POST /api/v1/checkout/orders/{id}/cancel` | — | pending → canceled |
| `GET /api/v1/plans` (có sẵn) | — | thêm `lifetime`/`team` + `payment.auto` chỉ khi visible |
| `POST /api/v1/payments/sepay/webhook` | payload SePay | 200 `{"success": true}`; 401; 400 JSON hỏng; 404 chưa cấu hình |
| `POST /api/v1/payments/webhook` (có sẵn) | Stripe event | như cũ + nhánh `order_id` |

Admin (`api/admin_checkout.py`, `verify_admin`, audit mọi POST): `GET /admin/checkout/orders` (cả VND lẫn Stripe), `GET /admin/checkout/events?match=`, `POST …/orders/{id}/activate|cancel` (`{reason}`), `POST …/events/{id}/attach` (`{order_id}`), `POST …/orders/{id}/simulate-sepay` (payload giả chạy cùng `handle`, `provider_event_id="sim-<uuid>"`), `GET /admin/checkout/stripe/prices`; `GET/POST /admin/plans/settings` thêm trường; `/admin/switches` có `payments_auto`.

### D. UI

Web: `/pricing` thêm Trọn đời + Team khi mở (tắt thì y nguyên); trang mới `/checkout` (query `?plan=&rail=&order=`; kênh mặc định theo ngôn ngữ: vi → VietQR, en → Stripe; nút đổi kênh luôn hiện; VietQR = ảnh QR + 4 trường chép + đếm ngược + poll; Stripe = chuyển trang rồi quay về poll; đủ trạng thái tải/lỗi/chưa cấu hình/hết hạn/thiếu tiền); `/billing` thêm "Đơn thanh toán" và khối Team (reuse `WorkspaceSettingsPage.jsx`/`WorkspaceContext.jsx`); xem trước admin `?preview=admin` (gửi `X-Admin-Token`, không ghi cache `vg_plans_v1`); i18n `locales/{vi,en}/checkout.json`, sửa `pricing.json`, `billing.json`.

Admin: trang mới `OrdersPage.tsx` (`/vid-admin/orders`, nhóm Manage, `minRole: 'operator'`); `PlansBilling.tsx` thêm giá/BIN/chế độ SePay/price id + "Kiểm giá trên Stripe" (chỉ hiện "đã cấu hình/chưa" cho khoá); `SwitchesPage.tsx` không sửa.

### E. Tests

Unit (catalog, mã, tier `lifetime/team`, verify SePay HMAC/Apikey/lệch giờ/thiếu khoá, parse, compare-and-set, sync không đè gói riêng); integration (`TestClient` + FakeDB như `tests/test_admin_desktop.py:60-131` + fakeredis; đủ tiền/trùng/thiếu/sai mã/trễ; Stripe ký `whsec_test` cho `mode=payment` và `subscription` quantity 5; v2 team → 400); regression (snapshot `/plans` khi tắt từng byte; luồng tay; subscription Stripe cũ; job 00:30 hạ `team`, không đụng `lifetime`); live (SePay Test mode + Stripe test mode). **Chạy một phiên pytest một lúc.**

## 5. Audit Before Build (đã kiểm 27/10, commit 3a1aa99)

| Hạng mục | Bằng chứng | Đánh giá |
|---|---|---|
| Stripe env/redirect | `payments.py:35-45` | `_FRONTEND_URL` mặc định `vidgrab.io` → phải đặt `FRONTEND_URL=https://dowloadvideo.io.vn` |
| `_upgrade_user` | `payments.py:91-105` | ghi cứng `tier='pro'`, bỏ `metadata.plan/seats` → gap |
| Webhook Stripe | `payments.py:202-279` | nhận sự kiện không chữ ký khi thiếu secret — **đã vá 6effd1d**; `mode=payment` bị bỏ → chưa có Trọn đời |
| Idempotency Stripe | `payments.py:453-486` | select-rồi-insert (race), trả True khi DB lỗi |
| Checkout v2 | `payments.py:381-448` | lỗi "Team bán giá Pro" sửa nửa (`:419-423`) |
| Giá/ngân hàng/mã tay | `plans.py:22-34, 96-110, 131-212` | mã theo tài khoản → cần mã theo đơn |
| Admin cấp Pro | `api/plans.py:82-95`; `admin.py:311-317, 897-990`; `PlansBilling.tsx`, `UsersPage.tsx:387-390` | giữ nguyên; tách `_manual_pro_fields` |
| Lưu gói/hạn | `004_monetization.sql:9-14`; `015:140-148`; `019:64-65`; `035` | đủ cột |
| Gói thật một nơi | `entitlements.py:225-290`; `quotas.py`; `api_quota.py:48-58`; `tests/test_effective_tier_is_one_rule.py` | thêm 2 trạng thái là đủ |
| Job 00:30 | `video_tasks.py:1920-1961`; `celery_app.py:287-290` | thêm `team` |
| Công tắc | `admin_switches.py:40-110`; `api/admin_switches.py:63-118, 189-223`; `SwitchesPage.tsx:253-269` | reuse |
| Rate limit | `main.py:42-70`; `limiter_routes.py:33-60` | webhook chưa được miễn |
| Log/audit | `structured_log.py:70-85`; `audit.py:29, 94-117` | log không che email/nội dung CK → tự không ghi |
| Team | `009_phase11.sql:9-70`; `workspace.py`, `invites.py`; `rbac.py:72-151` | reuse; lời mời không so email; không có gửi email |
| Web | `PricingPage.jsx:16, 56-140`; `lib/usePlans.js`; `App.jsx:84-97`; `i18n/index.js:27-32` | route mới dùng query |
| Migration | số kế tiếp **041** | |

## 6. Design Choice

**Chọn:** một bảng đơn `payment_orders` + một hàm `activate_order` cho cả hai kênh; quyền lợi ghi vào `profiles` bằng đúng cột và luật hiện có; Team reuse `workspaces`; ẩn bằng khoá `payments_auto` trong cơ chế công tắc sẵn có. Hai webhook chỉ là hai nguồn bằng chứng đổ vào một máy trạng thái đơn; thêm kênh mới = thêm một bộ verify+parse. Thành viên Team được ghi thẳng `tier='pro', billing_status='team'` nên mọi nơi đọc gói không phải sửa. Mã chuyển khoản theo đơn (`VGP`+8) để khớp đúng số tiền và gói.

**Bỏ:** bảng `teams` riêng; mã theo tài khoản; Stripe cho VND; tính gói thành viên lúc đọc; tắt webhook khi công tắc tắt.

## 7. Các bước triển khai

| # | Việc | File | Test | Công |
|---|---|---|---|---|
| 0 | Migration 041; kiểm `auth.role()` compat | `database/migrations/041_payment_orders.sql` | chạy 2 lần không lỗi | 0,5 d |
| 1 | Công tắc + catalog + trạng thái mới + job | `core/admin_switches.py`, `api/admin_switches.py`, `core/payments_auto.py`, `core/plans.py`, `core/entitlements.py`, `tasks/video_tasks.py` | `test_payments_auto_switch.py`; `test_plans.py` (+ snapshot); `test_effective_tier_is_one_rule.py` | 1 d |
| 2 | Engine đơn + VietQR + endpoint người dùng + task hết hạn | `core/payment_orders.py`, `api/checkout.py`, `tasks/payment_tasks.py`, `celery_app.py`, `core/limiter_routes.py` | `test_checkout_orders.py` | 1,5 d |
| 3 | Webhook SePay | `core/sepay_webhook.py`, route trong `api/payments.py` | `test_sepay_webhook.py` (kể cả caplog không PII) | 1,5 d |
| 4 | Kênh Stripe | `api/payments.py` | `test_stripe_rail.py` | 1,5 d |
| 5 | Team | `core/team_seats.py`, `api/invites.py`, `api/workspace.py`, `tasks/payment_tasks.py` | `test_team_seats.py` | 1,5 d |
| 6 | Admin | `api/admin_checkout.py`, `api/plans.py`, `admin/pages/OrdersPage.tsx`, `AdminRoutes.tsx`, `layout/navConfig.ts`, `pages/PlansBilling.tsx` | `test_admin_checkout.py` | 1,5 d |
| 7 | Web | `pages/CheckoutPage.jsx`, `App.jsx`, `PricingPage.jsx`, `BillingPage.jsx`, `lib/usePlans.js`, locales | build + eslint + kiểm tay | 2 d |
| 8 | Docs + live | `docs/PLANS-FREE-PRO.md`, `docs/ARCH.md`, `docs/PRODUCT-STATUS.md`, `.env.example` | SePay/Stripe test mode | 0,5 d |

## 8. Bảo mật

Xác thực webhook theo tài liệu nhà cung cấp, constant-time, HMAC trên raw body, lệch giờ ≤ 300 s; số tiền ≥ giá đơn và đơn `pending` (Stripe: `payment_status=='paid'`, `amount_total`/`currency` khớp); replay chặn bằng UNIQUE + compare-and-set; tạo đơn 10/phút + tối đa 3 đơn chờ; log chỉ `order_id`/`tx_id`/số tiền/kết quả; bí mật chỉ env; đơn chỉ chủ đơn xem; ghế Team phải trùng email.

## 9. Test Plan

Unit + integration + regression như 4E; live: (1) pytest ký thử; (2) admin "Mô phỏng webhook SePay"; (3) SePay Test mode sau khi owner đăng ký; (4) chuyển khoản thật 1.000đ với giá test trước khi mở; Stripe `stripe listen` + thẻ `4242…` cho Trọn đời và Team 2 ghế.

## 10. Rollback

Tắt `payments_auto` → khách hết thấy, endpoint công khai 404, webhook vẫn xử lý đơn đã tạo; đơn đã kích hoạt giữ quyền lợi. Schema 041 chỉ thêm.

## 11. Checklist cho owner khi mở

**Env:** `SEPAY_WEBHOOK_SECRET` hoặc `SEPAY_WEBHOOK_API_KEY`; `SEPAY_WEBHOOK_IPS` (tuỳ chọn); `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` (endpoint `/api/v1/payments/webhook`, sự kiện `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.payment_failed`); `FRONTEND_URL=https://dowloadvideo.io.vn`.
**Stripe:** tạo Price Pro tháng, Pro năm, Trọn đời (one-time), Team ghế tháng (per-unit), Team ghế năm (tuỳ chọn); dán Price ID vào admin.
**SePay:** liên kết tài khoản ngân hàng; webhook POST JSON tới `/api/v1/payments/sepay/webhook`, sự kiện "In", auto retry, HMAC-SHA256, lọc tiền tố `VGP`.
**Admin:** giá Trọn đời, giá ghế Team (VND/USD), giá USD Pro, `bank_bin`, chế độ SePay, price id; "Kiểm giá trên Stripe".
**Migration:** chạy 041 (backup trước). **Mở:** xem thử `?preview=admin`, rồi bật công tắc "Thanh toán tự động".

## 12. Success Criteria

1. Công tắc TẮT: `GET /api/v1/plans` giống hôm nay từng byte; `POST /api/v1/checkout/orders` → 404; `/pricing` không đổi; admin tạo đơn → 201.
2. Webhook SePay hợp lệ, đủ tiền, đúng mã → đúng gói < 5 s; gửi lại 5 lần → 1 kích hoạt, 1 audit.
3. Thiếu/sai mã/trễ → không đổi `profiles`; hiện ở `/vid-admin/orders`.
4. Stripe test mode: Trọn đời không hết hạn; Team 5 ghế → 5 tài khoản Pro ở web, tiện ích, app, API key; huỷ → hết hạn về Free.
5. Webhook không/sai chữ ký → 401/404, không ghi gì.
6. Luồng tay cũ và subscription Stripe cũ hoạt động như trước.
7. Không log nào chứa email, nội dung chuyển khoản, số tài khoản người gửi.

## 13. Câu hỏi mở cho owner

1. Giá ghế Team VND/USD; gói năm; ghế tối thiểu; thành viên dùng hạn mức Pro (100/ngày) hay hạn mức `team` (500/ngày, `quotas.py:74`)?
2. Giá USD Pro tháng/năm/Trọn đời.
3. Tiền về trễ/thừa: chờ admin (mặc định) hay tự kích hoạt?
4. Hạn đơn 24 h, tối đa 3 đơn chờ/tài khoản?
5. SePay HMAC (khuyến nghị) hay API Key?
6. Lời mời Team là link (chưa có gửi email); cần SMTP/Resend sau không?
7. Hoàn tiền/chargeback Stripe Trọn đời: xử lý tay?
8. Khi bật tự động, ẩn hộp chuyển khoản tay hay giữ làm dự phòng?

## 14. Báo cáo sau khi làm

Theo mục 7 Playbook: Summary · Audit · Design Choice · Changed Files · New API/DB/State · Tests · Live Verification · Remaining Limits.

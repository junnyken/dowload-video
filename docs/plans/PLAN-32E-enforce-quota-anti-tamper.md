# PLAN 32E — Chặn thật hạn mức 5/20 trên app Windows + chống lách

Ngày: 2026-10-07 · Nhánh: `feat/32d-p3` @ `45b31ee` (= production) · App 0.8.0 · Chỉ là KẾ HOẠCH, không sửa code.
Người đọc: chủ sản phẩm (quyết phạm vi / chi phí / chính sách) và dev. Mọi chỗ ghi **chưa kiểm** là chưa xác minh trong code hoặc trên máy thật.
Tiếp nối `PLAN-32D-desktop-local-cookies.md` §5, §5.4, §7.4, §9.2 (quyết định 07-10: grace 3/ngày, trần IP ×3, hoàn ≤ 10/ngày, shadow 7 ngày rồi ép `DESKTOP_MIN_VERSION=0.6.0`).

## 0. Mục tiêu và phạm vi

| | |
|---|---|
| Mục tiêu | Chuyển `CLIENT_QUOTA_MODE` từ `shadow` (chỉ đếm) sang `enforce` (từ chối thật) cho khách 5 / tài khoản 20 lượt/ngày, gộp mọi nền tảng, tài khoản dùng chung với web — để người dùng tạo tài khoản và nâng cấp. Kèm chống lách ở mức **tương xứng** và đường hỗ trợ khi chặn oan. |
| Cổng quyết định | **14/10/2026**: chủ sản phẩm đọc 7 ngày số shadow trên `/vid-admin/desktop-app` (§2) rồi chọn bật / chờ / đổi mức. |
| Ngoài phạm vi | Không đổi mức 5/20/100/500, không thu phí mới, không DRM, không Tauri updater tự động (cần ký mã, xem `docs/desktop/SIGNING-COSTS.md`), không chặn người tự chạy yt-dlp ngoài app. |

## 1. Hiện trạng đã kiểm trong code (45b31ee)

| Hạng mục | Trạng thái | Bằng chứng |
|---|---|---|
| Cờ máy chủ | `CLIENT_QUOTA_ENABLED` / `MODE` / `ENFORCE_FOR` (mặc định `user,device,anon`) / `OFFLINE_GRACE=3` / `REFUND_DAILY_MAX=10` / `IP_MULT=3`, đọc lúc gọi | `backend/app/api/client_quota.py:72-98` |
| Shadow không bao giờ từ chối, chỉ ghi `over_shadow` | ✔ | `client_quota.py:239-241`, `:257` |
| Enforce theo loại requester | `_enforced(req)` = mode enforce **và** `req.kind ∈ ENFORCE_FOR` | `client_quota.py:97-98` |
| Trả lời từ chối | 403 (tài khoản) / 429 (khách), `error_code=quota_exceeded_daily`, `upsell=signin\|upgrade`, `reason=daily_limit\|ip_limit` | `client_quota.py:213-221`, `:263-264` |
| Retro (tải ngoại tuyến) bỏ qua kiểm tra, tối đa grace/ngày/requester | ✔ | `client_quota.py:173-186` |
| Hoàn lượt khi settle ≠ completed: cùng requester, < 2 h, < `REFUND_DAILY_MAX`/ngày | ✔ | `client_quota.py:345-354` |
| Requester: admin > user > device (`dev:<32 hex>`) > anon(IP) | ✔ | `backend/app/core/quotas.py:351-368`; admin không bao giờ bị đếm `:503-504` |
| Trần IP phụ cho máy khách | `device_ip_used(ip) ≥ 5 × IP_MULT` → `ip_limit` | `client_quota.py:235-238`, `quotas.py:550-571` |
| `/client/version` | `latest` (`DESKTOP_LATEST_VERSION`), `minSupported` (`DESKTOP_MIN_VERSION`), `downloadUrl` (`DESKTOP_DOWNLOAD_URL`), `features` | `backend/app/api/client_api.py:224-251` |
| Trang admin "App Windows" | thẻ "Vượt lượt khi chỉ đếm" = `summary.over_shadow`; bảng tuyến × kết quả 7 ngày; bảng máy kèm `used/limit`, `hoàn`, `mất mạng`; cờ đang bật | `frontend/src/admin/pages/DesktopAppPage.tsx:336-342`, `:45-50`, `:258-261`; `backend/app/api/admin_desktop.py:327-378` |
| App: claim trước `start_download`, settle sau, dừng hàng đợi khi bị từ chối | ✔ | `desktop/src/lib/queue.ts:240-249`, `:136-138`, `:291-293`; `desktop/src/lib/quota.ts:136-162` |
| App: huy hiệu "Hôm nay x/y", thông báo từ chối + nút Đăng nhập / Nâng cấp / Thử lại | ✔ | `desktop/src/components/QuotaBadge.tsx:11-50` |
| App: cắt số video chọn trong kênh theo `remaining` **chỉ khi** `enforced=true` | ✔ | `desktop/src/lib/quota-core.ts:186-189`; `ChannelPicker.tsx:69` |
| Sản xuất hôm nay | `ENABLED=true`, `MODE=shadow`; `DESKTOP_LATEST_VERSION=0.7.4` (lệch, chủ đang chạy 0.8.0); `DESKTOP_DOWNLOAD_URL` rỗng; còn người dùng 0.1–0.5 | theo brief; **chưa kiểm** trực tiếp env trên Vibe Host |

### 1.1 Lỗ hổng / thiếu sót phát hiện khi đọc code (phải xử lý trong 32E)

| # | Phát hiện | Bằng chứng | Hệ quả |
|---|---|---|---|
| G1 | App **không làm gì** khi phiên bản < `minSupported`: chỉ so với `latest` để hiện huy hiệu "Có bản mới" | `desktop/src/screens/SettingsScreen.tsx:99`, `:191-199`; `minSupported` chỉ có trong `desktop/src/types/api.ts:53` | Ép nâng cấp hiện nay chỉ là một con số trên trang admin. Cần màn chặn (§4). |
| G2 | `/fetch-link` và `/client/douyin/video` không đưa `X-VG-Device` vào `resolve_requester` → khách từ app đi đường máy chủ bị đếm ở ô `ip:` (chung web), không phải `dev:` | `backend/app/api/routes.py:547`; `backend/app/api/client_douyin.py:71-73` | Khách trong app thực tế có 5 (máy) + 5 (IP) lượt; cùng URL tải cục bộ rồi rơi về máy chủ có thể bị đếm hai lần ở hai ô. PLAN-32D §5.2 đã ghi, chưa làm. |
| G3 | App < 0.6.0 không gửi `X-VG-Client`/`X-VG-Device`, không claim; tải cục bộ của chúng **không chạm máy chủ** | `desktop/src/lib/device.ts` ra đời ở 0.6.0 (`4ebcae6`) | Không thể chặn từ máy chủ; chỉ nhắc/chặn được các lời gọi máy chủ (Douyin, đồng bộ lịch sử). Nhận diện app qua `Origin: http://tauri.localhost` (`backend/app/main.py:374-379`). |
| G4 | Sổ ngoại tuyến + "hint" nằm trong `localStorage`; hint chưa biết (`enabled=false`) ⇒ tải **không đếm** | `desktop/src/lib/quota.ts:28-49`, `:153` | Chặn mạng tới API + xoá localStorage = tải không giới hạn (vẫn bị trần retro 3/ngày nếu có báo bù, nhưng app không báo gì). |
| G5 | Hoàn lượt tối đa 10/ngày **lớn hơn** hạn mức khách 5 | `client_quota.py:89-90`; test `backend/tests/test_client_quota.py:154` | Client sửa để luôn settle `failed` được tới 15 lượt/ngày. |
| G6 | Admin chỉ có `reset-user-quota` (bảng `user_usage.downloads_today`), không đụng bộ đếm Redis theo nền tảng / máy | `backend/app/api/admin.py:894-907` | Không có cách cấp thêm / đặt lại cho một máy hoặc một tài khoản khi chặn oan. |
| G7 | Đồng bộ lịch sử chỉ cho tài khoản đăng nhập | `client_api.py:140-147` (`get_required_user`) | Tín hiệu "có tải mà không claim" chỉ đo được cho người đăng nhập. |
| G8 | `OPEN_URL_HOSTS` chỉ `dvid.vibe1.tinhgon.xyz` + `dvid-api…` | `desktop/src-tauri/src/validate.rs:86` | Link tải bản mới phải nằm trên host này, nếu không app (kể cả bản cũ) không mở được bằng nút; `CopyLink` vẫn dùng được. |

## 2. Cổng quyết định 14/10

Đọc trên `/vid-admin/desktop-app`, chọn "7 ngày". Ký hiệu từ `admin_desktop.py:355-369`:
**C** = tổng lượt đã đếm = `counted_local + counted_local_cookie + counted_server` · **V** = "Vượt lượt khi chỉ đếm" (`over_shadow`) · **R** = `refunds_today` (chỉ hôm nay — cộng tay 7 ngày hoặc đợi P0) · **M** = số máy `total` / `new_in_period` / `active_today` · **T** = `retro` · **F** = `settle_failed + settle_cancelled`.
Những số **chưa có** trên trang, P0 (§8) bổ sung trước 14/10: tách khách / tài khoản, top 10 máy theo V, phổ phiên bản app (kể cả "không có header" = app < 0.6.0), R và T theo ngày.

| Chỉ số | Ngưỡng | Đọc là | Gợi ý |
|---|---|---|---|
| V / C | < 2 % | hạn mức hiếm khi chạm; bật gần như không ai thấy | **Bật ngay**, có thể rút mỗi bước còn 2 ngày |
| V / C | 2–15 % | bình thường, chạm ở thiểu số | **Bật theo thứ tự §3**, giữ 3 ngày/bước |
| V / C | > 15 % **và** top 5 máy ≥ 50 % V | vài máy tải dồn (dùng nặng / lạm dụng) | Bật; xem top máy ở P0; không đổi mức |
| V / C | > 30 % **và** trải đều nhiều máy khách | mức 5 chặt so với dùng thật | Chủ quyết: giữ 5 (ép đăng nhập) hoặc nâng `PLATFORM_DAILY_LIMIT_ANON` lên 8 — đổi env, không đổi code |
| Khách vs tài khoản trong V | ≥ 70 % là khách | đúng mục tiêu "ép đăng ký" | Bật `device` sớm hơn (gộp bước S2+S3) |
| R / claim | > 30 % hoặc có máy R ≥ 5/ngày | hoàn lượt đang bị lợi dụng hoặc tải lỗi nhiều | Hạ `CLIENT_QUOTA_REFUND_DAILY_MAX` xuống 3 **trước** khi bật; soi mã lỗi `settle_failed` |
| T / C | > 10 % | "mất mạng" bị dùng như cửa lách | Đặt `CLIENT_QUOTA_OFFLINE_GRACE=1` (0 nếu > 25 %) trước khi bật |
| M active_today / total | thấp (< 30 %) | nhiều máy vãng lai | Bật — ít người bị ảnh hưởng |
| App < 0.6.0 (phổ phiên bản) | > 30 % lời gọi app | phần lớn chưa đếm gì | Bật vẫn đúng (không ảnh hưởng họ), nhưng lên lịch ép nâng cấp §4 sớm |
| Bất kỳ ngày `redis_ok=false` | — | số không tin được | **Chờ** thêm 3 ngày sạch |

Quyết định ghi vào task AI Factory kèm ảnh chụp trang admin ngày 14/10.

## 3. Thứ tự bật (dùng cờ sẵn có, mỗi bước đổi 1 env)

Cờ đọc lúc gọi (`client_quota.py:61-98`) nhưng trên Vibe Host đổi env phải `set_env` + `redeploy_project` (**đã kiểm 07/10**: đổi env không có hiệu lực tới khi deploy lại; đổi env sau khi deploy đã bắt đầu thì Vibe Host báo `NO_CHANGE` → dùng biến đánh dấu `VIDGRAB_ENV_REV` để ép deploy). Lưu ý `admin` có hạn -1 nên `ENFORCE_FOR=admin` **không chặn ai** — bước S1 chỉ để kiểm dây.

| Bước | Env | Thời lượng | Ai bị ảnh hưởng | Theo dõi | Người dùng thấy (chữ do BA duyệt) |
|---|---|---|---|---|---|
| S0 (≤ 13/10) | giữ `MODE=shadow`; deploy P0 + phát hành app 0.9.0; `DESKTOP_LATEST_VERSION=0.9.0`, `DESKTOP_DOWNLOAD_URL=<link>` | đến 14/10 | không ai | số §2 | 0.6+: Cài đặt báo "Có bản mới 0.9.0" + link |
| S1 | `CLIENT_QUOTA_MODE=enforce`, `CLIENT_QUOTA_ENFORCE_FOR=admin` | 1 ngày | không ai (admin không đếm) | `GET /client/quota` trả `mode=enforce`, `enforced` đúng theo loại; `refused`=0; app chủ sản phẩm chạy bình thường | không gì |
| S1b (tuỳ chọn, Q1) | `CLIENT_QUOTA_ENFORCE_USERS=<id chủ, id tester>` | 2 ngày | 2 tài khoản | từ chối ở lượt 21, nút "Nâng cấp" mở web, kênh bị cắt ở `remaining` | `[Bạn đã dùng hết 20 lượt hôm nay. Lượt mới lúc 07:00.]` + nút Nâng cấp |
| S2 | `ENFORCE_FOR=admin,user` | 3 ngày | mọi tài khoản đăng nhập (20/ngày chung với web) | `refused`/ngày, ticket hỗ trợ, lượt mua gói, `settle_failed` không tăng vọt | như trên; web và app cộng chung |
| S3 | `ENFORCE_FOR=admin,user,device` | 3 ngày | khách dùng app ≥ 0.6.0 (5/máy, trần 15/IP) | `refused` tách `reason=ip_limit` vs `daily_limit`; số tài khoản mới/ngày (Supabase Users — **chưa có** trên trang); top máy bị chặn | `[Bạn đã dùng hết 5 lượt của khách hôm nay.]` + nút "Đăng nhập để có 20 lượt/ngày"; văn phòng: `[Mạng này đã dùng hết lượt tải của khách hôm nay. Đăng nhập để…]` (`client_quota.py:219-221`) |
| S4 | bỏ `ENFORCE_FOR` (mặc định cả ba) | 1 ngày | app không có/sai header máy (hiếm: chỉ bản dev) | `refused` cho `requester=anon` ≈ 0 | — |
| S5 | `DESKTOP_MIN_VERSION=0.6.0` (+ cổng 426 ở §4) | khi phổ phiên bản < 0.6.0 ≤ 10 % hoặc chủ chấp nhận | người còn dùng 0.1–0.5 | lời gọi 426/ngày giảm dần | 0.5: Douyin/lịch sử báo `[Bản VidGrab này đã cũ. Tải bản mới tại …]`; tải cục bộ của họ vẫn chạy (G3) |

**Rollback** (1 env, không cần đổi app): `CLIENT_QUOTA_MODE=shadow` — vẫn đếm, không chặn. Khẩn cấp: `CLIENT_QUOTA_ENABLED=false` → app nhận 503 và tải như trước, huy hiệu ẩn (`quota-core.ts:54-56`). Hàng đợi đang bị gate tự mở khi `refreshQuota()` thấy 503 / sang ngày (`quota.ts:214-225`). Đảo lại S5: hạ `DESKTOP_MIN_VERSION`.

## 4. App cũ và ép nâng cấp

**Sự thật cần chấp nhận (G3):** app < 0.6.0 tải YouTube/TikTok/… bằng yt-dlp cục bộ mà không gọi máy chủ → không có cách nào chặn từ máy chủ. Chỉ có thể (a) chặn những gì chúng gọi máy chủ, (b) nhắc nâng cấp, (c) chờ chúng tự biến mất. Dân số này **đo được** qua phổ phiên bản P0 (lời gọi có `Origin: http://tauri.localhost` mà thiếu `X-VG-Client`).

| Lớp | Việc | Chi tiết |
|---|---|---|
| App 0.9.0 (P1) — màn chặn | Khi `GET /client/version` trả `minSupported` > phiên bản app (`versionLess`, `desktop/src/lib/format.ts:56-64`) → `App.tsx` render **màn toàn cửa sổ** thay mọi màn hình: tiêu đề, `notes`, nút "Tải bản mới" (`api.openUrl(downloadUrl)`), nút sao chép link (`CopyLink`), nút "Kiểm tra lại". Hàng đợi không khởi động (`initQueue` sau khi kiểm). Kiểm lúc mở app và mỗi 6 h; mất mạng → chạy bình thường (không chặn vì không biết). Chữ: BA duyệt. Nhớ: màn này **chỉ có từ 0.9.0**; nên khi nâng `DESKTOP_MIN_VERSION` lên > 0.8.0 sau này, bản 0.6–0.8 vẫn chỉ thấy huy hiệu "Có bản mới". |
| Máy chủ (P1) — cổng 426 | `require_min_client()` dùng chung cho `/client/douyin/*`, `/client/history`, `/client/quota/*`, và `/fetch-link` **chỉ khi** `Origin ∈ {tauri origins}` hoặc `X-VG-Source: desktop`: nếu `X-VG-Client` thiếu hoặc < `DESKTOP_MIN_VERSION` → `426 {error_code:"update_required", detail, downloadUrl, minSupported}`. Web không bị động vào (không có Origin tauri). **chưa kiểm**: bản 0.5 hiện `detail` của máy chủ ra sao (`errors.ts:26-30` gợi ý có hiện cho Douyin). Bản 0.6–0.8 nhận 426 ở claim → `decideClaim` coi là "offline" → dùng 3 lượt grace rồi báo "Không kết nối được máy chủ…" (chữ sai nhưng chặn được) → vì vậy **không nâng min > 0.8.0 trước khi 0.9.0 phủ ≥ 90 %**. |
| Phân phối (điều kiện tiên quyết của S0) | `DESKTOP_DOWNLOAD_URL` đang rỗng, bộ cài chia tay. Đề xuất: đặt file `VidGrab_<ver>_x64-setup.exe` + `.sha256` ở `https://dvid.vibe1.tinhgon.xyz/download/` (static qua nginx của frontend, `frontend/nginx.conf` — **chưa kiểm** dung lượng cho phép trên Vibe Host, bộ cài ~100 MB vì kèm ffmpeg/deno) và một trang `/download` ngắn có SHA256. Host này đã nằm trong `OPEN_URL_HOSTS` nên **mọi** bản app (cả cũ) mở được. Phương án B: GitHub Releases — nút mở link **không** chạy ở bản cũ (G8), chỉ sao chép. |
| Env | `DESKTOP_LATEST_VERSION` nâng theo mỗi phát hành (hiện lệch 0.7.4 vs 0.8.0); `DESKTOP_RELEASE_NOTES` 1–2 câu tiếng Việt. |

Không làm Tauri updater tự động trong 32E: cần cặp khoá updater + manifest + nên có ký mã (`Cargo.toml:33-36` feature `updater` đang tắt, `tauri.conf.json` `createUpdaterArtifacts:false`). Quyết định riêng sau khi có chữ ký mã.

## 5. Chống lách — tương xứng

### 5.1 Nói thẳng về giới hạn
- Ai cũng chạy được yt-dlp ngoài app (PLAN-32D §5.4). Hạn mức là công cụ **điều hướng** (đăng nhập / nâng cấp), không phải DRM.
- Trong Tauri 2, bundle JS nằm **trong** file `.exe` (`frontendDist` nhúng lúc build), không phải tệp rời như Electron → "sửa JS" thực chất đã là vá binary. Vì vậy các đường lách rẻ còn lại là: (1) **giả máy chủ** (hosts file + CA tự ký + mitmproxy trả `allowed:true` hoặc 503 `client_quota_disabled`), (2) **chặn mạng** để vào grace (G4), (3) đổi mã máy (registry/máy ảo) — đã có trần IP, (4) settle `failed` giả để hoàn (G5). Token ký chỉ chặn (1); (2) chặn bằng sổ phía Rust; (3)(4) chặn bằng trần + tín hiệu admin.

### 5.2 (a) Token claim có chữ ký — xác minh trong Rust trước `start_download` (P3)

| Mục | Thiết kế |
|---|---|
| Khoá | Ed25519. Khoá riêng: env `CLIENT_QUOTA_SIGNING_KEY` (base64 seed 32 byte) + `CLIENT_QUOTA_SIGNING_KID` (vd `k1`); sinh một lần bằng script `scripts/gen_quota_key.py`, lưu dự phòng trong kho mật khẩu của chủ. Python: `cryptography` (**chưa kiểm** đã có trong `backend/requirements*.txt`; nếu không, `PyNaCl`). |
| Token (thêm vào trả lời `claim` / `claim-batch`) | `token = b64url(payload) + "." + b64url(sig)`; payload JSON gọn `{v:1, kid, cid:<claimId>, uh:<sha256(url đúng chuỗi app gửi)[:32]>, req:<req.key>, exp:<ts claim + 2h>, mode}`. Dùng **chuỗi URL nguyên văn** cho `uh` để Rust không phải tái hiện `platform_key`/fingerprint của máy chủ. |
| Chính sách ký (để Rust biết có phải đòi token không) | `/client/version` trả thêm `policy` ký cùng khoá: `{v:1, kid, requireToken:<ENABLED && MODE=enforce && ENFORCE_FOR≠∅>, grace:<OFFLINE_GRACE>, exp:+24h}`. Rust đọc **từ chính Rust** (không nhận qua webview) lúc mở app, mỗi 6 h và trước mỗi lần tải nếu bản cache > 1 h; cache `<app data>/policy.bin`. Cần HTTP client trong Rust: `ureq` (rustls, nhỏ) — **chưa kiểm** kích thước thêm vào exe. |
| Xác minh trong `start_download` (`desktop/src-tauri/src/lib.rs:731-800`, trước `run_cookies`/spawn) | Tham số mới `claim_token: Option<String>`. Nếu policy hợp lệ (chữ ký đúng, chưa hết hạn) và `requireToken=true`: token phải (i) đúng chữ ký với một khoá công khai nhúng, (ii) `exp > now`, (iii) `uh == sha256(url)[:32]`, (iv) `cid` chưa dùng trong phiên (set trong `Jobs`). Thiếu/sai → `Err(Code::ClaimRequired)`. Không có policy hợp lệ nào (chưa từng nối máy chủ) → hành vi cũ (nói thẳng: cài mới + chặn mạng = không chặn; chấp nhận, vì yt-dlp ngoài app cũng vậy). |
| Khoá công khai trong app | Nhúng lúc build qua `build.rs` như `pins.rs` (`desktop/src-tauri/build.rs:61-91`): đọc `desktop/quota-keys.json` `[{kid, pub}]` (tối đa 3). Crate `ed25519-dalek` (thêm `Cargo.toml`), `sha2` đã có. |
| Xoay khoá | Thêm khoá mới vào `quota-keys.json` → phát hành app → chờ `DESKTOP_MIN_VERSION` ≥ bản đó → đổi `CLIENT_QUOTA_SIGNING_KEY/KID` trên máy chủ → bản sau bỏ khoá cũ. Lộ khoá riêng: đổi ngay, bản app cũ (không có khoá mới) rơi về "policy không hợp lệ" = hành vi cũ cho đến khi cập nhật → kết hợp nâng `DESKTOP_MIN_VERSION`. |
| Ngoại tuyến (thay G4) | Sổ grace chuyển từ localStorage sang bảng SQLite `quota_grace(day, used, url, at)` trong `vidgrab.db` (chỉ Rust ghi). Rust chỉ cấp slot grace khi **chính Rust** không nối được `/client/version` (timeout 3 s); nối được mà không có token = JS cố bỏ qua → từ chối. Trần = `policy.grace`. JS vẫn gửi `retro` khi có mạng; máy chủ vẫn giới hạn bằng `_take_retro`. Mặc định hint khi chưa biết: **coi như bật** (fail-closed, sửa `quota.ts:153`) — làm ngay ở P1 không cần chờ P3. |
| Tắt / rollback | `requireToken=false` khi bất kỳ cờ nào tắt → Rust không đòi token trong ≤ 1 h (cache policy) hoặc ngay khi app mở lại. Thiếu `CLIENT_QUOTA_SIGNING_KEY` trên máy chủ → không có `policy`/`token` → app ≥ 0.10 chạy như trước (ghi log cảnh báo ở máy chủ). |
| Phương án B (ghi để so) | Chuyển cả việc claim/settle vào Rust (Rust có mã máy `device.rs` và phiên Supabase `auth.rs`, biết exit code yt-dlp nên settle thật). Chặn kín hơn (JS không thể nói dối `failed`) nhưng đụng toàn bộ `queue.ts`/`channels.ts`, ~2× công. Không khuyến nghị cho 32E; cân nhắc nếu G5 thành vấn đề thật. |

### 5.3 (b) Tín hiệu bất thường trên trang admin (P2) — chỉ hiển thị, không tự khoá

| Tín hiệu | Nguồn / khoá mới | Ngưỡng gợi ý |
|---|---|---|
| Nhiều máy sau một IP | SADD `vidgrab:devip:members:<ip>:<ngày>` trong `_claim_one` (hiện chỉ có bộ đếm) | ≥ 4 máy/ngày hoặc chạm `ip_limit` ≥ 3 ngày/7 |
| Nhiều tài khoản trên một máy | SADD `vidgrab:dev:users:<dev>:<ngày>` khi claim có user | ≥ 3 tài khoản/ngày (nuôi tài khoản để ×20) |
| Retro lặp | HINCRBY `vidgrab:stats:retro_by_req:<ngày>` (giữ 40 ngày như `_stat`) | retro ≥ grace ở ≥ 3/7 ngày |
| Hoàn lượt bất thường | HINCRBY `vidgrab:stats:refund_by_req:<ngày>` | hoàn ≥ 50 % claim và ≥ 3/ngày |
| Có tải mà không claim (chỉ tài khoản, G7) | `desktop_downloads` (`state=completed`, `device_id`, ngày) so với SADD `vidgrab:claims:<req>:<ngày>` (fingerprint) | tải hoàn tất > claim + grace |
| App cũ | HINCRBY `vidgrab:stats:client_version:<ngày>` `{<ver>|none}` từ mọi `/client/*` và `/fetch-link` có Origin tauri | dùng cho §2 và S5 |
| Nhiều mã máy "fallback" | `desktop_devices` cần cột `source` (migration 038, app gửi `X-VG-Device-Source`) — hoặc bỏ nếu không muốn migration | > 5 máy fallback/ngày từ một IP |

Hiển thị: mục "Dấu hiệu bất thường (7 ngày)" trên `DesktopAppPage`, mỗi dòng: mã máy / tài khoản, tín hiệu, giá trị, nút "Cấp thêm" / "Đặt lại" (§6). Không nối vào `anomaly_detector.py` (khoá `vidgrab:stats:<ngày>` khác họ, `anomaly_detector.py:4`); đọc trực tiếp trong `admin_desktop.py`. Bộ nhớ Redis: ~6 khoá nhỏ/requester/ngày, TTL ≤ 40 ngày — không đáng kể.

### 5.4 (c) KHÔNG làm (chi phí > lợi ích)

| Không làm | Vì |
|---|---|
| Vân tay phần cứng thêm (MAC, serial đĩa, CPU id) | riêng tư; máy ảo/đổi phần cứng gây chặn oan; người có kỹ năng vẫn giả được |
| Làm rối/obfuscate JS, anti-debug, phát hiện mitmproxy/CA lạ | JS đã nằm trong exe; anti-debug là mèo vờn chuột, dễ gây crash giả |
| Ép mọi lượt tải qua máy chủ để "kiểm soát" | ngược mục tiêu tiết kiệm băng thông/Apify của 32D |
| Xác minh SĐT/OTP cho 20 lượt | tốn phí SMS + rào cản; đã chặn email dùng một lần (migration 036) |
| Chặn VPN / IP datacenter | chặn oan văn phòng, không có dữ liệu cho thấy cần |
| Tự động khoá máy/tài khoản từ tín hiệu §5.3 | tín hiệu mềm; chủ/admin quyết tay, có audit |
| Tauri updater + ký mã | quyết định riêng (129–349 USD/năm, `SIGNING-COSTS.md`) |

## 6. Hỗ trợ và công bằng

| Tình huống | Vì sao bị chặn | Cách gỡ |
|---|---|---|
| Văn phòng / quán net chung NAT, nhiều máy khách | trần IP 15/ngày (`ip_limit`) | Thông điệp đã mời đăng nhập (ô `user:` không bị trần IP). Admin: "Đặt lại IP" hoặc nâng `CLIENT_QUOTA_IP_MULT` toàn cục. |
| Máy ảo nhân bản / 2 máy cùng `MachineGuid` | chung ô `dev:` | Đăng nhập tách ô; admin "Cấp thêm" cho máy. |
| Tài khoản dùng app + web cùng ngày | 20 chung (`test_client_quota.py:101-108`) | đúng thiết kế; nêu rõ trong thông điệp `[… gồm cả lượt tải trên web]`. |
| Tải lỗi nhiều lần (mạng kém) | hết 10 hoàn/ngày | admin "Đặt lại" cho requester; soi `settle_failed` mã lỗi. |
| Mốc reset 07:00 VN | ngày UTC (`quotas.py:216-217`) | thông điệp luôn kèm `resetTimeVn`. |

**Công cụ admin (P1, có audit qua `log_admin_action`, `backend/app/core/audit.py:94`):**
- `POST /admin/desktop/quota/grant {key:"dev:<32hex>"|"user:<id>", extra:<1..100>, reason}` → SET `vidgrab:quota:grant:<key>:<ngày>` (TTL đến nửa đêm UTC). `platform_limit()` (`quotas.py:371-376`) cộng grant → áp **cả web lẫn app** cho tài khoản.
- `POST /admin/desktop/quota/reset {key, reason}` → DEL `vidgrab:quota:plat:<key>:*:<ngày>`, `vidgrab:quota:seen:<key>:<ngày>`, `refund`, `retro`; với `dev:` trừ `devip` tương ứng (`device_ip_add(ip,-n)`).
- `POST /admin/desktop/ip/reset {ip, reason}` → DEL `vidgrab:quota:devip:<ip>:<ngày>`.
- Nút trên dòng máy ở `DesktopAppPage` (yêu cầu nhập lý do); hiện trong `AuditLogPage`.
- Thông điệp cho người dùng khi bị chặn kèm Mã máy (đã có ở Cài đặt) để hỗ trợ tra nhanh: `[… Nếu cho rằng bị nhầm, gửi Mã máy XXXXXXXX cho hỗ trợ.]` (BA duyệt).

## 7. Thay đổi theo lớp, kiểm thử, rollback

### 7.1 Máy chủ (FastAPI)

| Tệp | P0 | P1 | P2 | P3 |
|---|---|---|---|---|
| `client_quota.py` | phổ phiên bản; SADD claims/req; HINCRBY retro/refund theo req | `require_min_client()`; `CLIENT_QUOTA_ENFORCE_USERS` (Q1); `refund_daily_max(req)` theo loại (Q2) | SADD devip members / dev users | `token` trong claim/claim-batch; `signing.py` |
| `client_api.py` | — | 426 ở `/client/history`; `/client/version` thêm `policy` ký (P3) | — | `policy` |
| `routes.py` `/fetch-link`, `client_douyin.py` | **G2**: `device_id=_device_header(request)` vào `resolve_requester` (`routes.py:547`, `client_douyin.py:73`) — **BẮT BUỘC kèm trần IP** như `client_quota._claim_one` (`device_ip_used`/`device_ip_add`, `PLATFORM_DAILY_LIMIT_ANON × CLIENT_QUOTA_IP_MULT`). Thiếu trần IP thì bịa mã máy ngẫu nhiên = 5 lượt mới mỗi lần (lý do P0 cố ý chưa làm, ghi bởi người duyệt 07/10) | 426 khi Origin tauri | — | — |
| `quotas.py` | — | `platform_limit()` + grant | — | — |
| `admin_desktop.py` | stats: tách khách/tài khoản, top máy theo V, phiên bản, R/T theo ngày | grant/reset/ip-reset + audit | `GET /admin/desktop/signals?days=7` | — |
| DB | không | không (grant là Redis) | migration 038 chỉ nếu muốn cột `source` | không |
| Env mới | — | `CLIENT_QUOTA_ENFORCE_USERS`, `CLIENT_QUOTA_REFUND_DAILY_MAX_GUEST` (Q2), `DESKTOP_DOWNLOAD_URL` | — | `CLIENT_QUOTA_SIGNING_KEY`, `_KID` |

### 7.2 App

| Lớp | P1 (0.9.0) | P3 (0.10.0) |
|---|---|---|
| TS | màn "Cần cập nhật" (`App.tsx`), kiểm `minSupported` lúc mở + 6 h; hint fail-closed (`quota.ts:153`); hiện `reason=ip_limit` khác chữ; `CopyLink` link tải; mock `tauri.mock.ts` | truyền `token` từ claim/claim-batch vào `api.startDownload`; lỗi `claim_required` → thẻ failed + toast (BA) |
| Rust | — | `ed25519-dalek`, `ureq`; `policy.rs` (fetch/cache/verify), `claim_token.rs` (verify), `quota_grace` SQLite; `start_download(claim_token)`; khoá nhúng qua `build.rs` |
| Hợp đồng | `docs/desktop/C1-CONTRACT.md` §6 cập nhật 426 + màn cập nhật | thêm `token`, `policy`, `claim_required` |

### 7.3 Kiểm thử (gồm đối chứng âm)

| Lớp | Phải xanh | Đối chứng âm (phải ĐỎ/từ chối) |
|---|---|---|
| Backend `pytest` (một lượt một lúc) | G2: `/fetch-link` + `X-VG-Device` đếm ở `dev:`, claim cùng URL sau đó `alreadyCounted`; 426 với `X-VG-Client=0.5.0` Origin tauri; 200 với 0.6.0; web không header **không** 426; grant `dev:A` nâng `limit` của A; reset xoá đúng khoá; `ENFORCE_USERS` chỉ chặn id trong danh sách; token verify được bằng khoá công khai (test ký/giải ký trong Python) | shadow vẫn cho lượt 6 (`test_shadow_counts_but_never_refuses` giữ nguyên); grant A **không** đổi B; token đổi 1 byte / hết hạn / sai `uh` không hợp lệ; refund thứ `MAX+1` bị từ chối |
| Rust `cargo test` (Linux) | `verify_token` đúng với vector ký sẵn; `policy` hết hạn → coi như không có; `quota_grace` reset theo ngày UTC; `download_args` không đổi | chữ ký khoá lạ; `uh` lệch URL; `cid` dùng lại trong phiên |
| TS `node --test` (`desktop/tests/quota.test.ts` hiện 14 test) | `decideClaim` 426 → `update_required` (không phải offline); hint mặc định = bật; `selectionCap` khi `enforced` | 426 **không** cấp grace |
| Click-through Windows (bắt buộc trước S2 và trước S5) | (1) 0.9.0 với `DESKTOP_MIN_VERSION=0.9.1` giả → màn chặn, nút mở link tải, "Kiểm tra lại" sau khi hạ min → vào app; (2) khách tải 5 → lượt 6 bị chặn, chữ đúng, nút Đăng nhập → tải tiếp; (3) tài khoản tải 3 trên web + 17 app → 21 bị chặn; (4) 4 máy ảo cùng IP → máy 4 chạm `ip_limit`; (5) ngắt mạng → 3 grace → chặn với chữ ngoại tuyến; nối mạng → admin thấy retro; (6) admin "Cấp thêm 2" → tải tiếp 2; audit có dòng; (7) `MODE=shadow` trở lại → app hết gate trong ≤ 1 phút; (8) `ENABLED=false` → huy hiệu ẩn, tải không đếm; (9) kênh 30 video còn 7 lượt → chọn tối đa 7; (10) app 0.5.0 còn giữ: Douyin báo "cần cập nhật" (ghi lại đúng chữ hiện ra); (11) P3: hosts file trỏ API về máy giả trả `allowed:true` không ký → app từ chối `claim_required`; (12) P3: xoá `policy.bin` + chặn mạng → vẫn 3 grace rồi chặn |

### 7.4 Rollback từng đợt

| Đợt | Hoàn tác |
|---|---|
| P0 | chỉ thêm khoá thống kê/đọc; revert deploy. |
| P1 server | `CLIENT_QUOTA_MODE=shadow`; hạ `DESKTOP_MIN_VERSION`; grant/reset là Redis TTL ≤ 1 ngày. |
| P1 app 0.9.0 | cài lại 0.8.0 (bộ cài cũ giữ trên `/download/`); không đổi định dạng dữ liệu. |
| P3 | bỏ `CLIENT_QUOTA_SIGNING_KEY` → không `policy` → app bỏ đòi token; app 0.10 vẫn chạy với máy chủ không ký. |

## 8. Phân đợt, công sức, lịch, câu hỏi mở

Quy đổi AI Factory: 1 ngày dev tay ≈ 2 giờ AI. Mỗi đợt tự ship, cờ tắt = hành vi cũ.

| Đợt | Nội dung | Phụ thuộc | Dev tay | AI | Lịch (so với 14/10) |
|---|---|---|---|---|---|
| **P0 — Đo cho đúng** | phổ phiên bản app; tách khách/tài khoản + top máy theo V + R/T theo ngày trên stats & trang; **G2**; `DESKTOP_LATEST_VERSION=0.8.0` | — | 1,5 | 3 h | deploy **≤ 10/10** để có ≥ 3 ngày số trước cổng |
| **P1 — Chặn thật** | app 0.9.0 (màn cập nhật, hint fail-closed, chữ `ip_limit`); máy chủ 426 + `ENFORCE_USERS` + refund theo loại; admin grant/reset/ip-reset + audit; host bộ cài `/download/`; BA duyệt chữ | P0 | 3 | 6 h | app phát hành **13/10**; bật S1 **15/10** → S2 16–18/10 → S3 19–21/10 → S4 22/10; S5 ~27/10 |
| **P2 — Dấu hiệu bất thường** | 6 tín hiệu §5.3 + mục trên trang admin | P0 | 2 | 4 h | 20–24/10 (song song S3, đọc được trước S5) |
| **P3 — Chữ ký claim** | `signing.py`, `policy`, `token`; Rust verify + `ureq` + SQLite grace; `build.rs` khoá; app 0.10.0; tài liệu xoay khoá | P1 ổn định ≥ 1 tuần | 3 | 6 h | 28/10–05/11; chỉ làm nếu chủ chọn (Q5) |
| P4 — Tuỳ chọn | Tauri updater (sau ký mã); giới hạn số máy/gói trả phí; phương án B §5.2 | ký mã | 3–5 | 8 h | chưa lên lịch |

Tổng P0–P3: ~9,5 ngày dev tay / ~19 giờ AI, chưa tính BA duyệt chữ và click-through Windows (~0,5 ngày/đợt).

### 8.1 Câu hỏi mở cho chủ sản phẩm (chỉ những điều đổi cách làm)

1. **Canary theo tài khoản** (`CLIENT_QUOTA_ENFORCE_USERS`): có làm không? Không có thì S1 chỉ kiểm dây và S2 chạm mọi tài khoản cùng lúc.
2. **Trần hoàn lượt cho khách**: giữ 10 (G5: client nói dối được tới 15 lượt) hay tách `REFUND_DAILY_MAX_GUEST=2`, tài khoản giữ 10?
3. **App < 0.6.0** ở S5: chặn Douyin/lịch sử của họ bằng 426 kèm link cập nhật, hay để yên (tải cục bộ của họ vốn không chặn được)?
4. **Nơi đặt bộ cài**: `dvid.vibe1.tinhgon.xyz/download/` (mọi bản app mở được, cần chỗ chứa ~100 MB/bản) hay GitHub Releases (bản cũ chỉ sao chép link)?
5. **P3 chữ ký claim**: làm ngay sau P1 (3 ngày, chỉ chặn đường "giả máy chủ") hay chờ bằng chứng từ P2?
6. **Cấp thêm**: chỉ theo ngày (Redis, hết nửa đêm) hay thêm "miễn hạn mức cho máy/tài khoản này N ngày" (cần cột trong `desktop_devices`/`profiles` + migration)?

**Bước đầu khi duyệt**: tạo task AI Factory cho P0 (hạn 10/10), xin BA duyệt 5 chuỗi chữ (màn cập nhật, hết lượt khách, hết lượt tài khoản, trần IP, ngoại tuyến), và kiểm trên Vibe Host điểm **chưa kiểm** rẻ nhất: nginx frontend phục vụ được file ~100 MB không (việc đổi env cần redeploy đã kiểm 07/10).

## Quyết định của chủ sản phẩm (07-10-2026)

Chủ sản phẩm đồng ý cả 6 đề xuất của người duyệt:
1. **Có** canary theo tài khoản (`CLIENT_QUOTA_ENFORCE_USERS`) trước khi mở cho mọi tài khoản.
2. Trần hoàn lượt cho khách **2/ngày** (tài khoản giữ 10).
3. App < 0.6.0 ở S5: **có** — 426 `update_required` kèm link cập nhật cho các lời gọi cần máy chủ.
4. Bộ cài đặt ở **`dvid.vibe1.tinhgon.xyz/download/`** — kiểm trước nginx frontend phục vụ file ~100 MB.
5. Chữ ký claim (P3): **chờ** bằng chứng từ P2.
6. Cấp thêm lượt: **chỉ trong ngày** (Redis, hết lúc reset), không migration.

Bổ sung ngưỡng cổng 14/10 còn trống: **15–30 %** → bật theo thứ tự như 2–15 % nhưng S2 (tài khoản) kéo dài 5 ngày thay vì 3 và xem lại mức 5 khách sau S3.


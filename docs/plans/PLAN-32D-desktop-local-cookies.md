# PLAN 32D — App Windows tải bằng cookie của chính người dùng, mã máy, và tính lượt tải cục bộ vào hạn mức

Ngày: 2026-10-07 · Nhánh gốc: `feat/douyin-channel` @ `e1c69d8` · App 0.5.1 · Tài liệu này chỉ là KẾ HOẠCH, chưa sửa code.
Người đọc: chủ sản phẩm (quyết phạm vi / chi phí) và dev thực hiện. Mọi chỗ ghi **chưa kiểm** là chưa xác minh trong code hoặc trên máy thật.

## 0. Yêu cầu của chủ sản phẩm (07-10-2026) và các quyết định đã chốt

> "Ai dùng Windows cũng tải được nhiều kênh ngay trong app bằng cookie CỦA HỌ; khi cần hoặc khi lỗi thì vẫn rơi về hệ thống tải trên máy chủ của mình, nhưng vẫn bị giới hạn như tôi muốn. Nhận diện người dùng theo MÁY như VoxDub (Mã máy 8 ký tự, cài lại app / xoá config không mất, cài lại Windows thì đổi). Với các nền tảng hiện có, ưu tiên đường RẺ nhất chạy được trước, Apify chỉ khi đường kia hỏng."

| # | Quyết định đã chốt | Hệ quả kỹ thuật |
|---|---|---|
| Q1 | Tải cục bộ bằng cookie người dùng VẪN tính vào hạn mức chung: khách 5 / có tài khoản 20 lượt/ngày, gộp mọi nền tảng | App phải "xin lượt" trên máy chủ TRƯỚC khi chạy yt-dlp cục bộ (§5). Hôm nay app không tính gì cả. |
| Q2 | Áp cho TẤT CẢ nền tảng, không riêng Douyin | Bảng đường đi theo nền tảng (§2) + khung cookie dùng chung cho mọi domain (§3). |
| Q3 | Nhận diện theo máy kiểu VoxDub | Mã máy từ `MachineGuid` (§4), gửi header `X-VG-Device`, lưu bảng thiết bị. |
| Q4 | Rẻ nhất trước, Apify sau cùng | Thứ tự: cục bộ (có/không cookie) → máy chủ đường miễn phí → máy chủ Apify. |

## 1. Mục tiêu, ngoài phạm vi, chỉ số thành công

**Mục tiêu**
1. Người dùng Windows tải video/kênh của mọi nền tảng ngay trên máy họ, dùng cookie của họ khi nền tảng đòi đăng nhập (Douyin, Instagram, X, Facebook riêng tư, YouTube hạn chế tuổi, Bilibili 1080p…).
2. Lỗi ở máy → tự rơi về máy chủ (đường miễn phí trước, Apify sau), người dùng thấy rõ đang đi đường nào.
3. Mọi lượt tải trong app — cục bộ hay qua máy chủ — đều trừ vào cùng một hạn mức 5/20 (`backend/app/core/quotas.py:227-233`, `:250-255`).
4. Mỗi máy có Mã máy ổn định; admin thấy máy nào tải bao nhiêu, cục bộ hay máy chủ.

**Ngoài phạm vi (không làm trong 32D)**
- Không gửi cookie người dùng lên máy chủ dưới bất kỳ hình thức nào (kể cả "chỉ cho yêu cầu này").
- Không mua/chia sẻ cookie, không xoay vòng tài khoản (quy tắc 4 của `docs/china-access/PLAN-32B1.md`).
- Không thu phí mới, không đổi giá gói; chỉ hiện lời mời đăng ký / nâng cấp sẵn có.
- Không chống bẻ khoá tuyệt đối (xem §5.4, nói thẳng: client sửa được).
- Không làm extractor mới cho Kuaishou / Xiaohongshu / iQIYI; chúng giữ đường máy chủ hiện tại.

**Chỉ số thành công (đo sau 30 ngày kể từ khi bật cho khách)**

| Chỉ số | Mốc | Lấy số ở đâu |
|---|---|---|
| Tỷ lệ lượt tải trong app đi đường cục bộ | ≥ 70% | bộ đếm `vidgrab:stats:route:<local\|server>:<ngày>` mới (§7.1) |
| Chi Apify Douyin phát sinh từ app | giảm ≥ 80% so với tháng 9 (lấy mốc từ admin China access trước khi bắt đầu — **chưa có số**) | `admin_china_platforms` spend hiện có |
| Lượt tải app ≥ 0.6.0 không có claim | ≈ 0% (đo trong chế độ shadow) | log claim vs lịch sử đồng bộ `desktop_downloads` |
| Đăng nhập cookie thành công trong ≤ 3 lần thử | ≥ 90% mỗi nền tảng | sự kiện `cookies_login_result` gửi kèm `/client/quota` |
| Tài khoản người dùng bị khoá do app | 0 ca báo cáo | hỗ trợ |
| Khách bấm "Đăng ký" sau khi bị từ chối lượt | đo được (chưa đặt mốc) | `track_quota_denial` (`quotas.py:465-467`) + sự kiện mới |

## 2. Thứ tự đường đi theo nền tảng (rẻ nhất trước)

Ký hiệu: **L0** = yt-dlp cục bộ không cookie · **L1** = yt-dlp cục bộ + cookie của người dùng · **S0** = máy chủ đường miễn phí (`POST /api/v1/fetch-link`, `backend/app/api/routes.py:450`, dùng kho cookie chung `app/core/cookie_pool.py` và proxy của hệ thống) · **S$** = máy chủ Apify (`china_platforms`, ~$0,0071/video Douyin, $0,00405 Kuaishou, $0,00255 XHS — `china_platforms/settings.py:253-300`).

| Nền tảng (slug `platform_key.py:21-38`) | Thứ tự | Khi nào rơi xuống bước sau | Ghi chú / bằng chứng |
|---|---|---|---|
| YouTube | L0 → L1 → S0 | L0 lỗi `private_or_login` / "Sign in to confirm you're not a bot" → L1; L1 lỗi → S0 | S0 đi qua proxy DataImpulse trả tiền (`downloader.py:2653`), để cuối. **chưa kiểm** tỷ lệ L0 bị "not a bot" trên IP dân dụng VN. |
| TikTok | L0 → S0 | lỗi bất kỳ trừ `not_found` | Máy chủ đang 87,9% thành công; L0 thường chạy. Không có đường cookie (TikTok không cần). |
| Instagram | L1 → S0 | chưa có cookie → nhắc đăng nhập trong app trước; L1 lỗi → S0 | Máy chủ cũng đòi cookie (`downloader.py:1892-1901`). |
| Facebook | L0 → L1 → S0 | `private_or_login` → L1 | Video công khai thường tải được không cookie. |
| X / Twitter | L0 → L1 → S0 | `private_or_login` / NSFW → L1 | S0 dùng cookie kho + proxy (`downloader.py:1923-1930`). |
| Threads | L0 → S0 | lỗi → S0 | **chưa kiểm** yt-dlp 2026.08.19 (`desktop/binaries.lock.json:6`) đọc Threads công khai được không; máy chủ có scraper riêng. |
| Reddit / Pinterest / Vimeo / SoundCloud / LinkedIn | L0 → (L1 nếu đã có cookie) → S0 | lỗi → bước sau | Trên máy chủ gộp vào ô "other" / `linkedin`. SoundCloud Go+ cần cookie. |
| Bilibili | L0 → L1 → S0 | muốn > 720p hoặc `private_or_login` → L1 | Máy chủ chỉ native (`policy.py:126-128`). |
| Douyin | **L1 → S$** | chưa có cookie → nhắc "mở Douyin trong app và bấm play 1 video"; L1 lỗi `private_or_login`/`forbidden` → nhắc làm mới cookie 1 lần → S$ | Đường duy nhất không tốn tiền: yt-dlp `DouyinIE` + cookie tươi (ghi nhớ 18-08: mọi đường không cookie đã chết, proxy CN vô ích). Hôm nay app đi S$ thẳng (`desktop/src/lib/douyin.ts:1-4`, `queue.ts:112-127`). Đây là nơi tiết kiệm Apify nhiều nhất. |
| Kuaishou | S$ | — | Không có extractor yt-dlp, không có đường cục bộ (`policy.py:97`). Giữ nguyên. |
| Xiaohongshu | S0 (native + cookie kho) → S$ | như router hiện tại (`policy.py:112`) | Không có đường cục bộ; cookie người dùng không được gửi lên (Q ngoài phạm vi). |
| Youku | L0 → S0 | lỗi → S0 | Máy chủ chạy OK 05-10. |
| MangoTV | L0 → S0 | lỗi → S0 | Bẫy: format không có `height`; bộ chọn `bv*[height<=N]+ba/b[height<=N]` của app (`desktop/src/lib/channels.ts:47`) sẽ rỗng như trên máy chủ. Phải nối thêm `/b` ở cuối (P0, sửa 1 dòng). |
| iQIYI | S0 | — | yt-dlp cần PhantomJS; app chỉ có deno (`engine.rs:47-52`). **chưa kiểm** trên máy thật; VIP/DRM không tải được ở đâu cả. |
| Spotify | S0 | — | Không phải yt-dlp, máy chủ xử lý riêng. |

Quy tắc chung:
- Mỗi bước thử ĐÚNG MỘT lần/job (không vòng lặp — quy tắc 6 của PLAN-32B1). Douyin được thêm một lần làm mới cookie, giống cơ chế làm mới link 403 hiện có (`queue.ts:182-189`).
- Lỗi `not_found`, `unsupported`, `geo_blocked` KHÔNG rơi xuống máy chủ (máy chủ cũng không cứu được; tránh tốn Apify).
- Rơi xuống máy chủ cho CÙNG URL trong ngày không bị tính lượt lần hai: `record_platform_download` bỏ qua URL đã đếm (`quotas.py:489-496`). Nên claim cục bộ rồi fallback là "một lượt".
- Người dùng thấy trên thẻ hàng đợi một nhãn nhỏ: "Tải trên máy này" / "Tải bằng tài khoản của bạn" / "Qua máy chủ VidGrab" / "Qua máy chủ (dịch vụ ngoài)". Câu chữ do BA duyệt.

## 3. Lấy cookie của người dùng một cách an toàn

### 3.1 Ba cách, chọn một cách chính

| Cách | Ưu | Nhược | Vai trò |
|---|---|---|---|
| **A. Cửa sổ đăng nhập trong app** (Tauri `WebviewWindow` nhãn `login-<platform>`, mở đúng trang nền tảng; khi người dùng bấm "Xong", Rust đọc cookie của domain đó rồi đóng cửa sổ) | Không phụ thuộc trình duyệt ngoài; chỉ lấy cookie domain cho phép; làm được cả bước "bấm play" Douyin | Cần API đọc cookie của webview: Tauri 2.12.1 (`Cargo.lock:4176-4177`) có `Webview::cookies_for_url` — **chưa kiểm** chữ ký chính xác và việc WebView2 trả đủ cookie HttpOnly; nếu thiếu, dùng `ICoreWebView2CookieManager` qua crate `webview2-com` | **CHÍNH (P1)** |
| B. `--cookies-from-browser firefox` | Không cần đăng nhập lại | Chỉ Firefox: Chrome/Edge 127+ dùng App-Bound Encryption, công cụ ngoài không giải mã được (xác nhận qua tài liệu công khai: The Register 31-07-2024, The Hacker News 08-2024, yt-dlp issue #12040; **chưa kiểm trên máy thật**). Đa số người dùng VN dùng Chrome/Cốc Cốc. | Phụ (P3) |
| C. Nhập tệp `cookies.txt` (Netscape) | Dân kỹ thuật quen | Người thường không biết xuất; tệp rơi vãi trên đĩa | Phụ (P3), chỉ cho "Nâng cao" |

### 3.2 Thiết kế cách A

1. **Mở cửa sổ**: lệnh Rust mới `cookies_login_open { platform }`. Rust tra bảng domain cho phép (`cookies.rs`, ví dụ `douyin → [douyin.com, iesdouyin.com]`, `youtube → [youtube.com, google.com]`, `twitter → [x.com, twitter.com]`, `threads → [threads.net, threads.com, instagram.com]`…), mở `WebviewWindowBuilder::new(app, "login-<p>", WebviewUrl::External(<trang đăng nhập>))`. Cửa sổ này KHÔNG nằm trong `capabilities/default.json` (`"windows": ["main"]`) nên không gọi được lệnh nào của app. Trang Douyin: hướng dẫn "không cần đăng nhập, mở một video và bấm play" (cookie chữ ký `__ac_signature` sinh lúc play — ghi nhớ 18-08).
2. **Đọc cookie**: khi người dùng bấm "Xong" (nút trong cửa sổ chính) hoặc đóng cửa sổ, Rust gọi API cookie của webview cho từng domain cho phép, lọc đúng domain (suffix match), bỏ cookie domain khác, ghi thành chuỗi Netscape. Port lại `sanitize_netscape_cookies` (`backend/app/core/cookie_pool.py:170`) sang Rust để yt-dlp không bao giờ in dòng lỗi chứa cookie ra stderr (bài học `downloader.py:85-92`).
3. **Lưu**: mã hoá bằng Windows DPAPI `CryptProtectData` (phạm vi người dùng, `CRYPTPROTECT_UI_FORBIDDEN`, entropy = hằng app) → `%APPDATA%\xyz.tinhgon.vidgrab\cookies\<platform>.bin` (cùng thư mục `vidgrab.db`, `lib.rs:1057-1062`). Cần thêm feature `Win32_Security_Cryptography` vào `windows-sys` (`Cargo.toml` khối `[target.'cfg(windows)'.dependencies]`). Linux dev: tệp thường 0600 sau `#[cfg(not(windows))]`, chỉ để chạy test. Không dùng Credential Manager vì blob giới hạn 2560 byte (`auth.rs:7-11`); cookie YouTube/Facebook dài hơn.
4. **Dùng**: trước khi spawn yt-dlp (`lib.rs:662-665`, và tương tự `probe` `:315`, `channel_fetch` `:348`), nếu URL thuộc nền tảng có blob → giải mã ra `%LOCALAPPDATA%\xyz.tinhgon.vidgrab\tmp\ck-<jobId>.txt`, thêm `--cookies <path>` vào vector tham số (`engine.rs:115-180`, thêm tham số `cookies: Option<&Path>`); xoá tệp tạm ngay sau `child.wait()` (`lib.rs:692-697`). yt-dlp ghi lại jar lúc thoát → tuỳ chọn mã hoá lại để giữ phiên tươi (P1 nếu kịp).
5. **Không bao giờ rời máy**: cookie chỉ đi `blob → tệp tạm → yt-dlp`. `validate::download_header` tiếp tục từ chối header `Cookie` (`validate.rs:27-41`, test `:102`); `apiFetch` chỉ gửi JSON (`http.ts:7-32`); CSP `connect-src` chỉ cho `dvid-api` + Supabase (`tauri.conf.json`). Log `download://log` (`lib.rs:530`) lọc bỏ dòng chứa đường dẫn tệp cookie.
6. **Đăng xuất**: lệnh `cookies_clear { platform }` xoá blob + xoá dữ liệu duyệt của cửa sổ login (`clear_all_browsing_data` — **chưa kiểm** có trong Tauri 2.12).

### 3.3 Rủi ro cho tài khoản của người dùng và cách app đi chậm

| Rủi ro | Biện pháp |
|---|---|
| Nền tảng thấy tài khoản tải dồn dập → giới hạn tốc độ, yêu cầu xác minh, hiếm khi khoá | Khi có cookie: thêm `--sleep-requests 1 --sleep-interval 2 --max-sleep-interval 5` (chỉ ở nhánh có cookie trong `download_args`); `queue.ts pump()` giới hạn 1 job đồng thời cho mỗi nền tảng đang dùng cookie (hiện tối đa 4 chung, `settings.ts:25`); quét kênh có cookie tối đa 50 video/lần và chu kỳ ≥ 6 giờ; và trần tuyệt đối là 5/20 lượt/ngày của chính Q1. |
| Cookie hết hạn → lỗi lặp | Lỗi `private_or_login` khi ĐÃ có cookie → đánh dấu blob "nghi hết hạn", nhắc đăng nhập lại, không tự thử lại (bài học retry storm 05-10: lỗi cần cookie phải là USER_ACTION). |
| Người dùng không hiểu mình đang dùng tài khoản của mình | Hộp đồng ý lần đầu (câu chữ BA duyệt): "VidGrab sẽ dùng phiên đăng nhập <nền tảng> của bạn để tải video NGAY TRÊN MÁY NÀY. Cookie được mã hoá bằng Windows và không gửi lên máy chủ VidGrab. Việc tải tự động có thể trái điều khoản của <nền tảng>; bạn tự chịu trách nhiệm với tài khoản của mình. Có thể xoá bất kỳ lúc nào trong Cài đặt." |

## 4. Mã máy kiểu VoxDub

Tham chiếu VoxDub (`~/workspace/projects/voidmix/autodub/device_id.py`): SHA-256 của `MachineGuid | tên máy | kiến trúc`, hiện 8 ký tự đầu in hoa; máy không đọc được registry thì sinh số ngẫu nhiên một lần và ghi xuống `~/.voxdub_device_id`.

**Đề xuất cho VidGrab (`desktop/src-tauri/src/device.rs`)**

| Mục | Thiết kế |
|---|---|
| Nguồn | `HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid`, đọc với `KEY_WOW64_64KEY` (như VoxDub) qua `windows-sys` feature `Win32_System_Registry` (thêm mới). KHÔNG đưa tên máy vào hash (đổi tên PC không đổi mã — khác VoxDub một chút, chủ sản phẩm quyết ở §9). Không dùng MAC. |
| Công thức | `deviceHash = SHA-256("vidgrab-device-v1|" + MachineGuid)` → 64 hex (sha2 đã có trong `Cargo.toml`). Mã hiển thị = 8 hex đầu, in hoa: "Mã máy: 5750E6A1". Salt là hằng công khai, chỉ để máy chủ không bao giờ thấy GUID thô và mã không trùng với app khác. |
| Dự phòng | Registry không đọc được (máy ảo lạ, Linux dev): sinh 32 byte ngẫu nhiên MỘT LẦN, lưu `%APPDATA%\xyz.tinhgon.vidgrab\device.id`; đánh dấu `source: "fallback"` để admin biết mã này kém ổn định. |
| Ổn định | Cài lại app ✔ · xoá config/localStorage ✔ (khác `deviceId()` hiện tại nằm trong localStorage, `settings.ts:40-48`, mất khi gỡ app) · cài lại Windows ✘ (GUID mới) · máy khác ✘. Hai máy nhân bản từ một ảnh đĩa có thể trùng GUID → chung mã → chung hạn mức khách; chấp nhận (tài khoản đăng nhập vẫn tách riêng). **chưa kiểm**: trình gỡ NSIS của Tauri có xoá `%APPDATA%` khi người dùng tích "xoá dữ liệu" không — chỉ ảnh hưởng mã dự phòng. |
| Gửi lên máy chủ | Header `X-VG-Device: <64 hex>` + `X-VG-Client: <version>` trên MỌI lời gọi `/api/v1/client/*` và `/api/v1/fetch-link` từ app (`http.ts` thêm 2 header mặc định). Thân JSON `GET /client/quota` gửi thêm `displayName: "PC-213 (Windows 10.0.26200)"` (chỉ để admin/hỗ trợ đọc, không vào hash). Thay thế `deviceId` cũ trong `sync.ts:72`. |
| Hiển thị | Cài đặt → thẻ "Máy này": tên máy, Mã máy, nút sao chép, dòng "Mã gắn với máy, không gắn với tài khoản; cài lại app không mất; cài lại Windows sẽ đổi. Đọc mã này khi cần hỗ trợ." |
| Chuyển máy / hỗ trợ | Khác VoxDub: ở VidGrab mã máy chỉ xác định KHÁCH cho hạn mức theo ngày, không gắn tiền/credit → **không có gì để chuyển**. Hạn mức của tài khoản đi theo tài khoản. Mã dùng để: hỗ trợ tra cứu, admin nhìn bất thường, và (tương lai, ngoài phạm vi) giới hạn số máy của gói trả phí. |
| Riêng tư | Máy chủ chỉ lưu hash + tên máy do người dùng gửi; không GUID, không MAC, không serial. Ghi rõ trong chính sách. |

**Nói thẳng**: mã máy là tín hiệu MỀM. Người có kỹ năng sửa registry / chạy máy ảo sẽ có mã mới. Vì thế hạn mức khách kết hợp: `dev:<hash>` làm ô đếm chính **+** trần phụ theo IP (`ip:<ip>`, hệ số `CLIENT_QUOTA_IP_MULT=3` → tối đa 15 lượt khách/ngày từ một IP dù bao nhiêu mã máy). Văn phòng dùng chung NAT có thể chạm trần này — chủ sản phẩm quyết (§9).

## 5. Tính lượt tải cục bộ vào 5/20 (quyết định Q1)

### 5.1 Hợp đồng API mới (`backend/app/api/client_quota.py`, mount `/api/v1` như `client_api`, `main.py:414`)

| Lời gọi | Thân | Trả về |
|---|---|---|
| `POST /client/quota/claim` | `{ url, platform (gợi ý), route: "local"\|"local_cookie", clientVersion }` + header `X-VG-Device` | `200 { allowed: true, claimId, expiresAt (+2h), alreadyCounted, remaining, limit, usedToday, resetTimeVn, requester: "admin"\|"user"\|"device", mode: "enforce"\|"shadow" }` · `403` (tài khoản) / `429` (khách) `{ allowed:false, error_code:"quota_exceeded_daily", detail, remaining:0, resetTimeVn, upsell:"signin"\|"upgrade" }` · `503 { error_code:"client_quota_disabled" }` khi cờ tắt |
| `POST /client/quota/claim-batch` | `{ items: [{url, platform}] ≤ 100, route }` | từng mục `allowed/claimId/detail`, dùng `BatchAllowance` (`quotas.py:505-544`) — cho kênh (§6) |
| `POST /client/quota/settle` | `{ claimId, outcome: "completed"\|"failed"\|"cancelled", errorCode?, fallbackRoute? }` | `{ refunded: bool, remaining }` |
| `GET /client/quota` | — | `platform_usage_snapshot` (`quotas.py:547-573`) + `{ device: {code, firstSeen}, features: {...cờ §7.4} }` — app gọi lúc mở và sau mỗi settle để hiện "Hôm nay 3/20" |

Máy chủ tự tính `platform` từ URL (`platform_key`), giá trị app gửi chỉ là gợi ý. Giới hạn tốc độ `60/minute` như `client_api.py:195`.

### 5.2 Ngữ nghĩa đếm (tái dùng `quotas.py`)

- **Reserve-first**: `claim` = `check_platform_quota` rồi `record_platform_download` NGAY (`quotas.py:438-502`), không phải "kiểm rồi commit sau". Lý do: client bỏ qua bước settle thì chỉ thiệt chính họ (lượt đã trừ), không lách được. `alreadyCounted=true` khi URL đã đếm hôm nay (tải lại, đổi chất lượng, fallback) → không trừ thêm, đúng ngữ nghĩa web (`quotas.py:209-213`).
- **Hoàn lượt** khi `settle outcome≠completed`: `DECR` hai bộ đếm (sàn 0) + `SREM` dấu vân URL, CHỈ khi claim còn trong Redis (`vidgrab:claim:<id>`, TTL 48h, hash gồm requester, platform, fp, route, state), chưa settle, tuổi < 2h, và số lần hoàn hôm nay của requester < `CLIENT_QUOTA_REFUND_DAILY_MAX=10`. Máy chủ không kiểm được kết quả cục bộ, nên hoàn là "tin có giới hạn".
- **Danh tính**: mở rộng `QuotaRequester` (`quotas.py:293-333`) thêm `REQ_DEVICE` khoá `dev:<32 hex đầu>`; `from_key` nhận tiền tố `dev:`; `resolve_requester` (`:336-348`) nhận `device_id=` từ header hợp lệ (đúng 64 hex) — thứ tự admin session > tài khoản > thiết bị > IP. Khoá Redis giữ nguyên mẫu `vidgrab:quota:plat:<key>:<platform>:<ngày>` (`:374-379`).
- Tài khoản đăng nhập dùng khoá `user:<id>` như web → app và web **chung** 20 lượt. Khách: web đếm theo IP, app đếm theo máy → hai ô riêng (5 + 5); ghi nhận, không sửa trong 32D.
- Admin (`X-Admin-Token` phiên, `:275-290`) luôn `allowed`, không đếm — làm nhóm canary đầu tiên.
- `fetch-link` (`routes.py:548`, `:820`) nhận thêm `device_id` để fallback máy chủ của khách rơi vào cùng ô `dev:` thay vì ô IP.

### 5.3 Hành vi trong app (`desktop/src/lib/quota.ts`, nối vào `queue.ts start()` `:129-138`)

1. Trước `api.startDownload` (và trước `douyinVideo` — endpoint đó đã tự đếm, `client_douyin.py:164-165`, nên Douyin qua máy chủ KHÔNG claim riêng, tránh đếm đôi): gọi `claim`.
2. `allowed` → chạy; `download://done` → `settle`. `completed` thì thôi; `failed` có đường tiếp theo (§2) → chạy fallback, settle sau cùng một lần với `fallbackRoute`.
3. Bị từ chối (403/429) → thẻ sang `failed` với mã mới `api:quota_exceeded_daily` (đã có chữ trong `errors.ts:29`) và nút: khách → "Đăng nhập để có 20 lượt/ngày" (mở `SignInModal` hiện có); tài khoản → "Nâng cấp" (`open_url` tới trang nâng cấp, thêm vào `OPEN_URL_HOSTS` `validate.rs:51` nếu khác host) + giờ reset `reset_time_vn`.
4. `503 client_quota_disabled` → app chạy như hôm nay (không đếm). Đây là công tắc rollback.
5. **Offline / máy chủ 5xx / lỗi mạng**: cho tải nhưng ghi sổ cục bộ (bảng SQLite `quota_ledger` trong `vidgrab.db`), tối đa `offlineGrace=3` lượt/ngày UTC/máy (cờ máy chủ đẩy về qua `GET /client/quota`); khi có mạng lại, gửi `claim { retro: true }` cho từng mục để máy chủ đếm bù (được phép vượt trần vì đã tải). Chủ sản phẩm có thể đặt 0 (§9).
6. Hàng đợi tiếp tục thẻ đang `queued` sau khi bị từ chối? Không: dừng pump cho đến khi reset hoặc đăng nhập, tránh 20 thẻ cùng báo lỗi (một toast gộp).

### 5.4 Giới hạn thật của việc cưỡng chế, và biện pháp

| Cách lách | Khả thi? | Biện pháp / mức độ đáng làm |
|---|---|---|
| Tự chạy yt-dlp ngoài app | Luôn được, yt-dlp miễn phí | Không chống. Giá trị của app là tiện: hàng đợi, kênh, cookie trong app, fallback máy chủ. Hạn mức là công cụ ĐIỀU HƯỚNG (đăng ký / nâng cấp), không phải DRM. |
| Sửa bundle JS trong app để bỏ bước claim | Có, với người biết | P3: máy chủ ký claim bằng Ed25519; Rust `start_download` nhận `claimToken` và xác minh chữ ký + hạn + khớp URL bằng khoá công khai nhúng trước khi spawn (crate `ed25519-dalek`). Phải vá cả binary Rust mới lách được. Mức vừa, ~1 ngày. |
| Vá binary Rust | Có, hiếm | Không chống thêm. |
| Chặn mạng để vào "offline grace" | Có | Grace 3/ngày là trần; máy chủ ghi `retro` và admin thấy máy nào toàn lượt retro → xử lý tay / đặt grace 0. |
| Nhiều mã máy (sửa registry, máy ảo) | Có | Trần IP ×3 (§4) + bảng thiết bị: một IP tạo > N máy/ngày → cảnh báo admin (`anomaly_detector.py` sẵn có, **chưa kiểm** cách nối). |
| Khuyến khích thay vì chặn | — | Lời mời rõ ràng: khách 5 → tài khoản 20 ngay lập tức, miễn phí; gói trả phí hiện 100/500 (`quotas.py:258-272`). |

## 6. Tải kênh cục bộ bằng cookie (nhiều video)

- Quy tắc trần giống Douyin trên máy chủ (`channel_listing.py:126-144`: liệt kê tối đa = lượt còn lại trong ngày): app gọi `GET /client/quota` lấy `remaining_total`, cắt danh sách chọn tải ở `ChannelPicker` (đã có chữ cho Douyin, `ChannelPicker.tsx:163` → dùng chung cho mọi nền tảng), rồi `claim-batch` cho các video được chọn; mục bị từ chối hiện lý do từng dòng như `bulk_item_quota_message` (`quotas.py:424-435`).
- Liệt kê kênh (`channel_fetch`, `engine.rs:67-74`) có thể dùng cookie (`--cookies`) để thấy video riêng tư/thành viên; liệt kê KHÔNG tính lượt (như Douyin). `looksLikeChannelUrl` (`urls.ts`) hiện chỉ nhận YouTube/TikTok/Douyin → mở rộng dần (Bilibili space, Instagram profile, X user) sau khi kiểm từng extractor — **chưa kiểm**.
- Kiểm tra tự động nền (`channels.ts:191-221`, mỗi phút tick, lấy 50): với kênh dùng cookie, chu kỳ tối thiểu 6h và chế độ mặc định `notify` (không tự tải) để không đốt hết 20 lượt lúc 07:00; `isManualOnly` (`:37`) giữ Douyin thủ công như nay; `mode=download` chỉ tự tải khi còn ≥ 1 lượt (claim-batch trước, giống thủ công).
- Thông báo khay khi hết lượt giữa chừng: "Kênh X: còn 7 video chưa tải vì hết lượt hôm nay. Lượt mới lúc 07:00."

## 7. Thay đổi theo từng lớp

### 7.1 Máy chủ (FastAPI)

| Tệp | Việc |
|---|---|
| `backend/app/core/quotas.py` | `REQ_DEVICE`, `from_key("dev:")`, `resolve_requester(device_id=)`, `claim_platform_download()` (= check + record + ghi hash claim), `refund_platform_download()`, trần IP phụ, bộ đếm route `vidgrab:stats:route:<local\|server>:<ngày>` và `vidgrab:stats:refund:<key>:<ngày>`. |
| `backend/app/api/client_quota.py` (mới) | 4 route §5.1, cờ `CLIENT_QUOTA_ENABLED` + `CLIENT_QUOTA_MODE=shadow\|enforce` theo mẫu `client_api_enabled()` (`client_api.py:40-48`); shadow = đếm + log nhưng luôn `allowed`. |
| `backend/app/api/routes.py` | `fetch-link` đọc `X-VG-Device` → requester `dev:` cho khách từ app (`:548`, `:820`); thêm `X-VG-Client` vào log. |
| `backend/app/api/client_api.py` | `/client/version` trả thêm `features` (cờ §7.4) để app không cần gọi thêm. |
| `backend/app/api/admin.py` (+ trang admin) | `GET /admin/desktop/devices?user=&code=` (mã, tên máy, user, lần đầu/cuối, IP cuối, lượt hôm nay cục bộ/máy chủ, retro, hoàn), `GET /admin/desktop/stats?days=7` (cục bộ vs máy chủ vs Apify theo ngày). |
| `database/migrations/037_desktop_devices.sql` (mới, theo mẫu `034_desktop_downloads.sql`) | bảng `desktop_devices(device_hash TEXT PK, user_id TEXT NULL, display_name, os, client_version, source, first_seen, last_seen, last_ip)`; RLS service_role như 034. Claim KHÔNG vào Postgres (Redis TTL 48h là đủ); lịch sử tải đã có `desktop_downloads.device_id` (`034:11`) → đổi sang ghi hash mới. Thiếu bảng → 503 `storage_not_ready` như `client_api.py:97-101`. |

### 7.2 App (Rust, `desktop/src-tauri`)

| Tệp | Việc |
|---|---|
| `src/device.rs` (mới) | đọc MachineGuid, hash, mã dự phòng, `device_info` command → `{ hash, code, displayName, source }`. |
| `src/cookies.rs` (mới) | bảng domain theo nền tảng, lọc + Netscape + sanitize, DPAPI lưu/đọc, tệp tạm theo job, xoá. |
| `src/login_window.rs` (mới) | mở/đóng cửa sổ `login-<platform>`, đọc cookie, trả `{ platform, cookieCount, expiresHint }`. |
| `src/engine.rs` | `download_args/probe_args/channel_fetch_args` thêm `cookies: Option<&Path>` → `--cookies`; cờ sleep chỉ khi có cookie; `qualityFormat` phía TS nối `/b`. |
| `src/lib.rs` | lệnh mới `device_info`, `cookies_login_open`, `cookies_login_finish`, `cookies_status`, `cookies_clear`, (P3) `cookies_from_firefox`, `cookies_import_file` (hộp thoại chọn tệp từ Rust như `pick_folder`), `start_download` thêm `useCookies: bool` (+ P3 `claimToken`); lọc log; xoá tệp tạm sau wait. |
| `src/validate.rs` | `platform_slug()` allowlist cho tham số nền tảng; `OPEN_URL_HOSTS` thêm trang nâng cấp nếu cần. |
| `capabilities/default.json`, `Cargo.toml`, `docs/desktop/C1-CONTRACT.md` §6 | khai báo lệnh mới; `windows-sys` + `Win32_System_Registry`, `Win32_Security_Cryptography`; (P3) `ed25519-dalek`. |

### 7.3 App (TS, `desktop/src`)

| Tệp | Việc |
|---|---|
| `lib/device.ts` (mới) | gọi `device_info` một lần, cache; thay `deviceId()` ở `sync.ts:72`. |
| `lib/http.ts` | thêm header `X-VG-Device`, `X-VG-Client` mặc định. |
| `lib/quota.ts` (mới) | claim / claim-batch / settle / snapshot, store `quota` cho UI, sổ offline (SQLite qua lệnh Rust `ledger_*` hoặc localStorage — chọn SQLite để không mất khi xoá cache), xử lý 503. |
| `lib/routes.ts` (mới) | `platformOf(url)` (mirror `platform_key.py`), bảng §2, máy trạng thái fallback, chỉ 1 lần/bước. |
| `lib/cookies.ts` (mới) | trạng thái cookie theo nền tảng, mở cửa sổ login, đồng ý lần đầu. |
| `lib/queue.ts` | `start()` → claim → startDownload (useCookies) → done → settle/fallback; pump giới hạn 1 job/nền tảng có cookie; dừng pump khi hết lượt. |
| `lib/channels.ts` | claim-batch, cắt theo `remaining`, chu kỳ ≥ 6h cho kênh cookie. |
| `screens/SettingsScreen.tsx` | thẻ "Máy này"; mục "Tài khoản nền tảng" (bảng nền tảng × trạng thái × nút). |
| `screens/DownloadScreen.tsx`, `QueueScreen.tsx`, `components/ChannelPicker.tsx` | huy hiệu "Hôm nay 3/20", nhãn đường đi, nút đăng nhập/nâng cấp khi bị từ chối. |
| `lib/errors.ts` | mã mới `cookie_required`, `cookie_expired`, `quota_offline_limit`; chữ do BA duyệt. |
| `lib/tauri.mock.ts` | mock 4 route quota + lệnh Rust mới để chạy UI ngoài app. |

### 7.4 Cờ tính năng và thứ tự bật

| Cờ (máy chủ, đọc lúc gọi) | Mặc định | Ý nghĩa |
|---|---|---|
| `CLIENT_QUOTA_ENABLED` | off | off → 503 → app không đếm (hành vi hôm nay). |
| `CLIENT_QUOTA_MODE` | `shadow` | shadow: đếm + log, luôn cho; `enforce`: từ chối thật. |
| `CLIENT_QUOTA_ENFORCE_FOR` | `admin` | danh sách requester bị enforce: `admin,user,device` → bật dần. |
| `CLIENT_QUOTA_OFFLINE_GRACE` / `_REFUND_DAILY_MAX` / `_IP_MULT` | 3 / 10 / 3 | §5. |
| `CLIENT_COOKIES_PLATFORMS` | `` (rỗng) | nền tảng app được dùng cookie, ví dụ `douyin,instagram,facebook,twitter,youtube,bilibili`; app đọc qua `features`. |
| `CLIENT_SERVER_FALLBACK_PLATFORMS` | `douyin` | nền tảng được rơi về máy chủ từ app. |
| `DESKTOP_MIN_VERSION` (đã có, `client_api.py:229-233`) | — | nâng lên 0.6.0 khi enforce cho khách, vì app cũ không claim gì cả. |

Thứ tự: (1) deploy máy chủ cờ off → (2) phát hành app 0.6.0 (P0) → (3) `ENABLED=on, MODE=shadow` 7 ngày, đọc số → (4) `enforce` cho `admin` → `user` → `device` (mỗi bước ≥ 3 ngày) → (5) bật `CLIENT_COOKIES_PLATFORMS` từng nền tảng (Douyin trước) → (6) `DESKTOP_MIN_VERSION=0.6.0`.
Rollback: tắt `CLIENT_QUOTA_ENABLED` (app về hành vi cũ trong ≤ 1 lượt gọi); rút nền tảng khỏi `CLIENT_COOKIES_PLATFORMS` (app ngừng dùng cookie, không xoá blob); migration 037 chỉ thêm bảng, không cần hoàn.

## 8. Rà soát bảo mật & riêng tư

| Mục | Kết luận |
|---|---|
| Cookie rời máy? | Không. Đường duy nhất: blob DPAPI → tệp tạm → yt-dlp (tiến trình con trong Job Object, `proc.rs`). Không header, không JSON, không log. Test Rust khẳng định `--cookies` chỉ xuất hiện khi được cấp và `validate::download_header("Cookie")` vẫn `Err` (`validate.rs:102`). |
| Khoá mã hoá | DPAPI người dùng Windows: tài khoản Windows khác / máy khác không giải mã được blob; sao chép blob sang máy khác vô dụng. Malware chạy cùng tài khoản thì đọc được — giống mọi trình duyệt, nêu rõ trong chính sách. |
| Tệp tạm | Trong `%LOCALAPPDATA%` của người dùng, tên theo jobId, xoá sau `wait()`; app khởi động quét xoá `tmp\ck-*.txt` còn sót (crash). |
| Log | `download://log` và `stderr_tail` (`lib.rs:734`) lọc dòng chứa `ck-<jobId>` hoặc "cookie"; sanitize Netscape trước khi đưa yt-dlp. Phía máy chủ không bao giờ nhận cookie nên không có gì để lọc. |
| Cửa sổ login | Nhãn riêng, không có quyền Tauri (`capabilities/default.json` chỉ `main`), URL khởi đầu chỉ từ bảng domain Rust, không nhận URL từ webview. Người dùng gõ gì trong trang nền tảng là chuyện giữa họ và nền tảng. |
| Mã máy | Hash có salt; máy chủ không có GUID. Không thể suy ngược. Tên máy do người dùng gửi (có thể chứa tên thật) → chỉ admin thấy, không hiện công khai. |
| Điều khoản nền tảng (cảnh báo, không kết luận pháp lý) | Tải tự động bằng tài khoản cá nhân có thể trái ToS của YouTube, Instagram/Facebook/Threads (Meta), X, Douyin/TikTok (ByteDance), Bilibili. Rủi ro đổ lên tài khoản người dùng, không lên hạ tầng VidGrab. Hộp đồng ý §3.3 bắt buộc; mục "Dịch vụ của bên thứ ba" trong điều khoản VidGrab cần BA/pháp lý rà. Suno đã bị loại vì ToS cấm (ghi nhớ) — không thêm. |
| Chống lạm dụng máy chủ | Claim có rate limit; trần IP; admin nhìn thiết bị; hoàn lượt có trần ngày. Reserve-first nên không có cửa "claim rồi không trả". |

## 9. Kiểm thử và câu hỏi mở cho chủ sản phẩm

### 9.1 Kiểm thử

| Lớp | Nội dung |
|---|---|
| Rust (`cargo test`, chạy được trên Linux trừ phần `#[cfg(windows)]`) | hash máy tất định với GUID giả; mã dự phòng sinh một lần; lọc domain cookie (không lọt `.evil.com`, đúng suffix); Netscape + sanitize; `download_args` có `--cookies`/sleep chỉ khi cấp; URL vẫn cuối sau `--` (mở rộng test `engine.rs:194-220`); lọc log; DPAPI roundtrip (Windows). |
| TS (`npm test`, mẫu `desktop/tests/urls.test.ts`) | `platformOf()` khớp bảng máy chủ; máy trạng thái route (mỗi bước 1 lần, lỗi không-fallback); quota: claim→start→settle, từ chối→upsell, 503→legacy, offline→sổ→retro; cắt danh sách kênh theo `remaining`. |
| Backend (`pytest`, một lượt một lúc) | claim idempotent theo URL/ngày; refund trần + tuổi; `dev:` requester, trần IP; shadow luôn cho; cờ off → 503; admin không đếm; `fetch-link` cùng URL sau claim không đếm đôi; migration thiếu → `storage_not_ready`. |
| Mock | `tauri.mock.ts` cho UI; backend mock cho app (`MSW` hoặc json server) — chạy UI 3 kịch bản: còn lượt / hết lượt khách / hết lượt tài khoản. |
| **Click-through trên Windows thật** (bắt buộc trước mỗi cột mốc, bài học "click-through finds what tests cannot") | (1) mã máy giống nhau sau gỡ/cài lại; (2) mã đổi trên máy ảo khác; (3) đăng nhập Douyin trong app, play 1 video, tải 1 video cục bộ thành công, admin thấy route=local, Apify không tăng; (4) cookie Instagram → tải reel riêng tư; (5) Chrome/Edge đang mở không ảnh hưởng; (6) xoá cookie → tải lại báo `cookie_required`; (7) khách tải 5 lượt → lượt 6 bị chặn với nút đăng nhập; đăng nhập → tải tiếp; (8) ngắt mạng → 3 lượt rồi chặn; nối mạng → admin thấy retro; (9) kênh 30 video với 7 lượt còn → đúng 7 tải, 23 báo hết lượt; (10) tắt `CLIENT_QUOTA_ENABLED` → app tải không đếm; (11) tệp tạm cookie không còn sau tải; log không có cookie; (12) MangoTV preset 720p tải được. |

### 9.2 Câu hỏi mở cho chủ sản phẩm

> **Đã chốt 07-10-2026 (chủ sản phẩm):** 1. Có offline grace 3 lượt/ngày · 2. Đồng ý khách theo máy + trần IP ×3 · 3. Mã máy KHÔNG gộp tên PC · 4. Hoàn lượt khi lỗi, tối đa 10/ngày · 5. Đồng ý shadow 7 ngày rồi ép `DESKTOP_MIN_VERSION=0.6.0` · 6. Kuaishou/Xiaohongshu giữ đường máy chủ (Apify) · 7. "OK cho tất cả": dùng cookie cho mọi nền tảng đọc được bằng yt-dlp + cookie — bật lần lượt qua `CLIENT_COOKIES_PLATFORMS`, Douyin trước, sau đó Instagram, Facebook, X, YouTube, Bilibili, Threads, Reddit, Pinterest… · 8. Giới hạn số máy cho gói trả phí: để sau · 9. Firefox / cookies.txt: đợt P3 · 10. BA duyệt câu chữ trước khi code UI.


1. **Offline grace** 3 lượt/ngày hay 0 (chặt hơn, nhưng mất mạng chốc lát là không tải được)?
2. **Khách theo máy + trần IP ×3**: chấp nhận văn phòng chung NAT có thể bị chạm trần 15/ngày?
3. **Mã máy có gộp tên PC như VoxDub không?** Đề xuất KHÔNG (đổi tên máy không đổi mã).
4. **Hoàn lượt khi tải lỗi** (trần 10/ngày) hay không hoàn (đơn giản, chặt nhất)?
5. **Shadow 7 ngày** trước khi enforce, và chấp nhận ép nâng cấp app cũ (`DESKTOP_MIN_VERSION`) vì app < 0.6.0 không đếm gì?
6. **Xiaohongshu / Kuaishou** giữ Apify-only (không có đường cục bộ) — chấp nhận chi phí, hay hoãn?
7. **Nền tảng cookie đợt đầu**: đề xuất Douyin, Instagram, Facebook, X, YouTube, Bilibili. Có thêm Threads/Reddit/Pinterest không?
8. **Giới hạn số máy cho gói trả phí** (ví dụ Pro ≤ 3 máy) — làm luôn hay để sau? (ngoài phạm vi hiện tại)
9. **Firefox / nhập cookies.txt** đưa vào P1 hay để P3?
10. **Câu chữ** hộp đồng ý, nhãn đường đi, lời mời nâng cấp — BA duyệt trước khi code UI (theo quy ước BA ⇄ DEV).

## 10. Phân đợt, công sức, phụ thuộc

Quy đổi: 1 ngày dev tay ≈ 2 giờ AI (chuẩn AI Factory). Mỗi đợt tự ship được, không đợt nào phá hành vi cũ khi cờ tắt.

| Đợt | Nội dung | Phụ thuộc | Dev tay | AI-assisted | Thứ tự hạn |
|---|---|---|---|---|---|
| **P0 — Nền móng đếm lượt** | Mã máy (Rust + header) · `client_quota.py` claim/settle/snapshot + cờ shadow/enforce · `REQ_DEVICE` · migration 037 · `quota.ts` nối `queue.ts` · sổ offline · huy hiệu "Hôm nay x/y" + nút đăng nhập/nâng cấp · `fetch-link` nhận device · thẻ "Máy này" · sửa `/b` MangoTV · app 0.6.0 | — | 5 ngày | ~10 giờ | Tuần 1 (deploy máy chủ cờ off + phát hành app) |
| **P1 — Cookie trong app** | `cookies.rs` DPAPI + Netscape + sanitize · cửa sổ login (xác minh API cookie Tauri 2.12 NGAY ngày đầu; nếu thiếu, `webview2-com`) · `--cookies` + sleep trong engine · Settings "Tài khoản nền tảng" + đồng ý · Douyin L1 → S$ fallback (route đầu tiên có fallback) · lọc log · `CLIENT_COOKIES_PLATFORMS` | P0 (đếm lượt phải có trước khi mở tải cục bộ Douyin) | 6 ngày | ~12 giờ | Tuần 2–3 |
| **P2 — Bảng đường đi đủ nền tảng** | `routes.ts` máy trạng thái · fallback S0 qua `fetch-link` cho nền tảng bảng §2 (xác minh hợp đồng trả về của `fetch-link` cho app — **chưa kiểm** là link trực tiếp hay job + poll) · nhãn đường đi trên thẻ · `CLIENT_SERVER_FALLBACK_PLATFORMS` · kênh: claim-batch + cắt theo remaining + chu kỳ ≥ 6h | P0, P1 | 4 ngày | ~8 giờ | Tuần 3–4 |
| **P3 — Admin, cứng hoá, nhập cookie** | trang admin thiết bị + thống kê local/server/Apify · cảnh báo nhiều máy/IP · claim ký Ed25519 + xác minh trong Rust · Firefox import + cookies.txt · mã hoá lại jar sau tải | P0–P2 | 4 ngày | ~8 giờ | Tuần 5 |
| **P4 — Tuỳ chọn** | `/client/resolve` chung (link trực tiếp thay vì tải qua máy chủ, theo khung đã phác ở `desktop/src/types/api.ts:17-40`) cho nền tảng CDN không khoá IP · mở rộng `looksLikeChannelUrl` · iQIYI/PhantomJS | P2 | 3–5 ngày | ~8 giờ | Sau khi có số 30 ngày |

Tổng P0–P3: ~19 ngày dev tay / ~38 giờ AI-assisted, chưa kể thời gian BA duyệt câu chữ và click-through Windows (~0,5 ngày mỗi đợt).

**Bước đầu tiên khi được duyệt**: tạo task AI Factory cho P0, đo mốc Apify Douyin từ admin (số tháng 9), và xác minh trên máy Windows thật hai điểm "chưa kiểm" rẻ nhất: API cookie của Tauri 2.12 và `--cookies-from-browser firefox`.

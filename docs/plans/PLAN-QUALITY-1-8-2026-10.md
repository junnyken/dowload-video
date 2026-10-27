# Kế hoạch nâng chất lượng đầu ra VidGrab — 8 hạng mục (10/2026)

Ngày lập: 27/10/2026. Mã đối chiếu: `main` @ `3a1aa99`. Đầu vào: `docs/QUALITY-UPGRADE-2026-10.md` (mọi `tệp:dòng` dưới đây đã đọc lại trực tiếp trong mã, không chép từ tài liệu đó), `docs/PLAN-2026-Q4.md`, `docs/PRODUCT-STATUS.md`, khung mini-spec v2 (`MINI_SPEC_PLAYBOOK.md`).

## 0. Những điều đúng chung cho cả 8 mục

**Môi trường kiểm thử**
- Backend: `cd backend && venv/bin/python -m pytest tests/<tệp> -q` — **chỉ chạy một phiên pytest tại một thời điểm**. `tests/conftest.py` nạp `app.main` thật trước mọi test.
- App Windows: `cd desktop && npm test` (node `--test` trên `tests/*.test.ts`) và `cd desktop/src-tauri && cargo test --lib`.
- Tiện ích: `node chrome-extension/tests/<tệp>.js` từng tệp (không có package.json).
- Web: `cd frontend && npm run build && npm run lint` (không có bộ test đơn vị; `frontend/tests/*.py` là test trình duyệt).
- **Workspace Linux này không có `ffmpeg`/`ffprobe` trên PATH** (đã kiểm `which`, `find`). Mọi test đo bằng ffprobe phải đánh dấu `pytest.mark.skipif(shutil.which("ffprobe") is None)` và chạy thật ở: ảnh Docker backend (`backend/Dockerfile:44` cài ffmpeg Debian), hoặc máy Windows có app (sidecar ffprobe, `desktop/src-tauri/src/lib.rs:64`). Khi cần chạy tại đây, tải một bản ffmpeg tĩnh vào thư mục scratchpad và thêm vào PATH của phiên test — không cài vào hệ thống.
- Mạng từ workspace: TikTok chặn IP; trang Douyin làm trình duyệt workspace sập; YouTube "nhiều khả năng" mở được (chưa đo). Mọi "live verification" cho TikTok/Douyin phải làm trên máy Windows của chủ sản phẩm hoặc qua API production.
- Fixture video: dùng `testsrc2` của ffmpeg (tất định). **Không dùng `gradients`** (không tất định — `docs/AI-HANDOFF.md:236`). Mọi khẳng định là codec / kích thước / fps / thời lượng / thẻ, **không bao giờ so pixel**.
- yt-dlp: venv có `2026.04.30`; `backend/requirements.txt:14` ghim `yt-dlp>=2026.10.21` cho ảnh máy chủ. Test chuỗi chọn định dạng phải chạy lại với bản ghim trước khi kết luận.

**Kênh phát hành**
- Backend + web: Vibe Host, hai project riêng (API và FE), build từ `github/main`, deploy bằng MCP `vays` `redeploy_project`, lùi bằng `rollback_project` (`docs/AI-HANDOFF.md:30,59`, `docs/release/SOFT-LAUNCH-2026-10.md` §"Lệnh lùi bản").
- App Windows: hiện `0.20.2`. Tăng phiên bản ở `desktop/package.json`, `desktop/src-tauri/tauri.conf.json`, `desktop/src-tauri/Cargo.toml` (+`Cargo.lock`), build, tạo GitHub Release `v<version>` với `.exe` + `.sig` (`docs/desktop/UPDATER.md` §4). Công tắc khẩn cấp trong admin tắt tự cập nhật.
- Tiện ích Chrome: hiện `5.22.4` (`chrome-extension/manifest.json:6`); đóng gói bằng `scripts/package-extension.py`, phát tại `/extension` (cài tay, không lên Web Store).

**Giao với kế hoạch Q4**
- Q4 P1.2 "hạ chất lượng tối đa của free": mục #1 **không đổi chính sách hạng**; chỉ sửa bộ chọn để "chọn N thì ra N". Quyền được tới 2K/4K vẫn do `FREE_MAX_HEIGHT` (`backend/app/core/quotas.py:49`), `GUEST_MAX_HEIGHT`, `YT_MAX_HEIGHT_USER/ANON` (`backend/app/api/routes.py:805-836`) quyết định.
- Q4 "không thêm AI mới": #7 chỉ nối job dịch có sẵn (`backend/app/api/transcript_translate.py`), không thêm mô hình.
- Q4 P0.3 (`download_failed`): dùng làm tiêu chí "không tăng lỗi" cho #1 và #4.

**Nguyên tắc chung (guardrail mọi mục)**
1. Không viết lại luồng tải; chỉ đổi chuỗi chọn, thêm postprocessor, thêm trường trả về.
2. Không bao giờ hiển thị con số chưa đo: thiếu số đo → không hiện (không hiện số đã yêu cầu thay cho số thật).
3. Không mở thêm quyền hạng (Free/khách) qua cửa sau; mọi giới hạn ở `routes.py:746-836` và `quotas.py` giữ nguyên.
4. Không đổi tên file lưu trên đĩa máy chủ (`downloader.py:521-534`, token HMAC).
5. Mỗi mục có test đơn vị chạy được không cần mạng; test cần ffprobe thì skip rõ ràng, không giả xanh.

---

## MS-1 — YouTube: chọn 1440p/2160p/4K thì ra đúng độ nét đó

**Context**
- Bộ chọn `_get_base_opts` (`backend/app/services/downloader.py:1039`): `video_4k` 1058-1069 đặt `bestvideo[vcodec^=avc1]+bestaudio[acodec^=mp4a]` lên đầu; `video_<N>` 1070-1116 cũng H.264 trước ở cả nhánh dọc (1091) lẫn ngang (1097); mặc định `video` 1124-1161 chặn 1080 (đúng, giữ nguyên). YouTube không có H.264 trên 1080p (mã tự ghi, 1644), nên mọi mức >1080 rơi về 1080p H.264.
- YouTube dùng `format_sort = ["res","ext:mp4:m4a","tbr",...]` (1304): trong một nhánh, độ nét cao nhất thắng.
- Client YouTube chính là `visionos` (967), đã đo trả đủ thang tới 4K; `android_vr` chỉ là dự phòng (990).
- **Hai chốt trước bộ chọn** làm "sửa selector mà không thấy gì": (a) `routes.py:805-822` ép `video_2160`/`video_4k` → `video_<YT_MAX_HEIGHT_USER>` (mặc định 1080) cho người dùng không phải admin; khách 720 (`YT_MAX_HEIGHT_ANON`). (b) Dự phòng Cobalt khi yt-dlp bị chặn (`downloader.py:3536`) gọi `fetch_cobalt_stream(video_quality="2160")` nhưng không truyền codec → `youtube_codec="h264"` mặc định (`backend/app/services/cobalt_service.py:232`) → Cobalt cũng chỉ trả tới 1080p.
- Trả về đã có `downloaded_vcodec`, `downloaded_universal` (`downloader.py:3973-3981`; `routes.py:1073-1075`); web đã có logic `downloadedUniversal` (`frontend/src/components/DashboardContent.jsx:1745-1749`) và cờ `universal` từng dòng (`downloader.py:1645`).
- Cổng proxy YouTube không còn chặn 4K (`backend/app/core/youtube_gate.py:125-139`); ghi chú cũ "(4K never)" ở `downloader.py:1233` đã lỗi thời.

**Goal.** Người có quyền chọn N>1080 trên YouTube nhận file có cạnh ngắn ≥ 95% N (khi nguồn có), kèm nhãn codec thật và lời dặn mở file.

**Constraints**
1. Mức ≤1080 và `video` giữ nguyên từng ký tự chuỗi chọn (H.264 + AAC "mở mọi máy").
2. Quy tắc cạnh ngắn cho video dọc giữ nguyên (đã đo 08/10).
3. Không chuyển mã (re-encode) mặc định; chỉ ghép (remux). Chuyển mã 4K trên CPU máy chủ là phút và tiền — để Scope C.
4. Không đổi `merge_output_format` toàn cục; container vẫn `.mp4`.
5. Không mở 4K cho hạng/khách không có quyền; không sửa `routes.py:746-836` ngoài việc đọc env.
6. TikTok/Instagram/X/Facebook (chỉ H.264) không đổi kết quả.
7. Thiếu số đo → UI không nói "4K".

**Scope**
- A (domain): thứ tự codec cho N>1080: **VP9 trước AV1** (VP9 giải mã phần cứng phổ biến hơn; AV1 cần máy mới) rồi mới "bất kỳ"; âm thanh ưu tiên AAC (`mp4a`) để phần tiếng vẫn mở mọi nơi, Opus là dự phòng. Container `.mp4` cho cả VP9/AV1 (ffmpeg ghép được, không nén lại). Quy tắc này dùng lại ở MS-8 (app).
- B (engine): trong `_get_base_opts`, **chỉ khi N>1080** (và `video_4k`), chèn TRƯỚC nhánh H.264 hiện có:
  - dọc: `bestvideo[width<=N][height>N][width>1080][vcodec~='^vp0?9']+bestaudio[acodec^=mp4a]` / `...[vcodec^=av01]+bestaudio[acodec^=mp4a]` / `bestvideo[width<=N][height>N][width>1080]+bestaudio`
  - ngang: `bestvideo[height<=N][height>1080][vcodec~='^vp0?9']+bestaudio[acodec^=mp4a]` / `...[vcodec^=av01]+...` / `bestvideo[height<=N][height>1080]+bestaudio`
  - `video_4k`: như trên với `[height>1080]` không chặn trên.
  Toàn bộ chuỗi cũ giữ nguyên phía sau làm dự phòng. Cobalt: ở `downloader.py:3536-3545` truyền `youtube_codec="vp9"` khi chiều cao yêu cầu >1080 (cobalt: `h264` ≤1080p, `vp9` 4K, `av1` 8K/HDR); `cobalt_video_quality` giữ nguyên.
  Sửa ghi chú lỗi thời `downloader.py:1233`.
- C (API): không thêm trường; `downloaded_universal=false` + `quality_note` (đã có, `routes.py:1077`) mang lời dặn codec khi file là VP9/AV1.
- D (UI web): một dòng ghi chú dưới kết quả khi `downloaded_universal === false`. **Wording (BA review):** "File này dùng mã VP9/AV1 để giữ độ nét 2K/4K. Mở tốt bằng VLC, Chrome/Edge, Windows 10/11 (nếu không mở được, cài 'VP9 Video Extensions' hoặc 'AV1 Video Extension' miễn phí từ Microsoft Store). QuickTime trên Mac/iPhone không mở được VP9; cần mở bằng QuickTime thì chọn 1080p." [SL: hệ quả Apple/Windows là kiến thức chung, cần kiểm trên máy thật trước khi ghi cố định]. Khoá `frontend/src/locales/vi/*.json` + `en`.
- E (tests): xem Test Plan.
- Không làm: HDR/fps selector; chuyển mã về H.264 (Scope C, Pro, đợt sau); đổi chính sách hạng.

**Audit Before Build**
1. Chạy fixture chọn định dạng (mẫu `backend/tests/test_format_selector_unknown_height.py:24-33`, hàm `_pick` dùng engine yt-dlp thật) với **bản yt-dlp ghim** (`>=2026.10.21`) trên danh sách kiểu YouTube: 137 avc1 1080, 248 vp9 1080, 271 vp9 1440, 313 vp9 2160, 401 av01 2160, 140 m4a, 251 opus, + bản Short dọc 1080x1920 / 2160x3840. Ghi kết quả hiện tại (dự kiến tất cả ra 137+140).
2. Đọc env production: `YT_MAX_HEIGHT_USER`, `YT_MAX_HEIGHT_ANON`, `FREE_MAX_HEIGHT`, `GUEST_MAX_HEIGHT` (MCP `vays` `list_env`). Nếu `YT_MAX_HEIGHT_USER` ≤ 1080 thì mục này **không đổi gì trên production** cho tới khi chủ sản phẩm nâng nó — ghi rõ vào quyết định.
3. Tải thật 3 video YouTube công khai (ngang 4K, Short dọc, một bản 60 fps) ở `video_1440`, `video_2160`, `video_4k` qua `POST /api/v1/fetch-link` (dev hoặc production, tài khoản admin để không bị ép mức) → ffprobe. Mục tiêu: xác nhận lỗi ngoài mô phỏng.
4. Xác nhận `test_yt_degraded_extraction.py:207` chỉ khẳng định chuỗi của `video` (HD) — không cần đổi.
5. Xem `test_cobalt_upgrade.py:43` để thêm khẳng định `youtube_codec`.

**Design Choice (một cách).** Chèn nhánh "đúng độ nét, VP9→AV1→bất kỳ, tiếng AAC" chỉ cho N>1080, đứng trước chuỗi cũ; không đụng `format_sort`, không đụng mức ≤1080. Lý do: thay đổi nhỏ nhất, test được bằng fixture có sẵn, dễ rút; đổi `format_sort` sang "res trước codec" sẽ lan sang mọi nền tảng và đổi cả nhãn `universal`. Cobalt sửa tại chỗ gọi, không đổi `fetch_cobalt_stream`.

**Test Plan**
- Unit (không mạng, chạy được tại workspace): tệp mới `backend/tests/test_yt_high_res_selector.py` dùng `_pick`: `video_1440`→`["271","140"]`, `video_2160`/`video_4k`→`["313","140"]` (vp9 thắng av01 cùng độ nét), `video_1080`/`video`→`["137","140"]` không đổi, Short dọc `video_2160`→ luồng 2160x3840 vp9, danh sách chỉ có H.264 ≤1080 → `video_2160` ra 1080 H.264 (dự phòng cũ vẫn chạy). Khẳng định chuỗi `video` và `video_1080` **bằng từng ký tự** chuỗi trước khi sửa.
- Regression: `test_yt_degraded_extraction.py`, `test_format_selector_unknown_height.py`, `test_probe_red_6332.py`, `test_cobalt_upgrade.py` (+ khẳng định `youtube_codec=="vp9"` khi `video_2160`).
- Integration: `TestClient` `POST /fetch-link` với `extract_video_info` giả → trả `downloaded_vcodec="vp09..."` → `downloaded_universal is False`, `quality_note` có.
- Live (cần máy có ffprobe + YouTube): 3 video ở bước audit; `ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,r_frame_rate -of json`; mở thử trên VLC, "Phim & TV" Windows, QuickTime (ghi kết quả thật vào plan, không suy đoán).

**Success Criteria**
1. Nguồn có 2160p → `video_2160` cho `min(width,height) ≥ 2052`, `codec_name ∈ {vp9, av1}`.
2. `video`, `video_1080` → H.264 1080p, chuỗi chọn không đổi ký tự.
3. Dọc 1080x1920 với `video_1080` vẫn 1080x1920.
4. File VP9/AV1 → `downloaded_universal=false` và web hiện ghi chú; file H.264 không hiện.
5. Cobalt fallback với `video_2160` gửi `youtubeVideoCodec=vp9`.
6. 7 ngày sau deploy: `download_failed` YouTube không tăng (Q4 P0.3).

**Tệp đổi:** `backend/app/services/downloader.py` (1058-1116, 1233, 3536-3545), `backend/tests/test_yt_high_res_selector.py` (mới), `backend/tests/test_cobalt_upgrade.py`, `frontend/src/components/DashboardContent.jsx` (~1745, ~2236), `frontend/src/locales/{vi,en}/…`. **Công sức:** S (backend) + S (web). **Rủi ro:** file 4K to hơn (băng thông proxy YouTube — `youtube_gate` không giới hạn); người dùng Mac mở không được nếu bỏ qua ghi chú; `YT_MAX_HEIGHT_USER` chưa nâng thì không thấy hiệu quả. **Kiểm khách quan:** ffprobe như trên. **Phát hành:** backend + web (đợt 1).

---

## MS-2 — Tên file giữ tiếng Việt có dấu (kể cả Đ/đ)

**Context**
- **Đã có** `content_disposition(filename)` (`backend/app/core/local_download.py:86-101`): lọc ký tự điều khiển, `filename="<ASCII, Đ→D đ→d>"` + `filename*=UTF-8''<percent>`. Đang dùng ở `download-local` (`routes.py:1797-1815`) và `transcript_asr.py:439`. Vì web tải file ghép qua `download-local?...&filename=<title>` (`DashboardContent.jsx:1078`), **đường tải chính đã đúng**.
- Còn bỏ dấu kiểu `NFKD` + `encode('ascii','ignore')` (mất Đ/đ) ở: `routes.py:1836` (download-thumbnail, header 1881), `2016` (trim → tên đưa vào `download_url`), `2146` (gif), `2602-2605` (proxy-download; header 2689 còn nhét **slug ASCII** vào `filename*`, dùng chuẩn mà sai), `backend/app/api/processing.py:92` (`_slugify`, mọi endpoint `/process/*`), `backend/app/api/flow_cleanup.py:640`. Không đụng: `workspace.py:42`, `admin_partners.py:54`, `utils/helpers.py:13` (slug URL, không phải tên file).
- Tiện ích gửi tên Unicode đã lọc (`chrome-extension/direct-download.js:120` `safeName`: thay `/\?%*:|"<>` và xuống dòng, cắt 120) vào `proxy-download?filename=` (`background.js:273`) → máy chủ bỏ dấu.
- App Windows: `named_template` (`desktop/src-tauri/src/engine.rs:130-158`): thay `<>:"/\|?*%` + ký tự điều khiển bằng khoảng trắng, gộp khoảng trắng, cắt 150 byte không cắt giữa ký tự, bỏ `.`/khoảng trắng cuối.

**Goal.** Mọi tên hiển thị khi tải qua máy chủ giữ nguyên tiếng Việt có dấu (kể cả Đ/đ), bản dự phòng ASCII đúng (Duong ve nha dep), và không bao giờ chứa ký tự hệ điều hành cấm.

**Constraints**
1. Không đổi tên lưu trên đĩa (`downloader.py:521-534`).
2. Không để `..`, `/`, `\`, ký tự điều khiển, `<>:"|?*` vào tên; cắt 150 byte như app.
3. Header luôn qua `content_disposition()`; không tự ghép chuỗi header ở route.
4. Giữ `slugify` của `helpers.py` cho URL.
5. Có công tắc env `FILENAME_ASCII_ONLY=1` để trở về bỏ dấu đúng (Đ→D) nếu phát sinh lỗi ở máy người dùng.

**Scope**
- A: hàm `display_name(title, fallback)` trong `local_download.py` (đặt cạnh `content_disposition`), quy tắc giống `engine.rs:139-157`; `content_disposition` giữ nguyên.
- B: thay 6 chỗ trên bằng `display_name` + `content_disposition`. `proxy_download`: bỏ slug, header = `content_disposition(f"{name}.{safe_ext}")`. trim/gif/processing: truyền tên hiển thị vào `download_url(...)` (đã đi qua `download-local` → đúng chuẩn).
- C: không đổi hợp đồng API (trường `filename` trả về giờ có dấu).
- D: không có UI.
- E: test như dưới.
- Không làm: mẫu tên ngày+tiêu đề cho web (có thể sau, giống `engine.rs:25-40`).

**Audit Before Build.** `grep -rn "NFKD" backend/app` (9 chỗ, phân loại như trên); đọc `local_download.download_url`/`file_fields` để chắc tên hiển thị chỉ đi qua `filename=`; đọc `chrome-extension/background.js:267-273, 326-347, 670-681` xác nhận tiện ích không cần đổi; kiểm bot Telegram có tự đặt tên không (`telegram-bot/bot.py`, grep `filename`).

**Design Choice.** Dùng lại `content_disposition()` có sẵn, thêm một `display_name()` chung; không viết bộ slug mới. Mặc định giữ dấu (chuẩn RFC 6266/5987, trình duyệt hiện đại đều đọc `filename*`); công tắc env để lùi. Không chọn "chỉ bỏ dấu đúng" vì xấu với người Việt và app đã giữ dấu — web/app sẽ lệch nhau.

**Test Plan**
- Unit (`backend/tests/test_display_name.py`, chạy tại workspace): bộ mẫu "Đường về nhà đẹp", "Tiếng Việt: 'ngoặc' ? * < > |", "😀 Video", 300 ký tự tiếng Việt (≤150 byte, không cắt giữa ký tự), "..", "a/b\\c", "", "CON" → tên mong đợi; `content_disposition("Đường về nhà đẹp.mp4")` == `attachment; filename="Duong ve nha dep.mp4"; filename*=UTF-8''%C4%90%C6%B0%E1%BB%9Dng%20v%E1%BB%81%20nh%C3%A0%20%C4%91%E1%BA%B9p.mp4`.
- Integration (`TestClient`): `GET /proxy-download` với `open_safe_stream` giả → header đúng; `GET /download-thumbnail`; `POST /trim` giả → `filename` có dấu.
- Regression: mọi test đang có của `local_download` và `routes` tải file.
- Live: tải bằng Chrome, Edge (Windows 11), Safari (macOS) qua web và qua tiện ích; ghi tên file thực nhận.

**Success Criteria.** (1) "Đường về nhà đẹp.mp4" nhận đúng tên trên Chrome/Edge/Safari; không bao giờ "uong-ve-nha-ep". (2) Không có `NFKD`+`ascii ignore` nào còn lại cho tên file (`grep` = 3 chỗ slug URL). (3) Test mẫu xanh. (4) `FILENAME_ASCII_ONLY=1` cho "Duong ve nha dep.mp4".

**Tệp đổi:** `backend/app/core/local_download.py`, `backend/app/api/routes.py` (1836-1882, 2016-2028, 2146-2158, 2602-2690), `backend/app/api/processing.py:90-94`, `backend/app/api/flow_cleanup.py:640`, `backend/tests/test_display_name.py` (mới). **Công sức:** S. **Rủi ro:** thấp; vài trình giải nén/ổ FAT cũ với tên có dấu [SL] → công tắc env. **Kiểm khách quan:** so header và tên file nhận được. **Phát hành:** backend (đợt 1).

---

## MS-3 — Báo đúng chất lượng thật của file đã tải (ffprobe)

**Context**
- `downloaded_height`/`downloaded_vcodec`/`downloaded_universal` (`downloader.py:3966-3981`) lấy từ info của yt-dlp (điều nguồn nói), chỉ nhánh Cobalt mới đo file bằng `_file_video_stream` (`downloader.py:337-351`, ffprobe `width,height,codec_name`; gọi ở 384). `file_size_mb` đã đo từ file (3920).
- TikWM có bộ đo riêng `backend/app/services/format_probe.py` (`parse_ffprobe_json`, `_ffprobe_args`) qua `POST /api/v1/formats/probe` (`backend/app/api/format_probe.py:27`).
- Web hiện nhãn "4K/2K/Np" từ `downloaded_height` (`DashboardContent.jsx:2236-2250`); lịch sử `download_jobs` ghi `downloaded_height` (`routes.py:992`).
- App: `DoneEvent` chỉ có `file_path`, `file_size` (`desktop/src-tauri/src/lib.rs:170-176, 1051-1066`); `HistoryItem` (`history.rs:24-36`, schema v4 qua `PRAGMA user_version`, `migrate` 67-110); ffprobe là sidecar đã xác minh (`lib.rs:64`, `paths[2]`).

**Goal.** Sau mỗi lần tải có file trên máy chủ (hoặc trên máy người dùng với app), người dùng thấy một dòng sự thật đo từ chính file: độ nét · codec · fps · bitrate · HDR · dung lượng — và không có số nào không đo.

**Constraints**
1. Đo chỉ file cục bộ, một lần ffprobe, timeout 15 s, lỗi → trường `measured=false` và UI không hiện.
2. Không chậm việc giao file quá 1 s cho file mẫu.
3. Không lộ đường dẫn máy chủ.
4. Không đo file tải trực tiếp (không có file ở máy chủ) — trừ TikWM đã có `/formats/probe`.
5. Giá trị đo **ghi đè** `downloaded_height/vcodec/universal` khi có; thiếu thì giữ giá trị yt-dlp và `measured=false`.

**Scope**
- A: cấu trúc `downloaded_media = {measured, width, height, short_side, vcodec, acodec, fps, bitrate_kbps, audio_bitrate_kbps, sample_rate, channels, hdr (bool), color_transfer, duration, size_bytes, container}`. HDR = `color_transfer ∈ {smpte2084, arib-std-b67}` hoặc có side data mastering display.
- B: mở rộng `format_probe.parse_ffprobe_json` để trả thêm fps (`r_frame_rate` → số), `color_transfer`, audio; `_file_video_stream` thành `probe_media_file(path)` gọi `ffprobe -show_streams -show_format -of json <path>` rồi dùng chung parser đó (một parser cho TikWM và file). Gọi ở khối `local_path` (`downloader.py:3918-3925`), ghi `result["downloaded_media"]`, và ghi đè 3 trường cũ từ số đo (`short_side` cho `downloaded_height`).
- C: thêm `downloaded_media` vào phản hồi `fetch-link` (`routes.py:1072-1075`) và vào `download_jobs` dạng JSON (cột mới `downloaded_media jsonb` — migration; nếu chưa muốn migration thì bỏ, Scope C).
- D (web): nhãn ở `DashboardContent.jsx:2236-2250` thành "2160p · VP9 · 60 fps · 12 Mbps · HDR · 310 MB" chỉ khi `measured`. Bỏ nhánh suy từ `quality` cho MP3 khi đã có `audio_bitrate_kbps`.
- D (app): sau khi có `canon` (`lib.rs:1059-1065`) chạy sidecar ffprobe (`proc.rs::spawn_tree`), thêm `media` vào `DoneEvent` và `HistoryItem` (schema v5: `ALTER TABLE history ADD COLUMN media_json TEXT`), `types.ts` thêm `MediaInfo`; dòng hàng đợi/lịch sử hiện chuỗi như web. Cập nhật `docs/desktop/C1-CONTRACT.md`.
- E: test dưới.
- Không làm: đổi file; đo file ngoài máy chủ.

**Audit Before Build.** Đọc `downloader.py:337-395`, `3918-3981`; `format_probe.parse_ffprobe_json`; `routes.py:985-1000` (lịch sử); `lib.rs:939-1070`; `history.rs:67-110`; cách app hiện lịch sử (`desktop/src/lib/queue.ts:360`, màn History). Kiểm `ffprobe` trong ảnh Docker đã dùng (`Dockerfile:44`).

**Design Choice.** Một parser ffprobe dùng chung (`format_probe.parse_ffprobe_json`), nối vào chỗ `downloaded_height` đang điền; UI đọc một cấu trúc duy nhất. Không đọc metadata yt-dlp làm sự thật (lệch chính là lỗi MS-1). App chạy cùng lệnh ffprobe với sidecar.

**Test Plan**
- Unit (chạy tại workspace, không cần ffprobe): `parse_ffprobe_json` với JSON ffprobe mẫu lưu sẵn (H.264 30 fps SDR, VP9 60 fps, HEVC `smpte2084`, chỉ âm thanh AAC, Opus) → đúng từng trường; `r_frame_rate "60000/1001"` → 59.94; thiếu stream video → `no_video_stream`.
- Integration cần ffprobe (skip nếu thiếu): sinh 4 file bằng `ffmpeg -f lavfi -i testsrc2=size=320x180:rate=30 -t 1` (libx264; libvpx-vp9 `-r 60`; VP9 với `-color_primaries bt2020 -color_trc smpte2084 -colorspace bt2020nc` làm HDR giả; `-f lavfi -i sine` → AAC) → `probe_media_file` so với `ffprobe` chạy tay; file hỏng → `measured=false`; thời gian ≤ 1 s.
- Regression: `test_format_probe.py`, `test_tikwm_formats_no_guessed_height.py`, test `fetch-link` hiện có.
- App: `cargo test --lib` cho parser (Rust) với cùng JSON mẫu; `npm test` cho định dạng chuỗi hiển thị.
- Live: một lần tải thật trên web và trên app Windows; so dòng hiển thị với ffprobe chạy tay.

**Success Criteria.** (1) 4/4 file mẫu khớp ffprobe tay (fps sai số ≤ 0,01). (2) File HDR giả được gắn HDR, SDR thì không. (3) UI web/app không hiện số nào khi `measured=false`. (4) `downloaded_height` sau sửa = cạnh ngắn đo được. (5) Lịch sử app có `media_json` cho tải mới; bản cũ vẫn mở được (migration v5 chạy trên DB cũ).

**Tệp đổi:** `backend/app/services/format_probe.py`, `backend/app/services/downloader.py` (337-351, 384, 3918-3981), `backend/app/api/routes.py` (992, 1072-1075), migration SQL (tuỳ Scope C), `frontend/src/components/DashboardContent.jsx:2236-2250`, `desktop/src-tauri/src/lib.rs` (170-176, 1051-1066), `desktop/src-tauri/src/history.rs`, `desktop/src/lib/types.ts`, màn History/Queue, `docs/desktop/C1-CONTRACT.md`. **Công sức:** M (backend S, web S, app S–M). **Rủi ro:** thấp; ffprobe lỗi với file lạ → `measured=false`. **Kiểm khách quan:** ffprobe tay vs trường trả về. **Phát hành:** backend + web (đợt 1); app (đợt 4).

---

## MS-4 — Douyin/TikTok: luôn lấy luồng không logo nét nhất

**Context**
- Douyin ký (`backend/app/services/douyin_extractor.py:973-987` `_best_play_url`): mặc định `play_addr.url_list[0]`; chỉ `video_4k` mới quét `bit_rate[]` theo `(height, bit_rate)`. Các đường khác: iesdouyin (330-339) và ScraperAPI SSR (600-603) lấy `url_list[0]` rồi đổi `/playwm/`→`/play/`.
- TikTok/TikWM: `hdplay` → `play` → `wmplay` (`douyin_extractor.py:482`), kích thước thật đo sau bằng `/formats/probe`. yt-dlp dự phòng loại format có "watermark" (`downloader.py:1406`).
- Tiện ích: `vgDouyinFindInRenderData` (`chrome-extension/content.js:383-396`) lấy `play_addr.url_list[0]`/`playAddr[0].src`; `vgHttps` (376-380) đổi `/playwm/`; interceptor API (430-435) nhận `direct_url` từ script chèn.
- App: `links()` trong page script (`desktop/src-tauri/src/douyin_local.rs:143-158`) gom `play_addr`, `play_addr_h264`, lấy link đầu không chứa `/playwm/`.
- **Chưa đo** chênh lệch `play_addr` vs `bit_rate` cao nhất; chưa biết `bit_rate[]` có `is_h265`/`bytevc1` ở mức cao nhất không (Douyin thường có).

**Goal.** Ở cả ba nơi (máy chủ, tiện ích, app), mặc định lấy luồng không logo có độ nét cao nhất, ưu tiên H.264 ở cùng độ nét; không bao giờ trả về link `/playwm/`.

**Constraints**
1. Không mất bảo đảm không logo: mọi URL chọn ra phải không chứa `/playwm/` (hoặc đã rewrite) — khẳng định trong test.
2. Quy tắc codec thống nhất MS-1/MS-8: cùng độ nét, H.264 trước H.265 (`is_h265==1`/`bytevc1`); H.265 chỉ khi là duy nhất ở độ nét cao nhất và người dùng chọn "chất lượng gốc"… (mặc định "dễ mở": lấy độ nét cao nhất **trong số H.264**; nếu H.265 cao hơn, ghi `quality_note`).
3. Không đổi hợp đồng `/fetch-link`; TikWM giữ thứ tự `hdplay` trước.
4. Không làm bước đo mất thêm thời gian ở `fetch-link` (đo vẫn qua `/formats/probe`).
5. **Phải đo trước khi code** (bước audit 1) — nếu chênh lệch < 5% thì chỉ làm phần máy chủ.

**Scope**
- A: hàm chọn `pick_douyin_stream(video, policy)` dùng chung: duyệt `bit_rate[]` + `play_addr` + `play_addr_h264`, khoá sắp xếp `(height, not is_h265, bit_rate)`, bỏ URL có `/playwm/` sau khi rewrite thất bại; trả `{url, height, width, is_h265, bit_rate}`.
- B (backend): `_best_play_url` dùng hàm trên cho mọi quality (không chỉ `video_4k`); iesdouyin/SSR dùng cùng hàm khi payload có `bit_rate`; TikTok không đổi.
- B (tiện ích): `vgDouyinFindInRenderData` quét thêm `v.bitRateList`/`v.bit_rate` (`playAddr[].src`/`play_addr.url_list`, `width/height/bitRate/isH265`) với cùng khoá; script chèn tính `direct_url` cũng vậy (audit tìm tệp).
- B (app): `links()` trong page script dùng cùng khoá; `Resolved` thêm `height`, `isH265` (tuỳ chọn) để UI hiện.
- C: `fetch-link` trả thêm `quality_note` khi bỏ qua H.265 cao hơn.
- D: web/app hiện độ nét sau khi đo (MS-3 / `/formats/probe`).
- E: test dưới.
- Không làm: tải nhiều luồng để so; đổi TikWM.

**Audit Before Build**
1. **Đo (máy Windows của chủ sản phẩm, app hoặc trình duyệt):** 10 link Douyin thật (ngang/dọc/dài/ngắn). Với mỗi link lấy JSON `aweme_detail.video` (từ app `douyin_local` debug window hoặc tiện ích), liệt kê `play_addr` và từng `bit_rate[]` (`height,width,bit_rate,is_h265,gear_name`), tải 2 file (play_addr vs tốt nhất) bằng app, `ffprobe` cả hai. Ghi bảng vào `docs/plans/AUDIT-MS4-douyin.md`.
2. Tìm script chèn của tiện ích sinh `VG_DOUYIN_API.direct_url` (grep `VG_DOUYIN_API` trong `chrome-extension/`).
3. Đọc `backend/tests/test_douyin_signed.py:158-176` (fixture `DETAIL` có `bit_rate` 720/1080) để mở rộng.
4. Kiểm Referer/UA cần cho URL `bit_rate` (app đã truyền header, tiện ích có quyền CDN từ 5.22.2).

**Design Choice.** Một hàm chọn chung với khoá `(height, H.264 trước, bit_rate)`, mặc định cho mọi quality; được tái dùng nguyên văn (JS) ở tiện ích và page script của app. Không dùng "bit_rate cao nhất" đơn thuần vì sẽ ưu tiên H.265 và vỡ "dễ mở".

**Test Plan**
- Unit backend (workspace): `test_douyin_signed.py` — mặc định giờ ra `https://v/1080`; thêm ca: `bit_rate` có H.265 1080 + H.264 720 → H.264 720 kèm `quality_note`; URL `/playwm/` trong `bit_rate` → rewrite; không có `bit_rate` → `play_addr`.
- Tiện ích (node): `tests/test_douyin_direct.js` thêm fixture RENDER_DATA có `bitRateList`.
- App: `desktop/tests/douyin-init.test.ts`/`page-listing-douyin.test.ts` thêm fixture; `cargo test --lib` cho `parse_title` với trường mới.
- Live (Windows): 5 link Douyin qua app + tiện ích + web: ffprobe file nhận được, cạnh ngắn ≥ cạnh ngắn của `bit_rate` cao nhất H.264; không logo (kiểm mắt một khung hình — ghi nhận thủ công, không tự động).
- TikTok: 3 link qua production API (workspace bị chặn): `/formats/probe` trả kích thước `hdplay` ≥ `play`.

**Success Criteria.** (1) 10/10 mẫu đo: URL chọn không chứa `/playwm/`. (2) Độ nét file ≥ độ nét `play_addr` cũ ở mọi mẫu, và = cao nhất H.264 của `bit_rate[]`. (3) Không tăng `download_failed` Douyin 7 ngày. (4) Ba nơi dùng cùng khoá (test cùng fixture).

**Tệp đổi:** `backend/app/services/douyin_extractor.py` (330-339, 600-603, 973-987), `backend/tests/test_douyin_signed.py`, `chrome-extension/content.js` (376-396 + script chèn), `chrome-extension/tests/test_douyin_direct.js`, `chrome-extension/manifest.json` (5.23.0), `desktop/src-tauri/src/douyin_local.rs` (143-158, 245-253), `desktop/tests/…`. **Công sức:** M (sau khi đo). **Rủi ro:** URL bitrate cao bị chặn/hết hạn khác; file to hơn; H.265. **Kiểm khách quan:** ffprobe + bảng đo. **Phát hành:** backend (đợt 3) + tiện ích 5.23.0 + app 0.21.0 (gộp đợt 4).

---

## MS-5 — Âm thanh: chọn kbps, giữ âm gốc, sửa OGG/WEBM, chuẩn hoá âm lượng

**Context**
- `_get_base_opts`: `mp3*` lấy `bestaudio[ext=m4a]/bestaudio/best` (`downloader.py:1119`), postprocessor `FFmpegExtractAudio` mp3 với `"320" if "320" in quality else "128"` (1196-1202); `audio_<codec>` cố định 128 (1203-1210). yt-dlp `ACODECS` chỉ có: mp3, aac, m4a, opus, vorbis, flac, alac, wav — **không có `webm`** → `audio_webm` gần như chắc lỗi `KeyError` trong PP [SL, đọc mã `FFmpegExtractAudioPP.run`, chưa chạy]. `preferredcodec="best"` = copy luồng (AAC→`.m4a`, Opus→`.opus`) không nén lại.
- Web: `handleMergeDownload` (`DashboardContent.jsx:1063-1068`): `ogg`→`mp3_128`, `webm`→`audio_webm`; 993 gọi `'ogg'` cho MP3 128; hàng audio 3182 truyền `fmt.ext`. `SuccessCard.jsx:63` coi ogg là audio.
- Đường riêng `/process/extract-audio` (`processing.py:328-375`): mp3/m4a, 128/192/320, `loudnorm=I=-16:TP=-1.5:LRA=11` (368), `-id3v2_version 3`.
- App: M4A copy hoặc MP3 VBR 0 (`engine.rs:52-66, 207-217`; `formats.rs:66-67`), không chọn kbps.
- Sau PP, `downloader.py:3926-3929` đoán đường dẫn `.mp3` bằng regex — sẽ sai với đuôi mới.

**Goal.** Người dùng chọn được MP3 128/192/256/320, "giữ âm gốc" không nén lại (M4A/Opus), OGG thật, và tuỳ chọn chuẩn hoá âm lượng; nhãn kbps đúng với file.

**Constraints**
1. Ngữ pháp quality tương thích ngược: `mp3_128`, `mp3_320`, `audio_m4a` giữ nghĩa cũ.
2. Chỉ nhận `audio_<codec>` với codec ∈ `ACODECS` + `best`; chuỗi lạ → 400, không rơi vào yt-dlp.
3. "Giữ âm gốc" không được nén lại; chuẩn hoá âm lượng bắt buộc nén lại → không cho phép bật cùng "giữ gốc" (UI khoá; máy chủ trả `quality_note`).
4. Không đổi `/process/extract-audio`.
5. Đường dẫn file sau PP lấy từ `requested_downloads[0]["filepath"]`, không đoán đuôi.

**Scope**
- A: quality: `mp3_<128|192|256|320>`, `audio_best` (copy), `audio_m4a` (AAC: copy nếu nguồn AAC, nếu không nén ở kbps), `audio_opus` (`.opus`, copy nếu nguồn Opus), `audio_vorbis` (`.ogg`, nén). Trường mới `FetchLinkRequest.audio_bitrate: Optional[int]` (áp cho nén) và `normalize_audio: Optional[bool]`.
- B: `_get_base_opts` dựng PP từ bảng trên; `normalize_audio` → `postprocessor_args={"extractaudio": ["-af","loudnorm=I=-16:TP=-1.5:LRA=11"]}` (cùng tham số `processing.py:368`); mp3 thêm `-id3v2_version 3`. Sửa 3926-3929 và `_local_file_response` media types (thêm `.opus`, `.ogg`), `proxy_download` `safe_ext`.
- C: phản hồi có `downloaded_media.acodec/audio_bitrate_kbps` (MS-3).
- D (web): khối audio: chọn định dạng (MP3 / Giữ âm gốc / OGG) + kbps (chỉ MP3/OGG) + ô "Chuẩn hoá âm lượng" (khoá khi giữ gốc). Bỏ nút "webm". **Wording BA review:** "Giữ âm gốc (không nén lại, M4A hoặc Opus)", "Chuẩn hoá âm lượng (nghe đều giữa các bài, sẽ nén lại)".
- D (app): `engine.rs:207-217` thêm `audio_quality: Option<String>` → `--audio-quality 192K`; preset "Giữ âm gốc" = `--audio-format best`; `formats.rs:166-184` thêm preset "Âm gốc" và nhãn kbps; `settings.ts` thêm `audioBitrate`.
- E: test dưới.
- Không làm: FLAC/WAV; chuẩn hoá cho video.

**Audit Before Build.** Chạy thử `audio_webm` một lần trên dev có ffmpeg để ghi lỗi thật; đọc `routes.py` chỗ xác thực quality (grep `startswith("mp3")`) và `youtube_gate._requested_height` (`youtube_gate.py:108-123`) để chuỗi mới vẫn được coi là audio; đọc `quotas.check_youtube_tier` (suggested `mp3_128`); kiểm `fetchlink.ts` của app gửi quality nào cho đường máy chủ.

**Design Choice.** Mở rộng ngữ pháp quality + hai trường mới, map thẳng sang `FFmpegExtractAudio` (`preferredcodec`, `preferredquality`) và `postprocessor_args`; không viết ffmpeg tay trong luồng tải. Lý do: yt-dlp đã xử lý copy-vs-nén đúng (`FFmpegExtractAudioPP.run`), ít mã nhất, app dùng cùng cờ CLI.

**Test Plan**
- Unit (workspace): bảng quality → `postprocessors`/`postprocessor_args` mong đợi; chuỗi lạ `audio_webm`, `audio_exe` → 400 ở route; `normalize_audio` + `audio_best` → bị từ chối/ghi chú.
- Integration cần ffmpeg (skip nếu thiếu): file nguồn `sine` AAC 1 s; chạy `FFmpegExtractAudioPP` trực tiếp cho từng quality → ffprobe: `mp3_192` → `codec_name=mp3`, `bit_rate` 192k ±10%; `audio_best`/`audio_m4a` → `codec_name=aac` và `bit_rate` bằng nguồn (copy); `audio_vorbis` → `vorbis`; `audio_opus` từ nguồn opus → copy. Chuẩn hoá: `ffmpeg -i out -af ebur128 -f null -` → I = −16 ±1 LU.
- App: `cargo test --lib` (engine.rs args có `--audio-quality 192K`, `--audio-format best`); `npm test` cho settings.
- Live: một bài YouTube (workspace nếu mở được) ở MP3 320 và "giữ gốc"; so kbps hiển thị (MS-3) với ffprobe.

**Success Criteria.** (1) Nút OGG tạo file `.ogg` codec vorbis; không còn gọi `mp3_128`. (2) `audio_best` không nén lại (codec và bitrate = nguồn). (3) 4 mức kbps cho `bit_rate` trong ±10%. (4) Chuẩn hoá đạt −16 ±1 LU. (5) Không còn chuỗi `audio_webm` trong web.

**Tệp đổi:** `backend/app/services/downloader.py` (1117-1119, 1196-1210, 3926-3929), `backend/app/api/routes.py` (274-284, `_local_file_response`, `proxy_download safe_ext`), `frontend/src/components/DashboardContent.jsx` (993, 1063-1068, 3176-3195), `SuccessCard.jsx:63`, locales, `desktop/src-tauri/src/engine.rs` (52-66, 207-217), `desktop/src-tauri/src/formats.rs` (63-67, 164-185), `desktop/src/lib/settings.ts`, `desktop/src/components/ProbeCard.tsx`. **Công sức:** M. **Rủi ro:** đổi hợp đồng web↔máy chủ (deploy web và API cùng lúc; API phải chấp nhận cả chuỗi cũ). **Kiểm khách quan:** ffprobe + ebur128. **Phát hành:** backend + web (đợt 2); app (đợt 4).

---

## MS-6 — Gắn tiêu đề/nghệ sĩ/ngày/ảnh bìa/chương vào file

**Context**
- Không có `FFmpegMetadata`/`EmbedThumbnail`/`--embed-*` ở backend, app, tiện ích (đã grep). Chương chỉ trả về để hiện (`downloader.py:3983-3996`).
- `smart_metadata.enhance_mp3_tags` (`backend/app/services/smart_metadata.py:279`) suy artist/title/album, chỉ dùng trong tác vụ phân tích.
- yt-dlp venv: `FFmpegMetadataPP` (ghi tệp ffmetadata UTF-8, map `title/date/artist/album/description/comment` — `ffmpeg.py:700-760`), `EmbedThumbnailPP` (mp3/mkv qua ffmpeg; mp4/m4a qua **mutagen** — đã cài `mutagen 1.47.0` trong venv; webp tự đổi sang jpg/png, `embedthumbnail.py:83`), `FFmpegEmbedSubtitle` (MS-7). Thứ tự chạy = thứ tự danh sách `postprocessors`.
- App: yt-dlp.exe chính thức kèm mutagen [SL, cần kiểm trên Windows]; nếu thiếu, PP tự rơi về ffmpeg (`embedthumbnail.py:151`).

**Goal.** File video có tiêu đề/ngày/chương; file âm thanh có tiêu đề/nghệ sĩ/album/ảnh bìa; chữ Việt trong thẻ còn nguyên — ở cả web và app.

**Constraints**
1. Chỉ dùng postprocessor của yt-dlp + mutagen cho thẻ nâng cao; không viết bộ ghi thẻ mới.
2. Thứ tự PP cố định: ExtractAudio → FFmpegMetadata → EmbedThumbnail (ảnh phải gắn sau khi đổi định dạng).
3. MP3 ghi ID3v2.3 (`-id3v2_version 3`, như `processing.py:360`) để Windows Explorer đọc chữ Việt.
4. Lỗi gắn thẻ/ảnh không làm hỏng tải: PP lỗi → giữ file, `quality_note="metadata_skipped"`.
5. Không ghi `description` dài vào MP4 nếu > 1 KB (một số trình phát hiện xấu) — cắt.
6. Ảnh bìa tải về (`writethumbnail`) phải bị xoá sau khi gắn (không để file `.webp` trong thư mục tải).

**Scope**
- A: không có trường mới; tuỳ chọn `embed_metadata: bool` mặc định **true** (web) — có công tắc env `EMBED_METADATA_DEFAULT`.
- B (backend): trong `_get_base_opts` thêm `writethumbnail=True`, PP `FFmpegMetadata` (`add_metadata`, `add_chapters`), `EmbedThumbnail` (`already_have_thumbnail=False`); `postprocessor_args={"metadata": ["-id3v2_version","3"]}`. Với `mp3`/`audio_*`: sau tải, dùng `enhance_mp3_tags(info)` + mutagen ghi `artist/title/album` nếu `confidence ≥ 0.6` (Scope B; A chỉ dùng thẻ yt-dlp).
- B (app): `download_args` thêm `--embed-metadata --embed-chapters --embed-thumbnail` (+ `--ppa "Metadata:-id3v2_version 3"`), bật/tắt bằng setting `embedMetadata` (mặc định bật).
- C: không đổi API.
- D: web/app: dòng nhỏ "Đã gắn tiêu đề/ảnh bìa" từ `downloaded_media.tags` (MS-3 thêm `tags: {title, artist, has_cover, chapters}`), **wording BA review**.
- E: test dưới.
- Không làm: lời bài hát, thẻ tuỳ chỉnh do người dùng nhập.

**Audit Before Build.** Chạy một tải MP3 thật và `ffprobe -show_format` để chứng minh hiện không có thẻ (tài liệu nghiên cứu ghi "chưa mở thử"); kiểm `yt-dlp.exe --verbose` trên Windows in `mutagen` trong danh sách phụ thuộc; xem `downloader.py:1187-1210` để đặt PP cho `video` (hiện không có PP) và `video_fast` (không ghép → không gắn).

**Design Choice.** Postprocessor yt-dlp cho mọi nền tảng + mutagen chỉ để bổ sung artist/album từ `enhance_mp3_tags`. Lý do: cùng cờ cho backend và app, UTF-8 do yt-dlp lo, ảnh webp tự đổi.

**Test Plan**
- Unit (workspace): `_get_base_opts` có PP đúng thứ tự cho `video_1080`, `mp3_320`, `audio_best`; `video_fast` không có; env tắt → không có.
- Integration cần ffmpeg (skip nếu thiếu): info dict giả `{"title":"Đường về nhà đẹp","uploader":"Ca sĩ Đ","upload_date":"20261027","chapters":[...2 chương],"thumbnails":[jpg cục bộ]}` + file `testsrc2` mp4 và mp3 → chạy `FFmpegMetadataPP`, `EmbedThumbnailPP` trực tiếp → `ffprobe -show_format -show_streams -show_chapters`: `format.tags.title == "Đường về nhà đẹp"`, `tags.artist` có "Đ", 2 chương đúng mốc ±0,01 s, có stream `codec_type=video` với `disposition.attached_pic=1` (mp3 và mp4), không còn `.webp`/`.jpg` rời.
- App: `cargo test --lib` khẳng định cờ; thử thật trên Windows → mở Properties của Explorer thấy tiêu đề tiếng Việt.
- Live: 1 MP3 YouTube, 1 MP4 có chương.

**Success Criteria.** (1) 2/2 file mẫu có thẻ UTF-8 đúng, ảnh bìa `attached_pic=1`, chương đúng. (2) Lỗi PP không làm tải thất bại. (3) Không còn file ảnh rời trong thư mục tải. (4) App Windows: Explorer hiện tiêu đề có dấu.

**Tệp đổi:** `backend/app/services/downloader.py` (1187-1210 + khối sau tải), `backend/app/services/smart_metadata.py` (dùng lại), `backend/tests/test_embed_metadata.py` (mới), `desktop/src-tauri/src/engine.rs:162-229`, `desktop/src/lib/settings.ts`, UI web/app (dòng nhỏ). **Công sức:** M (backend S–M, app S). **Rủi ro:** thời gian PP thêm vài giây với 4K (ffmpeg `-c copy`); mutagen thiếu trên app → rơi về ffmpeg; webp lỗi đổi. **Kiểm khách quan:** ffprobe tags/chapters/attached_pic. **Phát hành:** backend (đợt 2); app (đợt 4).

---

## MS-7 — Phụ đề trọn gói: SRT chuẩn, nhúng mềm vào MP4, nối dịch, app có phụ đề

**Context**
- Tải: `writesubtitles`/`writeautomaticsub`, ngôn ngữ vi→en (`downloader.py:3207-3212`); không đặt `subtitlesformat` (đường subtitle-only có, 1427-1440). `_find_subtitle_file` (805-830) ưu tiên `.srt`, nếu `.vtt` thì đổi bằng regex `_convert_vtt_to_srt` (784) — không khử cue lặp của phụ đề tự động YouTube, bỏ thẻ bằng regex.
- Hậu xử lý ở route (`routes.py:1014-1048`): `burned` = libx264 CRF 23 preset fast, giữ tiếng (488-503); `soft` = MKV `-c:s srt` (505-518) → đuôi `.mkv`. Có `app/services/subtitle_format.py` (`parse_srt/parse_vtt/serialize_srt`, test `test_subtitle_format.py`) và `subtitle_qa`.
- Dịch: `translate_cues(cues, src, dst)` (`backend/app/services/transcript_translation_service.py:138`), job qua `POST /transcript-translate/upload` (`transcript_translate.py:198`), `jobs/{id}/download`, `burn-into-video` (487) — công cụ riêng, chưa nối vào lúc tải. `FEATURES.md:477` ghi "Subtitle backend partial — chưa test end-to-end".
- yt-dlp venv có `FFmpegSubtitlesConvertorPP` và `FFmpegEmbedSubtitlePP` (mp4 → `-c:s mov_text`, `ffmpeg.py:221`).
- App: không có tuỳ chọn phụ đề (`engine.rs`).

**Goal.** Chọn "kèm phụ đề" thì nhận SRT hợp lệ; chọn "nhúng mềm" thì vẫn là `.mp4` có luồng `mov_text`; có nút dịch sang tiếng Việt dùng job có sẵn; app Windows tải và nhúng được phụ đề.

**Constraints**
1. Nhúng mềm: `-c:v copy -c:a copy`, không bao giờ nén lại video.
2. Đốt cứng: nén lại là bắt buộc; CRF 18 (quyết định chủ sản phẩm; file to hơn ~1,5–2×), preset giữ `fast`.
3. Dịch là job bất đồng bộ có sẵn, có hạn mức AI theo hạng (`ai_tools`); không chạy LLM trong `fetch-link`.
4. SRT đi qua `subtitle_format.parse_*` → `serialize_srt`; file không parse được → không trả (`subtitle_error`), không trả SRT hỏng.
5. Không thêm mô hình AI (Q4).
6. App: phụ đề là tuỳ chọn, mặc định tắt.

**Scope**
- A: `subtitle_mode` giữ nguyên tập giá trị; `soft` giờ ra `.mp4` khi video là mp4 (chỉ còn `.mkv` khi file nguồn là mkv). Trường `subtitle_translate_to` **không thêm** vào fetch-link; thay bằng nút trên kết quả.
- B (backend): (1) `dl_opts["subtitlesformat"]="srt/vtt/best"`; `_convert_vtt_to_srt` đổi thân thành `parse_vtt` → khử cue lặp liên tiếp (cùng text, mốc nối nhau — kiểu auto-caption) → `serialize_srt`. (2) `_mux_subtitle(video, srt, out.mp4)`: `-c:s mov_text -metadata:s:s:0 language=vie -movflags +faststart` khi đuôi `.mp4`; giữ nhánh MKV cho mkv. (3) `_burn_subtitle` CRF 18. (4) Nối dịch: `POST /transcript-translate/upload` nhận thêm `local_file_id` (id của SRT vừa tải, dùng `safe_file_id`) thay vì chỉ upload; phần còn lại dùng job có sẵn; `burn-into-video` nhận `local_file_id` video đã tải.
- C: `fetch-link` trả thêm `subtitle_file_id` (đã có `subtitle_file_url`), `subtitle_language_actual`.
- D (web): nút "Dịch sang tiếng Việt" dưới dòng phụ đề (`DashboardContent.jsx:~2256`) → gọi upload với `local_file_id`, theo dõi job, cho tải SRT dịch và "đốt vào video". **Wording BA review.**
- D (app): setting `subtitles: 'off'|'file'|'embed'`, ngôn ngữ `vi,en`; `download_args` thêm `--write-subs --write-auto-subs --sub-langs "vi.*,en.*" --sub-format srt/best --convert-subs srt` và `--embed-subs` cho `embed`; ô chọn trong `ProbeCard`.
- E: test dưới.
- Không làm: dịch tự động ngay khi tải; Whisper tại app.

**Audit Before Build.** Chạy đầu-cuối thật một YouTube có phụ đề tự động với `subtitle_mode=file|soft|burned` trên dev có ffmpeg, ghi kết quả vào `TEST_LOG` (xoá nhãn "partial" ở `FEATURES.md:477` hoặc giữ, theo kết quả). Đọc `test_phase13_subtitle.py`, `test_process_subtitle_lang.py`, `test_subtitle_qa.py` để không trùng. Kiểm `transcript_translate.py:198-260` cách nhận file và hạn mức.

**Design Choice.** SRT qua parser có sẵn `subtitle_format` (không tin regex), nhúng mềm bằng `mov_text` trong `.mp4` (chuẩn Apple/Windows/VLC), dịch nối bằng `local_file_id` vào job sẵn có. Không chọn `FFmpegEmbedSubtitlePP` của yt-dlp cho backend vì route đã có bước mux sau tải (một chỗ xử lý); app thì dùng cờ yt-dlp vì không có bước sau.

**Test Plan**
- Unit (workspace): `_convert_vtt_to_srt` với VTT auto-caption có cue lặp → SRT parse lại được, số cue = số cue duy nhất, mốc tăng dần; VTT hỏng → None. Route `soft` với ffmpeg giả → gọi đúng tham số `mov_text` và đuôi `.mp4`.
- Integration cần ffmpeg (skip nếu thiếu): `testsrc2` mp4 + SRT 2 cue tiếng Việt → `_mux_subtitle` → `ffprobe -select_streams s -show_entries stream=codec_name:stream_tags=language` = `mov_text`,`vie`; video/audio `codec_name` không đổi; `_burn_subtitle` → `codec_name=h264`, thời lượng ±0,05 s.
- Integration (TestClient): upload với `local_file_id` → job tạo; id lạ → 403.
- App: `cargo test --lib` cờ phụ đề; `npm test` settings; Windows: tải một YouTube có phụ đề → có `.srt` cạnh file, bản `embed` có luồng `mov_text`.
- Live: 2 video YouTube (phụ đề người + tự động).

**Success Criteria.** (1) Mọi SRT trả về parse được bằng `parse_srt`, mốc tăng dần. (2) `soft` cho `.mp4` có `mov_text` ngôn ngữ `vie`, video/audio copy. (3) Nút dịch tạo job từ file đã tải (không upload lại), SRT dịch tải được. (4) App: `.srt` và luồng nhúng như web. (5) `FEATURES.md:477` cập nhật theo kết quả đo thật.

**Tệp đổi:** `backend/app/services/downloader.py` (784-830, 3207-3212), `backend/app/api/routes.py` (488-518, 1014-1048, 1077-1081), `backend/app/api/transcript_translate.py` (198, 487), `backend/app/services/subtitle_format.py` (hàm khử lặp), tests mới `test_subtitle_pipeline.py`, `frontend/src/components/DashboardContent.jsx` (~2256), locales, `desktop/src-tauri/src/engine.rs`, `desktop/src/lib/settings.ts`, `ProbeCard.tsx`, `FEATURES.md`, `docs/FEATURE_SPEC_transcript_translate.md`. **Công sức:** L (SRT+mux M, nối dịch S, app S–M). **Rủi ro:** chi phí LLM/hạn mức; CRF 18 tốn CPU; auto-caption lặp xử lý chưa hết mẫu. **Kiểm khách quan:** ffprobe luồng phụ đề; parser SRT. **Phát hành:** backend + web (đợt 2); app (đợt 4).

---

## MS-8 — App Windows: preset "Dễ mở" và "Chất lượng gốc"

**Context**
- Preset `bv*[height<=N]+ba/b[height<=N]/b` (`desktop/src-tauri/src/formats.rs:47-49`; `desktop/src/lib/quality.ts:70-72`), không ép codec → yt-dlp mặc định ưu tiên AV1/VP9/Opus. `GUEST_FORMAT` (`quality.ts:10-11`) cũng không ép codec. Ghép `mp4/mkv` (`engine.rs:187-188`). "Tốt nhất" không `-f` (196-206).
- `ProbeFormat` có `vcodec/acodec/fps` (`formats.rs:12-25`, `types.ts:3-14`) nhưng `ProbeCard` chỉ hiện nhãn (`ProbeCard.tsx:72-73`); yt-dlp `-J` còn có `dynamic_range` (chưa map).
- Settings có `defaultQuality`, `platformPresets` (`settings.ts:6-27`).

**Goal.** Người dùng app chọn được "Dễ mở trên mọi máy" (H.264/AAC, mặc định) hoặc "Chất lượng gốc" (codec tốt nhất, có thể VP9/AV1/Opus), và thấy codec/fps/HDR của mỗi mức trước khi tải — cùng quy tắc với web (MS-1).

**Constraints**
1. Quy tắc giống MS-1: ≤1080 H.264+AAC; >1080 VP9→AV1→bất kỳ, tiếng AAC trước; "gốc" = để yt-dlp chọn theo thứ tự mặc định.
2. Không đổi id preset cũ đang lưu trong `platformPresets`; thêm trường mới có mặc định.
3. Khách (chưa đăng nhập) vẫn bị chặn 1080 (`capForGuest`), áp cùng codec pref.
4. Giữ `--merge-output-format mp4/mkv` (yt-dlp tự chọn mkv khi luồng không vào mp4 được).
5. Nhãn chỉ nói codec/fps có trong JSON probe; thiếu → không nói.

**Scope**
- A: `settings.codecPref: 'easy'|'original'` (mặc định `'easy'`); `ProbeFormat.dynamicRange: string|null` (từ `dynamic_range`); cập nhật `docs/desktop/C1-CONTRACT.md:52-56`.
- B: `presetFormat(h, pref)` (quality.ts) và `preset_selector(max_h, pref)` (formats.rs) sinh chuỗi: `easy` = `bv*[height<=H][vcodec^=avc1]+ba[acodec^=mp4a]/bv*[height<=H][height>1080][vcodec~='^vp0?9']+ba[acodec^=mp4a]/bv*[height<=H][height>1080][vcodec^=av01]+ba[acodec^=mp4a]/bv*[height<=H]+ba/b[height<=H]/b` (cho H>1080; với H≤1080 bỏ hai nhánh giữa); `original` = chuỗi hiện tại. `GUEST_FORMAT` thêm biến thể easy. `qualityFormat(q, guest, pref)`; `queue.ts:272-277` truyền pref. "Tốt nhất" + easy = chuỗi easy với H = cao nhất có.
- C: không có API máy chủ.
- D: Settings: nhóm "Mở file" với hai lựa chọn + mô tả (**wording BA review**: "Dễ mở trên mọi máy — H.264/AAC, mở được bằng trình phát mặc định của Windows, Mac, iPhone; 2K/4K dùng VP9/AV1 vì YouTube không có H.264 ở mức đó" / "Chất lượng gốc — codec tốt nhất nguồn có (VP9/AV1/Opus), file nhỏ hơn hoặc nét hơn, có thể cần VLC"). `ProbeCard`: nhãn "1080p (Full HD) · H.264 · 30 fps", "2160p (4K) · VP9 · 60 fps · HDR" dựa trên format được chọn theo pref (hàm `describeFormat`).
- E: test dưới.
- Không làm: chuyển mã trong app; preset theo nền tảng cho codec (Scope C, dùng `platformPresets`).

**Audit Before Build.** Đọc `queue.ts:180-280` (đường Douyin/server bỏ `formatId`), `presets-core.ts`, `desktop/tests/*.test.ts` (chưa có test cho `quality.ts` → thêm), `formats.rs` tests 240-341; xác nhận yt-dlp `-J` trả `dynamic_range` cho YouTube (chạy `yt-dlp -J` trên Windows một lần).

**Design Choice.** Hai chuỗi chọn cố định sinh từ một hàm dùng chung TS+Rust, lưu pref trong settings; không thêm preset mới vào danh sách chất lượng (giữ UI gọn). Lý do: khớp web (MS-1), test bằng so chuỗi, không cần đổi engine.

**Test Plan**
- Unit (`desktop/tests/quality.test.ts`, chạy tại workspace): `qualityFormat('1080',false,'easy')` == chuỗi mong đợi; `'2160'` easy có nhánh vp9 trước av01; `original` == chuỗi cũ; guest + easy; `describeFormat` với/không `fps`/`dynamicRange`.
- `cargo test --lib`: `preset_selector` hai pref; `map_probe` đọc `dynamic_range`; args `download_args` không đổi.
- Live (Windows, cùng một video YouTube có 4K/60fps/HDR): easy@1080 → ffprobe `h264`/`aac`, `.mp4`; original@1080 → `vp9|av1`; easy@2160 và original@2160 → cạnh ngắn ≥ 2052; mở thử bằng "Phim & TV" (easy 1080 phải mở); ghi kết quả.

**Success Criteria.** (1) Chuỗi easy ≤1080 ra H.264/AAC trên fixture YouTube (yt-dlp `-F` giả qua `map_probe` + test chọn). (2) easy@2160 ra ≥2052 cạnh ngắn VP9/AV1. (3) Nhãn mức chất lượng hiện codec/fps đúng với JSON. (4) Người dùng cũ mở app mới: pref = easy, không lỗi settings.

**Tệp đổi:** `desktop/src/lib/quality.ts`, `desktop/src/lib/settings.ts`, `desktop/src/lib/queue.ts:272-277`, `desktop/src/lib/types.ts`, `desktop/src/components/ProbeCard.tsx`, màn Settings, `desktop/src-tauri/src/formats.rs` (47-49, 150-161), `desktop/tests/quality.test.ts` (mới), `docs/desktop/C1-CONTRACT.md`. **Công sức:** M. **Rủi ro:** đổi mặc định cho người dùng hiện tại (file 1080 nay là H.264 to hơn VP9); yt-dlp exe bản khác hiểu regex `~=` (có từ lâu). **Kiểm khách quan:** ffprobe hai preset cùng video. **Phát hành:** app 0.21.0 (đợt 4).

---

## Thứ tự thực hiện

**Đợt 1 — backend + web "quick wins" (một deploy API + một deploy FE)**
Thứ tự: MS-2 (S) → MS-3 backend+web (M) → MS-1 (S).
Lý do: MS-3 là thước đo cho MS-1 và mọi mục sau; MS-2 độc lập, rẻ, người Việt thấy ngay.
Phụ thuộc: MS-1 cần quyết định env `YT_MAX_HEIGHT_USER` (và `FREE_MAX_HEIGHT` — Q4 P1.2) để có hiệu lực trên production.
Checklist phát hành đợt 1:
1. `cd backend && venv/bin/python -m pytest -q` xanh (một phiên); các test ffprobe chạy trong ảnh Docker backend (ghi số skip/pass).
2. `cd frontend && npm run build && npm run lint`.
3. Audit MS-1 bước 3 (3 video thật) ghi vào `docs/plans/TEST-LOG-QUALITY.md`.
4. Deploy API trước (chấp nhận hợp đồng cũ), rồi FE (`vays` `redeploy_project` ×2); kiểm `/health`, một lần tải thật mỗi loại: YouTube 1080 (H.264), YouTube 2160 (nếu env cho), TikTok, tên file "Đường về nhà đẹp".
5. Đặt env nếu chủ sản phẩm duyệt; theo dõi `download_failed` 7 ngày.
6. Lùi: `rollback_project` FE rồi API.

**Đợt 2 — backend + web media postprocessor (một deploy API + FE)**
Thứ tự: MS-5 (M) → MS-6 (M) → MS-7 (L).
Phụ thuộc: MS-6 đặt PP sau ExtractAudio của MS-5 (thứ tự danh sách); MS-7 dùng `downloaded_media` của MS-3 để hiện luồng phụ đề; MS-5/MS-6 cần MS-3 để hiện kbps/thẻ.
Checklist: như đợt 1, thêm: ffprobe 3 file mẫu (mp3 192, m4a copy, mp4 có mov_text + chương + ảnh bìa) trong Docker; thử nút dịch với tài khoản Pro thật; `FEATURES.md` cập nhật mục phụ đề; API deploy trước FE vì web gửi quality mới.

**Đợt 3 — Douyin/TikTok (chặn bởi bước đo)**
MS-4 backend (sau khi có `AUDIT-MS4-douyin.md`), tiện ích 5.23.0, app (gộp đợt 4).
Checklist: pytest Douyin; `node chrome-extension/tests/test_douyin_direct.js` + `test_direct_download.js` + `test_web_contract.js`; `scripts/package-extension.py`; cập nhật trang `/extension`; deploy API; thử 5 link Douyin trên Windows qua ba đường; theo dõi `download_failed` Douyin 7 ngày.

**Đợt 4 — app Windows 0.21.0 (một GitHub Release)**
MS-8 (M) + phần app của MS-3, MS-5, MS-6, MS-7, MS-4 (mỗi phần S–M).
Phụ thuộc: MS-8 phải khớp chuỗi MS-1 đã deploy; MS-3-app trước để kiểm MS-8/MS-5/MS-6/MS-7 bằng ffprobe sidecar.
Checklist: `cd desktop && npm run typecheck && npm test`; `cd desktop/src-tauri && cargo test --lib && cargo clippy`; tăng version 3 tệp + `Cargo.lock`; build NSIS, `.exe` + `.sig`; thử trên Windows 10 và 11 sạch theo `docs/desktop/BUILD.md` §6 + bảng ffprobe hai preset; tạo GitHub Release `v0.21.0`; kiểm `/api/v1/client/update/windows/x86_64/0.20.2` trả `signature`; `docs/desktop/C1-CONTRACT.md` cập nhật; lùi bằng công tắc khẩn cấp admin.

**Quyết định cần chủ sản phẩm chốt trước khi bắt đầu:** (1) env chiều cao YouTube/Free; (2) mặc định giữ dấu; (3) duyệt bước đo Douyin; (4) OGG = Vorbis hay Opus; chuẩn hoá Pro-only?; (5) CRF 18 và nối dịch là job Pro; (6) app mặc định "Dễ mở"; (7) mọi wording "BA review".

### Critical Files for Implementation
- /home/coder/workspace/share-projects/Dowload-video/backend/app/services/downloader.py
- /home/coder/workspace/share-projects/Dowload-video/backend/app/api/routes.py
- /home/coder/workspace/share-projects/Dowload-video/backend/app/core/local_download.py
- /home/coder/workspace/share-projects/Dowload-video/desktop/src-tauri/src/engine.rs
- /home/coder/workspace/share-projects/Dowload-video/desktop/src/lib/quality.ts

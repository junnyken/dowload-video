# Sổ tay sự cố cho owner (Phase 34A-2, task #6332)

Dành cho một người vận hành, không cần biết lập trình. Mỗi mục: **Dấu hiệu -> Kiểm tra (đúng thứ tự) -> Xử lý -> Khi nào báo ai**.
Quy ước: "Admin" = `https://dvid.vibe1.tinhgon.xyz/vid-admin/...` (tên menu ghi đúng như trong thanh bên). "Vibe Host" = bảng điều khiển Vibe Host của project `dowload-video-backend` / `dowload-video-frontend`. "Chưa kiểm" = chưa thử thật, đừng coi là đã chạy.

Các runbook cũ trong thư mục này (`stuck-jobs.md`, `retry-storm.md`, `file-expiry.md`...) và `docs/RUNBOOK.md` viết cho máy Oracle VPS cũ (lệnh `ssh vidgrab ...`). Trên Vibe Host **không dùng được các lệnh ssh đó**; chỉ lấy phần giải thích nguyên nhân.

Giám sát ngoài và cách đọc cảnh báo: `external-monitoring.md`. Trang trạng thái công khai nằm trên UptimeRobot (vẫn mở được khi vibe1 chết).

Luật chung: **không deploy khi đang có sự cố** (13/10 deploy cũng lỗi "fetch failed"). Đổi biến môi trường trên Vibe Host = sửa env + **Redeploy** + tăng `VIDGRAB_ENV_REV` (nếu không Vibe Host báo `NO_CHANGE`).

---

## (a) Host vibe1 không vào được / chậm
Ví dụ thật 13/10/2026: 09:40-11:30 web, API, app cùng "mất kết nối"; TLS 5-14 s, `/health` timeout; deploy "fetch failed 6/6"; log báo `wings_error`. Tự hết, phục hồi lúc ~11:25 (`docs/PHASE-34-0-EVIDENCE.md`).
- **Dấu hiệu**: UptimeRobot báo đỏ cả "VidGrab Web" và "VidGrab API"; người dùng báo không tải được.
- **Kiểm tra**: (1) trang trạng thái UptimeRobot: tất cả đỏ hay một cái? (2) mở `https://dvid-api.vibe1.tinhgon.xyz/health` trên điện thoại 4G (loại trừ mạng nhà) — thấy `"status":"ok"` là tiến trình sống, chỉ chậm. (3) Vibe Host: trạng thái project và vài dòng log cuối.
- **Xử lý**: tất cả đỏ = lỗi máy/mạng của nhà cung cấp, **bạn không sửa được**: đừng Redeploy (dễ hỏng thêm). Chỉ API đỏ, web xanh: Vibe Host -> backend -> xem log -> Redeploy một lần.
- **Báo ai**: người quản trị Vibe Host ngay khi đỏ > 10 phút, kèm giờ bắt đầu trên UptimeRobot và ảnh `/health`. Đỏ > 1 giờ: cân nhắc phương án B trong `continuity-options.md`. Ghi giờ bắt đầu/kết thúc vào nhật ký sự cố.

## (b) DNS / tên miền không phân giải
- **Dấu hiệu**: trình duyệt báo "không tìm thấy máy chủ" / `DNS_PROBE_FINISHED_NXDOMAIN`; UptimeRobot đỏ nhưng Vibe Host báo project vẫn chạy.
- **Kiểm tra**: (1) thử mạng khác (4G). (2) trang web ngoài kiểu "dnschecker.org" với `dvid.vibe1.tinhgon.xyz` và `dvid-api.vibe1.tinhgon.xyz` — có ra IP `203.171.31.200` không? (3) Vibe Host: project còn gắn đúng tên miền không?
- **Xử lý**: tên miền `*.vibe1.tinhgon.xyz` do nhà cung cấp quản lý, owner không có quyền sửa DNS (chưa kiểm có bảng DNS riêng). Chỉ lùi lại gắn lại tên miền trong Vibe Host nếu thấy bị gỡ.
- **Báo ai**: người quản trị Vibe Host / chủ tên miền `tinhgon.xyz` ngay. Nếu sau này dùng tên miền riêng: đổi DNS về máy dự phòng (xem phương án B).

## (c) Redis đầy hoặc mất kết nối
Redis giữ hàng đợi tải, bộ đếm hạn mức, công tắc ASR/China, cache cookie. Cấu hình repo: `--appendonly yes`, `volatile-lru`, tối đa 512 MB (`docker-compose.yml`; bản `small` 160 MB). Vibe Host dùng file nào: chưa kiểm.
- **Dấu hiệu**: `/health` có `"redis":{"ok":false}`, hoặc `queue_depth` tăng mãi, `celery.worker_count` = 0 hoặc -1; tải treo ở "đang xếp hàng".
- **Kiểm tra**: (1) mở `/health` ghi lại `redis.ok`, `queue_depth`, `worker_count`. (2) Admin -> **Queue** và **Queue Health**: có job kẹt không (xem `stuck-jobs.md`, `stale-queue.md`). (3) Vibe Host: service Redis còn chạy, bộ nhớ có sát trần không.
- **Xử lý**: Vibe Host -> restart service Redis, rồi restart backend. Dữ liệu hàng đợi tạm có thể mất, người dùng bấm tải lại. Không xoá volume Redis.
- **Báo ai**: restart 1 lần không hết, hoặc mất dữ liệu cấu hình admin -> báo Claude (nhờ khôi phục từ bản sao lưu, xem `continuity-options.md` mục B); kèm ảnh `/health`.

## (d) Đĩa > 90%
- **Dấu hiệu**: `/health` -> `disk.used_pct` > 90 (tự đọc, hoặc nhờ Claude thêm cảnh báo; monitor hiện tại chưa báo đĩa); tải lỗi ghi file.
- **Kiểm tra**: (1) `disk.used_pct`, `free_gb` trong `/health`. (2) Admin -> **Overview** xem số tệp đang giữ. (3) Có tải hàng loạt/ZIP lớn đang chạy không (Admin -> **Queue**). Cơ chế dọn tệp hết hạn: `file-expiry.md` (đọc phần giải thích, bỏ lệnh ssh).
- **Xử lý**: chờ tác vụ dọn tự chạy; Vibe Host -> xem dung lượng đĩa project, tăng gói đĩa nếu có. Nút "dọn ngay" trong Admin: **chưa có** (chưa kiểm) — nhờ Claude chạy dọn từ phía backend.
- **Báo ai**: > 95% hoặc không giảm sau vài giờ -> báo Claude ngay (đĩa đầy làm Redis/ghi tệp hỏng dây chuyền).

## (e) Nhà cung cấp chết (Cobalt, Apify, proxy, Gemini/ASR)
Chi tiết theo nền tảng: `provider-down.md` (phần ssh bỏ qua).
- **Dấu hiệu**: một nhóm nền tảng lỗi cùng lúc; Admin -> **Cobalt** hiện máy đỏ / "Tạm ngắt theo nền tảng"; Admin -> **Phiên âm (ASR)** báo lỗi nhà cung cấp.
- **Kiểm tra**: (1) Admin -> **Cobalt**: máy `dvid-cobalt` và `dvid-cobalt-2` cùng đỏ? (hai máy dùng chung 1 IP ra ngoài nên **không** dự phòng cho nhau khi bị chặn IP). (2) Admin -> **Proxy**: proxy có trả lời không. (3) Admin -> **Chi phí Apify**: hết tiền/token? (4) trang trạng thái nhà cung cấp (apify.com, ai.google.dev) — chưa kiểm địa chỉ.
- **Xử lý**: Cobalt chết: app tự lùi về yt-dlp (chậm hơn, không sập). Apify hết tiền: nạp tiền hoặc đổi token ở Admin -> **Config** (mục Apify token pool; trang ẩn khỏi thanh bên, vào thẳng `/vid-admin/config`). Gemini/ASR lỗi: Admin -> **Phiên âm (ASR)** -> **Tạm dừng phiên âm** để khỏi tốn tiền cho lượt lỗi.
- **Báo ai**: nhà cung cấp đỏ > 2 giờ hoặc ảnh hưởng ≥ 3 nền tảng -> báo Claude; hết tiền/khoá bị thu hồi -> owner xử lý tài khoản.

## (f) Một nền tảng chặn (TikTok, Instagram...)
Chi tiết: `cookie-blocked.md` (phần ssh bỏ qua), `china-platform-rollout.md` cho Douyin/Xiaohongshu/Kuaishou.
- **Dấu hiệu**: một nền tảng lỗi `login required` / 403 / `extraction_timeout`; probe của nền tảng đó đỏ.
- **Kiểm tra**: (1) Admin -> `/vid-admin/probes` ("Sức khoẻ nền tảng", ẩn khỏi thanh bên): đỏ thật hay link probe cũ? (2) **trước khi kết luận bị chặn**, nhớ 13/10: Instagram báo 504 là do host chậm, không phải Instagram chặn — xem mục (a) trước. (3) Admin -> **Cookies**: cookie nền tảng đó còn sống? (4) Admin -> **Proxy**.
- **Xử lý**: nạp cookie mới ở **Cookies** (đăng nhập tài khoản phụ, không dùng tài khoản chính); hoặc chờ vài giờ nếu chặn tạm thời. Không tăng tần suất thử lại.
- **Báo ai**: đỏ > 24 giờ -> ghi vào danh sách "đang hỏng" và báo Claude; không quảng bá nền tảng đang đỏ.

## (g) Chi phí tăng bất thường
- **Dấu hiệu**: cảnh báo Telegram 50%/80% trần (nếu đã bật), hoặc số trong Admin -> **Chi phí Apify** / **Đo lường** vọt so với ngày thường.
- **Kiểm tra**: (1) **Chi phí Apify**: nền tảng nào, hôm nay/tháng này. (2) **Đo lường**: nguồn chi (Apify lớp Trung Quốc, Apify theo dõi kênh, proxy). (3) **Phiên âm (ASR)**: chi hôm nay so với "Trần" hiển thị trên trang. (4) Admin -> **Audit Log**: ai bật gì gần đây.
- **Xử lý (theo mức)**: ASR -> nút **Tạm dừng phiên âm** (Admin -> Phiên âm (ASR); trần đặt bằng env `ASR_SPEND_CEILING_USD`, `ASR_GLOBAL_DAILY_MINUTES_CAP`). Apify theo token -> sửa "Trần chi tiêu tháng (USD)" của từng token ở Admin -> **Config**. Dừng nền tảng Trung Quốc (toàn bộ hoặc từng nền tảng) và dừng Theo dõi kênh: Admin -> **Công tắc khẩn cấp** (có hiệu lực trong vài giây, không cần Redeploy).
- **Báo ai**: chạm 80% trần hoặc chi gấp đôi trung bình -> báo Claude trong ngày; chạm trần -> tạm dừng trước, hỏi sau.

## (h) App Windows lỗi hàng loạt sau phát hành
- **Dấu hiệu**: nhiều người báo app không mở/tải lỗi ngay sau bản mới; số lỗi tăng trong Admin -> **App Windows**.
- **Kiểm tra**: (1) lỗi bắt đầu sau bản nào (`/api/v1/client/version` cho biết bản mới nhất). (2) nếu liên quan yt-dlp: lỗi khớp với lúc bạn duyệt bản yt-dlp mới?
- **Xử lý (đúng thứ tự)**: (1) Admin -> **App Windows** -> mục "Bản yt-dlp cho app": tắt -> app quay về yt-dlp có sẵn khi kiểm tra lần tới. (2) Rút bản app: Admin -> **Công tắc khẩn cấp** -> tắt "Tự cập nhật app Windows" (vài giây, không cần Redeploy) — app ngừng được mời cập nhật, link tải tay giữ nguyên (`docs/desktop/UPDATER.md` §4). Hoặc xoá tệp `.sig` khỏi GitHub Release trên repo `junnyken/vidgrab-releases` (sau ~10 phút trả 204). (3) Máy đã cập nhật: phải cài lại bản cũ bằng tay.
- **Báo ai**: báo Claude để dựng bản sửa; thông báo cho người dùng qua kênh hỗ trợ.

## (i) Lộ bí mật
- **Dấu hiệu**: khoá/token xuất hiện trong ảnh chụp, tin nhắn, repo công khai, hoặc cảnh báo từ GitHub/nhà cung cấp.
- **Kiểm tra**: (1) loại bí mật nào: mật khẩu admin (`ADMIN_PASSWORD`/`ADMIN_TOKEN`), khoá Supabase (`SUPABASE_SERVICE_KEY`), `APIFY_TOKEN`, `GEMINI_API_KEY`, `COBALT_API_KEY`, `TELEGRAM_BOT_TOKEN`, khoá ký lượt tải (k1), khoá ký cập nhật app. (2) Admin -> **Audit Log**: có thao tác lạ không.
- **Xử lý**: thu hồi/đổi ở **nhà cung cấp trước** (Apify, Google, Supabase, BotFather), sau đó cập nhật giá trị mới trên Vibe Host + Redeploy (tăng `VIDGRAB_ENV_REV`). Khoá ký lượt tải: `docs/desktop/P3-KEY-ROTATION.md`. Khoá ký cập nhật app: `docs/desktop/UPDATER.md` §5 (mất/lộ khoá = người dùng phải cài lại tay). Kết quả quét lịch sử git: `docs/security/`. Không dán giá trị bí mật vào chat/ticket.
- **Báo ai**: báo Claude ngay (để đổi và rà soát), kèm *tên* biến bị lộ, không kèm giá trị.

# Phase 34 — Sẵn sàng vận hành và ra mắt thị trường (bản đã đối chiếu code, 13/10/2026)

Nguồn: kế hoạch "Phase 34 — Production Readiness and Market Launch Plan" do owner gửi 13/10/2026, đã sửa 6 chỗ sai so với hệ thống thật và bổ sung 4 chỗ thiếu (mục 9). Owner duyệt bản sửa 13/10. Task AI Factory (project "Triều — VidGrab & AdsCommand"): #6329–#6335.

Thị trường: người dùng tiếng Việt trước, cộng người dùng toàn cầu dán link từ các nền tảng đang hỗ trợ. Không thêm nước/ngôn ngữ mới. Phase này là **ổn định, an toàn, đo lường, ra mắt có kiểm soát** — không thêm tính năng mới (trừ yt-dlp tự cập nhật trong app, xem 34A-3).

## Luật owner (mọi task)

1. Cấu hình vận hành (proxy, cookie, khoá, hạn mức, token nhà cung cấp) chỉnh trong **admin**, không bắt owner đặt biến môi trường trên host.
2. Không đụng luồng đang chạy tốt; mọi thay đổi có công tắc tắt được.
3. Mỗi đợt nêu rõ tiện ích Chrome và app Windows có cần cập nhật không; cần thì cập nhật cùng đợt.
4. Khách tối đa 1080p; tài khoản đăng ký 2K/4K.
5. Không mua chứng chỉ ký mã Windows.
6. Không làm Suno; không thêm nền tảng owner chưa duyệt.
7. Proxy trả phí chỉ dự phòng, không chở dữ liệu video.
8. Mục chưa kiểm thật thì báo "chưa kiểm thật".

## 1. Kết luận hiện tại

Sẵn sàng cho **beta có kiểm soát (G0)**; chưa sẵn sàng quảng bá hay bán Pro. Điểm yếu: một host duy nhất (vibe1 chập chờn sáng 13/10), sức khoẻ nền tảng chưa phủ (11/21 tốt, 3 lỗi, 7 chưa có probe), repo công khai + bí mật cũ trong lịch sử git, chưa đo chi phí/lượt tải, chưa có trang Điều khoản và gỡ nội dung.

## 2. Cổng

| Cổng | Được phép | Điều kiện |
|---|---|---|
| G0 Beta (hiện tại) | Mời vài chục người | — |
| G1 Ra mắt mềm (cộng đồng Việt, không quảng cáo) | Chia sẻ công khai | 34-0 xong; giám sát ngoài + cảnh báo chạy; 21/21 nền tảng có probe, ≤2 lỗi có lý do; quét bí mật xong + đổi khoá k1; có trang Điều khoản + gỡ nội dung; diễn tập khôi phục 1 lần; tiện ích trên Chrome Web Store |
| G2 Tăng trưởng (SEO, nội dung) | Tăng lưu lượng có chủ đích | 14 ngày liên tục uptime ngoài ≥99%; tỷ lệ thành công theo nền tảng đạt mục tiêu; báo cáo chi phí/lượt tải hằng tuần; có phương án dự phòng (34A-B hoặc C) |
| G3 Doanh thu (bán Pro) | Hiện giá, thu tiền | Biết đơn vị kinh tế; chốt đường thanh toán; có quy trình hoàn tiền + hỗ trợ |

## 3. Các mảng việc

### 34-0 Kiểm thật (2–3 ngày) — task #34-0
- Chạy thử luồng chính trên **production bằng tài khoản thử, chỉ đọc** (KHÔNG có staging): tải lẻ trên các nền tảng chính, danh sách chất lượng, ZIP, lịch sử, lịch tải, Theo dõi kênh (TikTok, YouTube), webhook, Hộp thư, đồng bộ app, nút tiện ích.
- Điều tra 3 probe lỗi (Instagram — ưu tiên số 1, Bilibili, Odysee — link probe cần thay): hỏng thật / link probe cũ / chặn tạm thời; kiểm tải Instagram thật có bị ảnh hưởng.
- Xác nhận: Theo dõi kênh TikTok qua proxy dự phòng; Instagram Watch bằng tài khoản Pro; tải cả kênh Xiaohongshu; chia sẻ Android; 2 bước bấm thử chữ ký lượt tải.
- Đầu ra: bảng bằng chứng (luồng, kết quả, ngày, bằng chứng) + danh sách "đang hỏng".

### 34A Ổn định và liên tục
1. **Giám sát ngoài** (task 34A-1): dịch vụ bên ngoài (UptimeRobot / Better Stack bản miễn phí, 5 phút/lần — mỗi phút là trả phí), báo Telegram + email: web, API /health, /client/version, một lượt tải mẫu, Theo dõi kênh. Owner đăng ký tài khoản dịch vụ.
2. **Probe đủ nền tảng** (34A-2): thêm probe 7 nền tảng còn thiếu, thay link Odysee, thêm "kiểm lần cuối" + "giới hạn đã biết". **Probe nền tảng trả phí (Douyin, Xiaohongshu, Kuaishou) chạy thưa (≤1 lần/ngày) với trần chi riêng.** Nền tảng không có probe thì không quảng bá.
3. **Trang trạng thái** (34A-2): trang tĩnh ở **nhà cung cấp khác Vibe Host** (GitHub Pages / Cloudflare Pages), vẫn mở được khi vibe1 chết.
4. **Sao lưu + diễn tập khôi phục** (34A-2): Redis, cấu hình admin, kho cookie ra ngoài host; khôi phục 1 lần vào môi trường sạch, đo thời gian.
5. **Phương án liên tục** (chọn cùng owner): A runbook + giám sát; B máy dự phòng nguội ở **nhà cung cấp khác**; C tự chuyển máy có kiểm sức khoẻ (kiểm giá trước).
6. **Năng lực**: KHÔNG chạy tải gấp 5 lần trên production (không có staging → tự làm sập). Chỉ đo nhẹ ngoài giờ cao điểm; đặt giới hạn đồng thời + thông báo "đang bận, thử lại" thay vì treo.
7. **Chế độ suy giảm** + **runbook sự cố** (host không vào được, DNS, Redis đầy, đĩa >90%, nhà cung cấp chết, nền tảng chặn) — viết để owner tự làm theo trên admin.
8. **yt-dlp tự cập nhật trong app** (34A-3, bổ sung): app tải yt-dlp mới có kiểm chữ ký/checksum mà không cần ra bản app; có đường lùi về bản đi kèm.

### 34B Bảo mật và mã nguồn (task 34B)
1. Quét bí mật **toàn bộ lịch sử cả 2 dòng git** (GitLab + GitHub); bí mật nào từng commit coi như lộ → đổi.
2. **Đổi khoá ký lượt tải k1** — cơ chế nhiều khoá (`kid`, danh sách khoá công khai trong app, `docs/desktop/P3-KEY-ROTATION.md`) **ĐÃ CÓ**: chỉ thực hiện đổi khoá + thời gian chuyển tiếp, không viết mới.
3. Chốt với owner: repo riêng tư, hoặc repo phát hành công khai riêng.
4. Cứng hoá admin, quét phụ thuộc/container, rà chống lạm dụng, quyền riêng tư khớp thực tế.

### 34C Đo lường và đơn vị kinh tế (task 34C)
- **Bổ sung vào cái đã có** (18 sự kiện phễu, `/api/v1/events`, tác vụ thống kê, trang chi phí Apify): tỷ lệ thành công theo nền tảng/ngày, thời gian tới byte đầu, tỷ lệ đăng ký, quay lại ngày 1/7, dùng Theo dõi kênh/Hộp thư/app/tiện ích.
- Chi phí mỗi lượt tải thành công theo nền tảng và theo người dùng (Apify, proxy GB, Cobalt, phiên âm, lưu trữ, phần chia host); báo cáo tuần + top 10 người tốn nhất.
- Trần chi tiêu = 2–3× trung bình tuần đo được; cảnh báo 50% / 80% trần.

### 34D Ra mắt (sau G1) (task 34D-0 cho phần điều kiện G1)
- **Trang Điều khoản + trang gỡ nội dung (takedown) — CHƯA CÓ** (mới có Chính sách riêng tư).
- **Đưa tiện ích lên Chrome Web Store** (owner chưa từ chối; tiện ích 5.5.0 đang chờ) → hết lệch phiên bản, tự cập nhật.
- Trang SEO: hiện có **một trang dùng chung `PlatformLandingPage`**; mở thêm nền tảng chỉ khi probe khoẻ.
- Rà câu chữ (BA) Theo dõi kênh/Hộp thư; hướng dẫn cảnh báo SmartScreen + checksum; FAQ 10 lỗi hay gặp; một kênh hỗ trợ.

### 34E Doanh thu (sau G3) — giữ nguyên bản gốc (giá từ chi phí đo được; VND QR chỉ mở lại khi đủ điều kiện; hoàn tiền; không quảng bá Pro khi 10% người dùng nặng nhất còn lỗ).

### 34F Mở rộng (sau G2) — Theo dõi kênh thêm nền tảng, macOS, Android; mỗi cái có cổng theo số đo 34C.

## 4. Mục tiêu cho G2
Uptime ngoài ≥99%/14 ngày · 21/21 có probe, ≤2 lỗi có lý do · tỷ lệ tải thành công ≥85% · theo dõi thời gian tới byte đầu (cảnh báo khi chậm gấp đôi) · chi phí/lượt tải dưới trần owner duyệt · thời gian khôi phục đo được · phản hồi hỗ trợ trong 24 giờ.

## 5. Luật phát hành
1. Mọi thay đổi có công tắc trong admin. 2. Trước phát hành chạy bộ kiểm 34-0 trên **production chỉ đọc** (chưa có staging; dựng staging là quyết định riêng có chi phí). 3. Ghi chú phát hành nêu ảnh hưởng backend / web / app / tiện ích. 4. Không deploy khi đang có sự cố nền tảng hoặc cảnh báo. 5. Giữ đường lùi 2 bản manifest cập nhật app gần nhất.

## 6. Thứ tự

| Tuần | Việc | Task |
|---|---|---|
| 1 (→17/10) | 34-0 kiểm thật (Instagram trước); quét bí mật lịch sử git; đổi khoá k1; giám sát ngoài | #6329, #6330, #6331 |
| 2 (→24/10) | Probe đủ nền tảng; trang trạng thái nhà cung cấp khác; diễn tập khôi phục; yt-dlp tự cập nhật trong app | #6332, #6333 |
| 3 (→31/10) | Đo lường bổ sung + chi phí/lượt tải; trang Điều khoản + gỡ nội dung; tiện ích lên Chrome Store → đạt G1 | #6334, #6335 |
| 4+ | Ra mắt cộng đồng Việt; xét G2/G3 | — |

## 9. Sửa so với bản gốc (13/10, đối chiếu code)
1. Không có staging → kiểm trên production bằng tài khoản thử, chỉ đọc.
2. Owner **chưa** từ chối Chrome Web Store → đưa lên Store là điều kiện G1.
3. Đánh phiên bản khoá ký đã có → chỉ đổi khoá k1.
4. Đo lường đã có nền (18 sự kiện, events API, trang chi phí Apify) → bổ sung, không dựng lại.
5. 2 máy Cobalt dùng chung 1 IP ra ngoài → chưa phải dự phòng khi bị chặn IP.
6. Probe nền tảng trả phí tốn tiền mỗi lần → chạy thưa, có trần riêng.
Bổ sung: yt-dlp tự cập nhật trong app; dự phòng ở nhà cung cấp khác; giám sát ngoài là dịch vụ ngoài (owner đăng ký, miễn phí 5 phút/lần); runbook cho một người vận hành; trang Điều khoản/gỡ nội dung chưa có; không chạy tải gấp 5 lần trên production.

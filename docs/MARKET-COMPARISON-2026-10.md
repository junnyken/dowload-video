# So sánh thị trường VidGrab — tháng 10/2026

Ngày đọc: **14/10/2026**. Nguồn: trang công khai của đối thủ (WebFetch/WebSearch). Không đăng ký, không chạy thử, không benchmark.

Quy ước:
- **[CT]** = đọc được trên trang chính thức của đối thủ.
- **[3P]** = từ bên thứ ba (reseller, blog, trang so sánh) — độ tin thấp hơn.
- **[SL]** = suy luận của người viết, không phải dữ kiện.
- "không rõ / không công khai" = trang không nói hoặc không đọc được. KHÔNG điền từ trí nhớ.
- Quy đổi VND: **giả định 1 USD ≈ 26.000đ** (không tra tỷ giá hôm nay; chỉ để so cỡ). 49.000đ ≈ 1,9 USD; 399.000đ ≈ 15,3 USD.

Hạn chế khi đọc: `vidjuice.com/unitube-video-downloader/`, `cobalt.tools`, `en.savefrom.net`, `snapinsta.app` không đọc được (chặn/lỗi mạng); `itubego.com/store/`, `uniconverter.wondershare.com/pricing.html`, `/video-downloader/` trả 404; `stacher.io` trả về nội dung rỗng; `apify.com/ayeeyee/video-downloader` 404. Y2mate: không xác nhận được trang chính thức còn hoạt động. Các mục tương ứng ghi "chưa đọc được".

---

## A. Bảng tự động hoá

Ký hiệu: ✅ = trang chính thức nêu rõ; ❌ = trang nêu không có / sản phẩm bản chất không có (có ghi chú); "không rõ" = trang không nói. Số trong ngoặc `[n]` là mã nguồn ở mục E.

| Công cụ | Theo dõi kênh tự tải | Lịch tải | Bắt link clipboard / tự nhận link | Tải hàng loạt / cả kênh | API / webhook | Bot |
|---|---|---|---|---|---|---|
| **VidGrab** (code nội bộ 14/10) | ✅ Pro 30 kênh quét 6h; Free 1 kênh quét 24h | ✅ Free 3, Pro 20 | ✅ tiện ích Chrome (nút trên trang) | ✅ Pro dán 500 link, cả kênh/playlist, ZIP | ✅ API key + webhook | ✅ Telegram |
| 4K Video Downloader Plus [1] | ✅ "Subscribe to playlists and creators for automatic downloads of new uploads" (tần suất không rõ) | không rõ (trang không nhắc) | không rõ (chỉ mô tả copy link rồi dán); có Smart Mode lưu preset | ✅ playlist/kênh/kết quả tìm kiếm 1 click | không rõ | không rõ |
| JDownloader 2 [2][3] | không rõ | ✅ [3P] "scheduled downloads" | ✅ [3P] clipboard observer mặc định bật (tài liệu ĐH Aarhus) | ✅ LinkGrabber | không rõ (có API My.JDownloader, chưa đọc) | không rõ |
| Allavsoft [4] | không rõ | ✅ lập lịch giờ thấp điểm, tự tắt máy sau khi tải xong | không rõ | ✅ thêm nhiều URL; "save all videos from a channel" | không rõ | không rõ |
| VidJuice UniTube [5] | không rõ | không rõ | không rõ | ✅ gói Member: video, playlist, kênh, nhiều URL | không rõ | không rõ |
| iTubeGo [6] | không rõ | không rõ | không rõ | ✅ "bulk or save all videos from a channel", playlist | không rõ | không rõ |
| Wondershare UniConverter (phần tải) | chưa đọc được (trang 404) | chưa đọc được | chưa đọc được | chưa đọc được | chưa đọc được | chưa đọc được |
| Downie (macOS) [7] | không rõ | không rõ | ✅ tiện ích Safari/Chrome/Firefox gửi link vào Downie 1 click; trình duyệt tích hợp dò video | không rõ | không rõ | không rõ |
| Stacher [8] | [3P] "channel/playlist subscriptions" (chỉ rõ lần tải đầu, không rõ định kỳ) | không rõ | không rõ | [3P] có | không rõ | không rõ |
| ClipGrab [9] | không rõ | không rõ | trang không nhắc | không rõ | không rõ | không rõ |
| Video DownloadHelper [10] | không rõ | không rõ | ✅ tự dò video trên trang, hiện icon | không rõ | không rõ | không rõ |
| Snaptube (Android) [11] | không rõ | ❌ trang không nhắc | không rõ (dùng nút Share) | không rõ | không rõ | không rõ |
| TubeMate (Android) [12] | không rõ | ❌ trang không nhắc | không rõ | "smart playlist", tải nền | không rõ | không rõ |
| SSSTik [13] | ❌ | ❌ | ❌ dán từng link | ❌ từng link | ❌ trang không nhắc API | ❌ |
| 9xbuddy [14] | ❌ | ❌ | ❌ | không rõ | không rõ | không rõ |
| Loader.to [15] | ❌ | ❌ | tiện ích trình duyệt (Chrome/Firefox/Edge/Safari) | ✅ cả playlist YouTube → MP3 | trang chủ chỉ trỏ tới "video-download-api.com" (chưa đọc) | không rõ |
| cobalt.tools [16] | ❌ | ❌ | ❌ | không rõ | ✅ API HTTP; instance công cộng không cho dùng trong dự án khác khi chưa được phép | không rõ |
| SaveFrom.net | chưa đọc được | chưa đọc được | chưa đọc được | chưa đọc được | chưa đọc được | chưa đọc được |
| SnapTik / SnapInsta / Y2mate | chưa đọc được trang chính thức (Y2mate: không xác nhận còn sống) | | | | | |

Ghi chú A:
- Chỉ **4K Video Downloader Plus** được xác nhận trên trang chính thức có "tự tải video mới của kênh/playlist theo dõi". Không đối thủ nào đọc được ghi rõ tần suất quét. [SL] Mức "6 giờ/lần + webhook + hộp thư" của VidGrab hiện không thấy đối thủ nào công bố tương đương — nhưng điều này chỉ đúng trong phạm vi đã đọc, không loại trừ tính năng chưa được nêu trên trang.
- Mọi ô "không rõ" nghĩa là trang tôi đọc không nhắc; tính năng có thể tồn tại trong sản phẩm.

---

## B. Bảng giá

| Công cụ | Gói miễn phí giới hạn gì | Gói trả phí (giá, kỳ hạn, mở khoá) | Nguồn, ngày đọc |
|---|---|---|---|
| **VidGrab** | Khách 5 lượt/ngày, 1080p; Free (tài khoản) 20/ngày, đến 8K; theo dõi 1 kênh/24h | Pro 49.000đ/tháng hoặc 399.000đ/năm (≈1,9 USD/tháng; ≈15,3 USD/năm): 100 lượt/ngày, ZIP, 500 link, kênh/playlist, 30 kênh/6h, webhook, API 3.000 lượt/tháng. Team: API 10.000/tháng (giá không nêu trong brief) | Nội bộ 14/10/2026 |
| 4K Video Downloader Plus | Starter miễn phí vĩnh viễn, có giới hạn số lượng (không nêu số); dùng thử 7 ngày tính năng trả phí | Lite 15 USD/năm (≈390.000đ); Personal 25 USD trọn đời (≈650.000đ); Pro 45 USD trọn đời (giá niêm yết 60 USD; ≈1,17 triệu đ), có giấy phép thương mại. Trang tự mâu thuẫn về "một lần" vs "năm" | [1] 14/10/2026 |
| JDownloader 2 | Trang chủ không nêu giá/gói (đọc được chỉ là trang tải) | không công khai trên trang đã đọc | [2] 14/10/2026 |
| Allavsoft | Có bản dùng thử (không nêu giới hạn) | 29,99 USD giảm còn 19,99 USD (≈520.000đ); không nêu kỳ hạn/số máy trên trang; hoàn tiền 30 ngày | [4] 14/10/2026 |
| VidJuice UniTube | Trial 0 USD: 10 video, 3 playlist, nhiều-URL 3 lần | Member: giá không hiện trên trang chính thức; có gói tháng (tự gia hạn) và năm; hoàn tiền 30 ngày. [3P] 9,95 USD/tháng (≈259.000đ), 19,95 USD/năm, 29,95 USD trọn đời 1 máy, Family 39,95 USD 5 máy | [5][5b] 14/10/2026; [3P] tekpon/reseller (WebSearch) |
| iTubeGo | Có bản dùng thử (không nêu giới hạn) | Trang chủ không có bảng giá; trang /store/ 404 → chưa đọc được | [6] 14/10/2026 |
| Wondershare UniConverter | chưa đọc được (trang 404) | chưa đọc được | 14/10/2026 |
| Downie (macOS) | không nêu | 19,99 USD trọn đời, tối đa 3 máy kích hoạt (≈520.000đ, chưa gồm thuế); Setapp 14,99 USD/tháng (gồm 300+ app) | [7] 14/10/2026 |
| Stacher | không rõ | [3P] AlternativeTo: freemium, ≈7 USD/tháng — chưa xác nhận trên trang chính thức | WebSearch 14/10/2026 |
| ClipGrab | Miễn phí (nhận donate) | Không có | [9] 14/10/2026 |
| Video DownloadHelper | Miễn phí không hết hạn, không cần đăng ký | Premium: 9 USD/năm (≈234.000đ) hoặc 17 USD trọn đời (≈442.000đ); mở khoá: tải không giới hạn, tốc độ/chất lượng tối đa, phụ đề; dùng mọi trình duyệt/thiết bị của chủ | [10][10b] 14/10/2026 |
| Snaptube | "Free … no limits" theo trang | không nêu giá | [11] 14/10/2026 |
| TubeMate | Trang không nêu giá | — | [12] 14/10/2026 |
| SSSTik | "Without any limits"; chỉ video công khai; trang không nhắc quảng cáo | không có | [13] 14/10/2026 |
| 9xbuddy | Miễn phí, nhờ quảng cáo; không nêu giới hạn | không có | [14] 14/10/2026 |
| Loader.to | Miễn phí, không đăng nhập, không nêu giới hạn playlist | không rõ | [15] 14/10/2026 |
| cobalt.tools | Miễn phí (mã nguồn mở, API có rate limit) | tự host | [16b] 14/10/2026 |
| SaveFrom, SnapTik, SnapInsta, Y2mate | chưa đọc được trang chính thức | | |

[SL] Về tỷ giá: các mức quy đổi trên dùng 26.000đ/USD, chưa gồm thuế/VAT và chưa tính phí thanh toán quốc tế.

---

## C. API thị trường so với VidGrab Pro (3.000 lượt/tháng)

| Dịch vụ | Quota/tháng | Giá | Overage | Nguồn, ngày |
|---|---|---|---|---|
| **VidGrab Pro / Team** | 3.000 / 10.000 lượt | nằm trong Pro 49.000đ/tháng (Team: giá không nêu) | không nêu | nội bộ 14/10 |
| RapidAPI "TikTok Downloader – without watermark" (mirzahadjaevaguzal) | Trang chính thức có gói/quota nhưng bản đọc không hiện số | không đọc được | không đọc được | [17] |
| RapidAPI "Tiktok89" (iq.faceok) | Free 500; gói trả phí đến 1.500.000 | 10 / 20 / 30 USD/tháng (≈260k / 520k / 780k đ) | không rõ | [18] qua WebSearch, 14/10 |
| RapidAPI TikTok downloader khác (tóm tắt từ WebSearch, không rõ listing) | Basic 1.000 (cứng); Pro 1 triệu; Ultra 3 triệu; Mega 7 triệu | 0 / 10 / 25 / 50 USD/tháng | 0,00002 → 0,000007 USD/request | WebSearch 14/10 — [3P], chưa xác nhận tên API |
| RapidAPI "Easy Instagram Service" (ariefsam) | Free 5.000; Pro 14.000; gói lớn 200.000 | 0 / 20 / 80 USD/tháng | không rõ | [19] qua WebSearch |
| Zyla API Hub (TikTok Collector) | 12.000 hoặc 24.000 lượt, trần 400/800 mỗi ngày | giá không đọc được | không rollover | [20] qua WebSearch |
| Apify (nền tảng) | Free tặng 5 USD/tháng credit; Starter 19 USD (17 nếu trả năm); Scale 199 USD (179 năm) | tính theo credit | trả thêm vào hoá đơn kỳ sau; Free thì bị chặn | [21] 14/10 |
| Apify actors tải video (cộng đồng) | tính theo kết quả | ≈3–5 USD/1.000 kết quả (video-downloader, neuro-prox); Instagram downloader từ 0,48 USD/1.000 request | tuỳ actor | [22] qua WebSearch — [3P], nhà phát triển nhỏ |
| cobalt API | không có gói thương mại; có rate limit | miễn phí tự host; instance công cộng không cho dự án khác dùng khi chưa xin phép | | [16b] |
| Loader.to / video-download-api.com | chưa đọc | chưa đọc | | [15] |

Nhận xét C:
- Đọc được: quota 500–14.000 là phổ biến ở gói rẻ trên RapidAPI/Zyla; gói trả phí 10–30 USD/tháng cho ra hàng triệu request ở một số listing TikTok.
- [SL] 3.000 lượt/tháng kèm Pro 49.000đ (≈1,9 USD) là **ít hơn nhiều** quota API thuần của các listing TikTok nêu trên (hàng trăm nghìn tới triệu), nhưng VidGrab là gói gộp (app + extension + bot) chứ không bán API riêng; các API kia thường chỉ trả metadata/link, chưa rõ có xử lý đa nền tảng như VidGrab hay không.
- Giá/1.000 lượt: Apify actor ≈3–5 USD. 3.000 lượt ≈ 9–15 USD theo mức đó (≈234.000–390.000đ), cao hơn nhiều so với 49.000đ — [SL] nghĩa là quota 3.000 lượt với giá 49.000đ là hợp lý, nhưng chỉ nếu một lượt gọi VidGrab tương đương một "kết quả" của actor.

---

## D. Nhận xét

### D1. VidGrab hơn / thiếu về tự động hoá (trong phạm vi đã đọc)

Hơn [SL, dựa trên A]:
- Đồng thời có theo dõi kênh (30 kênh, 6h/lần), webhook, hộp thư, lịch tải, bot Telegram, tiện ích Chrome và app Windows tự cập nhật. Không đối thủ nào đọc được liệt kê đủ bộ này; 4K Video Downloader Plus là đối thủ duy nhất xác nhận "tự tải video mới".
- Phủ nguồn Trung Quốc (Douyin, Xiaohongshu, Bilibili) và TikTok không logo trong một sản phẩm; đối thủ phần mềm đọc được chỉ nêu Bilibili (4K) hoặc "1000+ site" chung chung.
- API + webhook gói chung với app; cobalt có API nhưng instance công cộng cấm dùng ngoài.

Thiếu / yếu hơn:
- Xác nhận được **JDownloader** (3P) có clipboard observer mặc định; **Smart Mode** của 4K lưu preset một lần. VidGrab chưa nêu clipboard monitor ở app Windows hay preset "một lần cho mọi tải" trong brief.
- Allavsoft có **lập lịch giờ thấp điểm + tự tắt máy**; VidGrab có lịch tải nhưng brief không nêu "tắt máy sau khi xong".
- Các app ngoại như VidJuice nêu lưu cloud 100 GB và đồng bộ Dropbox/Google Drive (Member) — VidGrab brief không nêu đồng bộ cloud.
- Downie: đồng bộ lịch sử qua iCloud để tránh tải trùng — VidGrab chưa nêu chống trùng liên thiết bị.
- Chưa có app macOS/mobile (brief không nêu); Snaptube/TubeMate là Android.

### D2. Giá 49.000đ/tháng đứng ở đâu

Số liệu quy đổi (26.000đ/USD, xem giả định ở đầu):

| Mức | Quy đổi | Ghi chú |
|---|---|---|
| VidGrab Pro tháng | 49.000đ ≈ 1,9 USD/tháng | |
| VidGrab Pro năm | 399.000đ ≈ 15,3 USD/năm | gần bằng 4K Lite 15 USD/năm |
| VidJuice tháng [3P] | 9,95 USD ≈ 259.000đ | gấp ≈5 lần |
| Stacher [3P] | 7 USD/tháng ≈ 182.000đ | gấp ≈3,7 lần |
| Setapp (có Downie) | 14,99 USD/tháng ≈ 390.000đ | gói gộp 300+ app |
| 4K Lite | 15 USD/năm ≈ 390.000đ | VidGrab năm rẻ hơn chút |
| DownloadHelper Premium | 9 USD/năm ≈ 234.000đ | rẻ hơn VidGrab năm; nhưng chỉ là tiện ích trình duyệt |
| Apify Starter | 19 USD/tháng ≈ 494.000đ | nền tảng dev |

Kết luận:
- **Gói tháng**: [SL] rẻ rõ so với các gói tháng đọc được (VidJuice, Stacher, Setapp).
- **Gói năm 399.000đ**: [SL] ngang mức trung bình; không rẻ hơn 4K Lite hay DownloadHelper nhiều, và các app đó có bản trọn đời (4K 25–45 USD, Downie 19,99, DownloadHelper 17, Allavsoft 19,99).
- **Đứng dưới** mọi công cụ miễn phí (SSSTik, Snaptube, Loader.to, ClipGrab, cobalt) cho nhu cầu tải lẻ — đó là mức so sánh khó thắng bằng giá, chỉ thắng bằng tự động hoá.
- Lưu ý: người dùng Việt thường quen "trọn đời/crack" hoặc miễn phí; chuyển khoản thủ công là rào cản chuyển đổi so với thẻ quốc tế [SL].

### D3. 5 đề xuất tự động hoá đáng làm tiếp (xếp theo giá trị cho người Việt)

| # | Đề xuất | Đối thủ đang có (đọc được) | Vì sao đáng làm [SL] |
|---|---|---|---|
| 1 | Bắt link từ clipboard trong app Windows (tự thêm vào hàng chờ, bật mặc định, có nút tắt) | JDownloader (3P) [3] | Người làm nội dung dán nhiều link TikTok/Douyin liên tục; bỏ được 1 bước |
| 2 | "Smart Mode / preset": chọn chất lượng, định dạng, thư mục, phụ đề một lần cho mọi lần tải; gắn preset theo kênh theo dõi | 4K Video Downloader Plus [1]; Downie post-processing [7] | Tăng giá trị gói Free→Pro; phù hợp quy trình lặp |
| 3 | Lịch tải giờ thấp điểm + tự tắt máy khi xong (ở app Windows) | Allavsoft [4]; JDownloader (3P) | Mạng gia đình/văn phòng VN hay giới hạn giờ; dễ làm trên app |
| 4 | Đồng bộ đích tải sang Google Drive/Dropbox, chống tải trùng khi theo dõi kênh | VidJuice (Member: 100 GB cloud, Dropbox/Drive) [5b]; Downie (iCloud dedupe) [7] | Team/agency cần gom video; bổ trợ cho theo dõi kênh |
| 5 | Mở rộng thanh toán (QR/VietQR tự kích hoạt) và gói trọn đời/“Team” rõ giá | 4K (Personal/Pro trọn đời) [1]; Downie 19,99 USD [7]; DownloadHelper 17 USD [10b] | Không phải tự động hoá tải, nhưng chuyển khoản + kích hoạt thủ công là điểm nghẽn doanh thu; đây là [SL], ít bằng chứng đối thủ |

Ghi chú: cần xác minh sau (chưa đọc được): SaveFrom, SnapTik/SnapInsta, UniConverter, iTubeGo pricing, JDownloader wiki chính thức, đối thủ Việt Nam có trả phí hay không. Tìm tiếng Việt chỉ thấy blog hướng dẫn SnapTik/SSSTik/Snapdouyin/DLPanda và bot Telegram miễn phí; không tìm thấy công cụ Việt có theo dõi kênh tự tải.

---

## E. Nguồn (đã đọc 14/10/2026)

Đọc được:
- [1] https://www.4kdownload.com/products/videodownloader-plus
- [2] https://jdownloader.org/jdownloader2
- [3] [3P] https://cc.au.dk/cdmm/tools-and-tutorials/data-collection/jdownloader-2 (qua WebSearch)
- [4] https://www.allavsoft.com/
- [5] https://www.vidjuice.com/ ; [5b] https://www.vidjuice.com/pricing/ ; [3P giá] https://tekpon.com/software/vidjuice-unitube/reviews/ và các trang reseller qua WebSearch
- [6] https://www.itubego.com/
- [7] https://software.charliemonroe.net/downie/
- [8] [3P] https://alternativeto.net/software/stacher/about (qua WebSearch; stacher.io không đọc được)
- [9] http://clipgrab.org/
- [10] https://www.downloadhelper.net/ ; [10b] https://www.downloadhelper.net/premium
- [11] https://www.snaptube.com/en
- [12] https://www.tubemate.net/
- [13] https://ssstik.io/en
- [14] https://9xbuddy.com/
- [15] https://en.loader.to/4/
- [16] https://cobalt.tools/about (không đọc được, không dùng); [16b] https://github.com/imputnet/cobalt/blob/main/docs/api.md
- [17] https://rapidapi.com/uk/mirzahadjaevaguzal/api/tiktok-downloader-download-videos-without-watermark1/pricing (không hiện số)
- [18] https://rapidapi.com/zh/iq.faceok/api/tiktok89/pricing (qua WebSearch)
- [19] https://rapidapi.com/de/ariefsam/api/easy-instagram-service/pricing (qua WebSearch)
- [20] https://zylalabs.com/id/3092 (qua WebSearch)
- [21] https://apify.com/pricing
- [22] https://apify.com/ayeeyee/video-downloader (404 khi fetch; số liệu từ WebSearch), https://apify.com/cap-capibara/neuroprox-video-extractor, https://apify.com/snapinsta/instagram-downloader-api (qua WebSearch)
- Tiếng Việt (3P): https://dienthoaivui.com.vn/snaptik-douyin , https://dienthoaivui.com.vn/y2mate-com-la-gi

Không đọc được / 404: vidjuice.com/unitube-video-downloader, cobalt.tools, en.savefrom.net, snapinsta.app, itubego.com/store/, uniconverter.wondershare.com (pricing, video-downloader), stacher.io, apify.com/ayeeyee/video-downloader.

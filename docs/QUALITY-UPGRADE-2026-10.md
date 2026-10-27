# Nâng cấp chất lượng đầu ra của VidGrab — tháng 10/2026

Ngày đọc nguồn công khai: **27/10/2026**. Mã nguồn đối chiếu: commit `3a1aa99` (nhánh main).
Người đọc: chủ sản phẩm (không phải kỹ sư) và đội dev.
Phạm vi: **chất lượng file tải về** (độ nét, codec, âm thanh, phụ đề, tên file, thông tin gắn kèm). Phần tự động hoá và bảng giá đã có ở `docs/MARKET-COMPARISON-2026-10.md` nên không lặp lại.

## Cách đọc tài liệu này

- **[MÃ]** = đọc trực tiếp từ mã nguồn VidGrab, có ghi `tệp:dòng`.
- **[ĐO]** = tôi chạy thử trên máy, không dùng mạng, không dùng dịch vụ của ai (xem mục "Cách đo" ngay dưới).
- **[CT]** = đọc trên trang chính thức của đối thủ (có URL ở mục F).
- **[3P]** = nguồn bên thứ ba (blog, trang tổng hợp, kết quả tìm kiếm). Độ tin thấp hơn.
- **[SL]** = suy luận của người viết, **không phải dữ kiện**.
- "không rõ" = trang đã đọc không nói gì về mục đó (không có nghĩa là sản phẩm không có). "chưa đọc được" = trang không mở được.
- Không có số liệu nào được điền từ trí nhớ. Không đăng ký, không tải thử, không dùng dịch vụ của đối thủ.

**Cách đo [ĐO].** Tôi lấy đúng chuỗi chọn định dạng (format selector) mà mã đang dùng, đưa cho thư viện yt-dlp trong `backend/venv` (bản 2026.04.30) chọn trên **một danh sách định dạng giả lập kiểu YouTube** (H.264 chỉ tới 1080p; VP9 và AV1 lên tới 2160p). Đây là mô phỏng, **chưa phải đo trên YouTube thật**. Kết quả nêu rõ ở mục A.

---

## Tóm tắt cho chủ sản phẩm (đọc mục này là đủ)

1. **Lỗi lớn nhất:** khi người dùng chọn 1440p hoặc 4K trên YouTube, bộ chọn định dạng của máy chủ ưu tiên "H.264" trước, mà YouTube chỉ có H.264 tới 1080p. Mô phỏng cho thấy cả bốn lựa chọn `video_1440`, `video_2160`, `video_4k` đều ra **1080p** (mục A, dòng 1). Nếu đúng với YouTube thật, thì thứ ta bán cho gói Pro ("tới 2160p") đang không giao đúng trên YouTube. Cần đo thật trước khi sửa (mục E.1).
2. **Tên file mất chữ tiếng Việt:** đường tải qua máy chủ bỏ dấu bằng cách xoá ký tự không phải ASCII, nên "Đường về nhà đẹp" thành `uong-ve-nha-ep` (chữ Đ/đ bị mất hẳn, không phải bỏ dấu). [ĐO] Đây là lỗi nhỏ, sửa rẻ, người Việt thấy ngay.
3. **Không gắn thông tin vào file:** không có đoạn mã nào gắn tên bài, ảnh bìa, chương vào file tải về (web và app). Nhiều đối thủ có (Seal, Allavsoft).
4. **Điểm mạnh thật, nên quảng bá:** bỏ logo TikTok/Douyin bằng cách **lấy nguồn không logo, không nén lại video** (nên không giảm chất lượng); xử lý video dọc đúng ("1080p dọc" ra 1080x1920); mặc định H.264 nên mở được trên mọi máy; có dịch phụ đề và tạo phụ đề tự động (đọc được trang đối thủ không thấy mục này).
5. **App Windows chưa có lựa chọn codec** và không tải phụ đề. Trên YouTube, app có xu hướng ra file VP9/AV1 (không phải H.264) [ĐO, danh sách giả lập], mở được trên VLC nhưng có thể không mở trên trình phát mặc định của một số máy [SL].

---

## A. "VidGrab hôm nay" — từng chiều chất lượng

| # | Chiều chất lượng | Mã làm gì hôm nay | Điểm yếu |
|---|---|---|---|
| 1 | **Độ nét tối đa, web/máy chủ (YouTube và các trang dùng yt-dlp)** | Mỗi mức chất lượng là một chuỗi ưu tiên: `video_4k` ưu tiên "H.264 + AAC" rồi mới tới mp4/webm (`backend/app/services/downloader.py:1058-1069`); `video_<N>` có nhánh "dọc trước" rồi "H.264 ở chiều cao ≤ N" (`downloader.py:1070-1116`); mặc định "HD" bị chặn ở 1080p (`downloader.py:1135-1161`). Gói Free bị cap 1080p (`backend/app/core/quotas.py:49`); YouTube: khách 720p, người dùng 1080p theo biến môi trường (`backend/app/core/plans.py:143-145`). | **[ĐO]** Trên danh sách giả lập kiểu YouTube (H.264 tới 1080p, VP9/AV1 tới 2160p): `video`, `video_1080`, `video_1440`, `video_2160`, `video_4k` đều chọn **cùng một luồng: 1080p H.264** (format 137 + 140). Lý do: nhánh "H.264 trước" khớp được ở 1080p nên chuỗi không bao giờ đi tới nhánh 2160p. Với Bilibili thì không bị, vì nó có chuỗi riêng không ép codec (`backend/app/services/bilibili_extractor.py:83-91`). Cần đo trên YouTube thật. Ngoài ra có đường dự phòng Cobalt, chưa xét ở đây. |
| 2 | **Codec (H.264/VP9/AV1)** | Ưu tiên H.264 + AAC để mở được trên QuickTime/iPhone/Windows (`downloader.py:1060-1062`, `1132-1134`). Danh sách định dạng xếp H.264 trước VP9 trước AV1 (`downloader.py:1618-1628`) và đánh dấu "universal" cho H.264 (`downloader.py:1645`). Kết quả trả về có codec thật của file (`downloader.py:3974-3981`). | Người dùng **không chọn được** codec. Không có chuyển mã sang H.264 khi nguồn chỉ có VP9/AV1: file VP9 vẫn được ghép vào .mp4 (`downloader.py:1188`, `merge_output_format = "mp4"`). Không có H.265. |
| 3 | **HDR và 60 fps** | Không thấy đoạn mã nào xử lý HDR hay fps (tìm "hdr", "fps" trong `downloader.py`: không có). Riêng YouTube đặt thứ tự ưu tiên: độ phân giải, rồi ext mp4/m4a, rồi bitrate (`downloader.py:1304`). | Không điều khiển được, không hiển thị trong giao diện web. Có 60 fps hay không chỉ do thứ tự mặc định quyết định [SL]. |
| 4 | **Âm thanh (web)** | MP3: chỉ có 128 hoặc 320 kbps (`downloader.py:1197-1202`), lấy nguồn m4a rồi mã hoá lại thành MP3 (`downloader.py:1119`). Các định dạng `audio_*` khác cố định 128 (`downloader.py:1203-1210`). Có đường riêng `/process/extract-audio`: MP3/M4A, 128/192/320, có chuẩn hoá âm lượng `loudnorm` (`backend/app/api/processing.py:328-368`). | Nguồn AAC rồi nén lại MP3 là "nén hai lần" [SL]. Nút "ogg" của web thực ra gọi MP3 128 (`frontend/src/components/DashboardContent.jsx:1067`, `param === 'ogg' ? 'mp3_128'`). Nút "webm" gọi `audio_webm`, nhưng yt-dlp không hỗ trợ "webm" làm định dạng âm thanh (danh sách hỗ trợ [ĐO]: aac, alac, flac, m4a, mp3, opus, vorbis, wav), nên nhiều khả năng lỗi [SL]. Chuẩn hoá âm lượng chỉ có ở đường riêng, không có ở luồng tải chính. |
| 5 | **Container và ghép** | Luôn ghép ra `.mp4` bằng yt-dlp (`downloader.py:1187-1195`, thêm `FFmpegVideoConvertor` cho `video_<N>`). | `FFmpegVideoConvertor` chỉ thật sự nén lại nếu file chưa phải mp4; nhưng vì đã ép ghép ra mp4 nên thường là bước không làm gì [SL]. Không có lựa chọn MKV. |
| 6 | **Bỏ logo TikTok/Douyin** | **Lấy nguồn không logo, không nén lại.** Douyin: đổi `/playwm/` thành `/play/` (`backend/app/services/douyin_extractor.py:339`, `603`; tiện ích `chrome-extension/content.js:379`). TikTok: gọi TikWM lấy `hdplay`, rồi `play`, rồi `wmplay` (`douyin_extractor.py:482`; `downloader.py:2408-2460`); dự phòng yt-dlp loại định dạng có chữ "watermark" (`downloader.py:1406`). | Kích thước thật của file TikWM không biết lúc trả danh sách (mã tự ghi rõ, `downloader.py:2420-2424`), chỉ đo sau bằng `ffprobe` (`backend/app/services/format_probe.py:1-12`). Chưa có số đo "TikWM hdplay độ nét bao nhiêu" trong repo. |
| 7 | **Douyin: chọn luồng nào** | Mặc định lấy `play_addr`; chỉ khi chọn `video_4k` mới chọn luồng có `bit_rate` và chiều cao lớn nhất (`douyin_extractor.py:973-987`). Tiện ích Chrome lấy link đầu tiên của `play_addr` (`chrome-extension/content.js:388`). App Windows gom cả `play_addr` và `play_addr_h264`, lấy link đầu tiên không chứa `/playwm/` (`desktop/src-tauri/src/douyin_local.rs:152-157`). | Không ai trong ba nơi này so sánh luồng nào nét hơn, trừ nhánh `video_4k` của máy chủ. Không rõ `play_addr` thấp hơn bao nhiêu so với `bit_rate` cao nhất: **chưa đo trên video thật**. |
| 8 | **Xoá logo trong khung hình (video AI như Flow/Veo)** | Chỉ cho Pro (`backend/app/api/flow_cleanup.py:40`, `318`). Có 5 cách: `natural` (mặc định, vá theo thời gian), `telea`, `telea_soft`, `crop`, `blur` (`flow_cleanup.py:564-571`). Video đầu ra nén lại bằng libx264 CRF 18, yuv420p (`flow_cleanup.py:254-256`, `backend/app/api/flow_inpaint.py:359`). | Không phải đường tải TikTok. Chất lượng "chưa chỉnh trên dữ liệu thật" (`docs/PRODUCT-STATUS.md` điểm yếu số 3; đã nằm trong kế hoạch Q4 mục P3). |
| 9 | **Phụ đề** | Tải kèm phụ đề tự động hoặc có sẵn, ưu tiên vi rồi en (`downloader.py:3207-3213`; bảng ngôn ngữ `backend/app/api/routes.py:470-475`). Ba chế độ: file rời, **đốt cứng** (libx264 CRF 23, giữ nguyên âm thanh, `routes.py:488-503`) hoặc **nhúng mềm vào MKV** (`routes.py:505-515`). Riêng: dịch phụ đề SRT/VTT bằng LLM, giữ mốc thời gian, sửa tay, đốt vào video; tạo phụ đề bằng Whisper, tối đa 45 phút (`docs/FEATURE_SPEC_transcript_translate.md` mục 1, `backend/app/api/transcript_translate.py:198`, `487`). | Định dạng file phụ đề không được chỉ định ở luồng tải chính (lấy mặc định của yt-dlp) [SL]. Nhúng mềm chỉ ra MKV, không ra MP4. Đốt cứng nén lại video (CRF 23 thấp hơn chất lượng file gốc đã nén). `docs/FEATURE_SPEC` nói tính năng dịch/ASR là công cụ riêng (tải lên, hoặc chọn video đã tải), chưa nối vào lúc bấm tải. App Windows: **không có tuỳ chọn phụ đề** (tìm trong `desktop/src-tauri/src/engine.rs`: không có). |
| 10 | **Thông tin gắn vào file (tên bài, ảnh bìa, chương)** | Không có. Tìm khắp backend, app, tiện ích: không có `EmbedThumbnail`, `FFmpegMetadata`, `--embed-*`, `--embed-chapters`. Có bộ sinh thẻ MP3 (`backend/app/services/smart_metadata.py:279`) nhưng chỉ được gọi từ tác vụ phân tích (`backend/app/tasks/analysis_tasks.py:257-267`), không ghi vào file tải. Chương (chapters) chỉ trả về để hiển thị (`downloader.py:3985-3995`). | File MP3 tải về không có ảnh bìa và có thể không có tên bài/nghệ sĩ [SL: chưa mở thử một file MP3 thật]. |
| 11 | **Tên file** | Máy chủ đặt tên lưu bằng chuỗi ngẫu nhiên (không lộ tên video, `downloader.py:521-534`). Tên hiển thị cho người dùng qua đường tải (`proxy_download`, `routes.py:2587-2606`) bị chuyển sang ASCII: **chữ Đ/đ rơi mất hẳn** [ĐO: "Đường về nhà đẹp" thành `uong-ve-nha-ep`]. Cùng cách bỏ dấu lặp lại ở `routes.py:1836`, `2016`, `processing.py:90-94`. App Windows giữ tiếng Việt có dấu, cắt 150 byte, kèm mã video, có 3 mẫu (tiêu đề / ngày + tiêu đề / ngày + kênh + tiêu đề) và 4 kiểu thư mục (`desktop/src-tauri/src/engine.rs:18-36`, `60-80`). | Web: tên mất nghĩa. App: tốt hơn, chưa có token độ phân giải trong tên mẫu [SL: chưa thấy]. |
| 12 | **App Windows: chọn chất lượng** | Preset: 2160, 1440, 1080, 720, 480, 360 (`desktop/src-tauri/src/formats.rs:41`), selector `bv*[height<=N]+ba/b[height<=N]/b` (`formats.rs:47`), không ép codec; luôn ghép `mp4`, nếu không được thì `mkv` (`engine.rs:187-188`). Khách chưa đăng nhập bị chặn ở 1080p tính theo cạnh ngắn (`desktop/src/lib/quality.ts:9-12`). Danh sách thô còn có fps, codec, dung lượng cho từng định dạng (`formats.rs:12-26`). | **[ĐO]** Trên danh sách giả lập: "2160" ra AV1 + Opus, "1080" ra VP9 (webm) + Opus, vì yt-dlp mặc định ưu tiên codec mới. Tức là app **không** mặc định "dễ mở" như web. Mã giữ nhãn "Tốt nhất" mà không chọn gì (`engine.rs:197-206`). |
| 13 | **App: âm thanh** | Hai mức: M4A (copy nguyên luồng nếu nguồn đã là AAC) hoặc MP3 VBR chất lượng 0 (`engine.rs:49-60`, `208-217`; `formats.rs:66-67`). | Không có chọn bitrate. M4A copy là điểm tốt. MP3 VBR 0 cho chất lượng cao nhưng người dùng không biết là bao nhiêu kbps. |
| 14 | **Tiện ích Chrome** | Tải trực tiếp trong trình duyệt **chỉ** cho chất lượng "video/best" (`chrome-extension/direct-download.js:47-53`); không dùng cho YouTube/TikTok/Spotify/SoundCloud (`direct-download.js:38`). Facebook lấy bản HD có sẵn (`direct-download.js:69`). Danh sách phát HLS: chọn luồng cao nhất (`chrome-extension/hls-downloader.js:132`), TS đổi sang MP4 **không nén lại** bằng mux.js (`hls-downloader.js:154-170`); bỏ qua video mã hoá và video có âm thanh rời (`hls-downloader.js:83`, `134-137`). Chọn file lớn nhất trong danh sách bắt được (`direct-download.js`, hàm `pickSniffed`). | Chọn "file lớn nhất" không bằng "nét nhất" (có thể là bản dài hơn hoặc bitrate cao hơn). Chọn chiều cao cụ thể, MP3, phụ đề thì chuyển sang máy chủ. |
| 15 | **Tốc độ** | yt-dlp tải 4 mảnh song song (`downloader.py:1176`); HLS trong tiện ích 6 mảnh (`hls-downloader.js:23`). | Không có tải nhiều kết nối cho file đơn như aria2c (Seal có, xem mục B). Không ảnh hưởng chất lượng. |

**Hàm ý quan trọng cho truyền thông:** vì VidGrab bỏ logo bằng "lấy nguồn không logo" chứ không "vá lại hình", video TikTok/Douyin **không bị nén lại** ở bước đó. Đây là điểm bán đáng nói rõ (xem mục C).

---

## B. So sánh với thị trường (chỉ đọc trang công khai)

Quy ước ô: ✅ trang nói có; ❌ trang nói không có; "không rõ" = trang đã đọc không nhắc. Số trong ngoặc là mã nguồn ở mục F.

| Công cụ | Độ nét tối đa | HDR / fps | Codec | Âm thanh | Không logo | Phụ đề | Thông tin gắn kèm | AI |
|---|---|---|---|---|---|---|---|---|
| **VidGrab** [MÃ] | Free 1080p, Pro 2160p theo giới hạn; YouTube thực tế có thể chỉ 1080p (mục A.1) | ❌ không có mã | Ưu tiên H.264, không cho chọn | MP3 128/320; M4A (app) | ✅ nguồn không logo (TikTok, Douyin) | Tải, đốt, nhúng MKV; dịch LLM; Whisper | ❌ | Dịch phụ đề, Whisper; xoá logo khung hình |
| 4K Video Downloader Plus | ✅ "720p, 1080p, 4K UHD, and even 8K UHD" [CT][1] | HDR, fps: trang không nhắc [CT][1] | Có tuỳ chọn AV1/VP9 (3P: ghi chú phát hành) [3P][4] ; Smart Mode "respects whichever codec you've selected" [CT][3] | MP3, M4A, OGG [CT][1]; bitrate không rõ | không rõ | SRT, "over 50 languages", cả playlist/kênh [CT][1]; tự dịch: không rõ | "metadata, thumbnails, chapters": trang không nhắc [CT][1] | 4 công cụ **âm thanh** AI: tách giọng, bỏ giọng, tách tiếng nói, giảm vang [CT][2]; upscale/phiên âm/dịch: không có trong bài [CT][2] |
| Allavsoft | ✅ "8K, ultra high definition (4K)" ; muốn 4K YouTube phải bật AV1 trong Preferences [CT][5] | HDR, 60 fps: không rõ [CT][5] | Chỉ nhắc AV1 [CT][5] | Chuyển MP3, WAV, AAC, FLAC, OGG, AIFF [CT][5]; bitrate không rõ | không rõ | "subtitles" , chi tiết không rõ [CT][5] | ✅ "chapter preservation"; thẻ ID3 cho Spotify [CT][5] | không rõ |
| VidJuice UniTube | ✅ "8K/4K/2K/1080p/720p" ; "4K, 8K, HDR video formats" [CT][6] | HDR được nhắc [CT][6]; 60 fps: bản đại lý nhắc [3P][7], trang chính thức không nhắc | không rõ | MP3, M4A... [CT][6] | không rõ | ✅ 45 ngôn ngữ SRT; **đốt phụ đề vào video** [CT][6] | không rõ | trang chính thức không nhắc AI [CT][6] |
| iTubeGo | "8K", "4K và Full-HD" [CT][8] | HDR, fps: không rõ [CT][8] | không rõ | MP3, M4A, WAV, AAC, FLAC; "high-bitrate MP3" , không nêu số kbps [CT][8] | không rõ | "subtitles as SRT files" [CT][8] | không rõ | không rõ |
| Downie (macOS) | 4K tốt, 8K không nhắc [3P][9]; trang chính thức **chưa đọc được** | không rõ | không rõ | trích âm thanh, định dạng không rõ [3P][9] | không rõ | không rõ | không rõ | không rõ |
| Stacher | "Support for 4K" [3P][10]; trang chính thức **chưa đọc được** (rỗng) | không rõ | không rõ | MP3 [3P][10] | không rõ | không rõ [3P][10] | không rõ | không rõ |
| Seal (Android, nền yt-dlp) | không rõ | không rõ | không rõ | "Embed metadata and video thumbnail into extracted audio files" [CT][11] | không rõ | ✅ "Embed subtitles into the downloaded videos" [CT][11] | ✅ siêu dữ liệu + ảnh bìa vào file âm thanh [CT][11] | không có |
| Video DownloadHelper | "HD quality" , không nêu mức tối đa [CT][12] | không rõ | không rõ | "MP3 and more" , không nêu bitrate [CT][12] | không rõ | phụ đề là tính năng bản trả phí [CT][12] | không rõ | không rõ |
| JDownloader 2 | trang chính thức chỉ có trang tải, không nói về chất lượng [CT][13] | không rõ | không rõ | không rõ | không rõ | không rõ | không rõ | không rõ |
| SnapTik | "Full HD or higher" nếu có [CT][14] | không rõ | không rõ | nút "Download Audio" , định dạng không rõ [CT][14] | ✅ "Remove watermarks from TikTok videos" , cách làm không nói [CT][14] | không rõ | không rõ | không có |
| SSSTik | "HD" , mức không nêu [CT][15] | không rõ | MP4 [CT][15] | MP3, bitrate không rõ [CT][15] | ✅ "No Watermark!" [CT][15] | không rõ | không rõ | không có |
| SaveFrom | **chưa đọc được** trang chính thức | | | | | | | |
| cobalt (mã nguồn mở, tài liệu API) | ✅ `videoQuality` tới "4320" ; mặc định "1080" [CT][16] | `allowH265` cho TikTok [CT][16]; HDR, fps: không rõ | ✅ `youtubeVideoCodec`: h264 (mặc định), av1, vp9; `youtubeVideoContainer`: auto, mp4, webm, mkv [CT][16] | ✅ `audioFormat`: best, mp3, ogg, wav, opus; `audioBitrate`: 320, 256, 128, 96, 64, 8 [CT][16] | không rõ | ✅ `subtitleLang` [CT][16] | không rõ (`filenameStyle` có 4 kiểu) [CT][16] | không rõ |
| Cốc Cốc (trình duyệt VN, mục tải Douyin) | "Full HD" và "high-resolution" , không nêu số cụ thể [CT][17] | không rõ | không rõ | chuyển MP3 [CT][17] | ✅ "without watermark" , cách làm không nói [CT][17] | không rõ | không rõ | không rõ |
| Công cụ Douyin web (Snapdouyin, DLPanda, SaveDouyin...) | "Full HD" , "chất lượng gốc" ; một bài tìm kiếm còn nói "1080p hay 'gốc' không được xác nhận rõ ràng ở đâu" [3P][18] | không rõ | không rõ | không rõ | ✅ quảng cáo "không logo" [3P][18] | không rõ | không rõ | không rõ |

Ghi chú B:

- Tôi **không** đọc được trang chính thức của Downie, Stacher, SaveFrom, cobalt.tools (trang người dùng), nên các ô này hoặc để "chưa đọc được" hoặc ghi [3P].
- Mọi ô "không rõ" chỉ nghĩa là trang tôi đọc không nhắc. Đặc biệt HDR và 60 fps: **chỉ UniTube nhắc HDR trên trang chính thức**; không trang nào tôi đọc nhắc 60 fps (trừ đại lý bán UniTube [3P]).
- Mẫu "chọn codec": chỉ **cobalt** (tài liệu API) và **4K Video Downloader Plus** (Smart Mode, qua bản phát hành) có chọn codec rõ ràng trong những gì tôi đọc.

---

## C. Khoảng cách: VidGrab đang thua ở đâu, hơn ở đâu

### C1. Đang thua hoặc thiếu (trong phạm vi đã đọc)

| # | Khoảng cách | Bằng chứng | Mức độ |
|---|---|---|---|
| 1 | **Chọn 1440p/4K không chắc ra 1440p/4K trên YouTube** | [ĐO] mục A.1; đối thủ quảng cáo thẳng 4K/8K ([CT][1][5][6]) | **Cao.** Là lời hứa của gói Pro |
| 2 | Không chọn được codec (H.264 / VP9 / AV1) | cobalt có `youtubeVideoCodec` [CT][16]; 4K Plus có tuỳ chọn AV1/VP9 [3P][4] | Trung bình |
| 3 | Không gắn tên bài, ảnh bìa, chương vào file | Seal [CT][11]; Allavsoft có chapter [CT][5] | Trung bình |
| 4 | Âm thanh: chọn mức kbps thô (128/320), nút OGG/WEBM gọi sai, MP3 mã hoá lại từ AAC | cobalt có 6 mức bitrate và định dạng `best`/`opus` [CT][16] | Trung bình |
| 5 | Không có HDR / fps (hiển thị hay chọn) | UniTube nhắc HDR [CT][6] | Thấp tới trung bình (đối thủ cũng ít nhắc) |
| 6 | Tên file web mất chữ Đ/đ | [ĐO] | Thấp về kỹ thuật, nhưng người Việt thấy ngay |
| 7 | App Windows: không có phụ đề; mặc định ra VP9/AV1 | mục A.9, A.12 | Trung bình |
| 8 | Đa định dạng chuyển đổi (AVI, MOV, FLV, FLAC...) | Allavsoft, UniTube, iTubeGo [CT][5][6][8] | Thấp cho người dùng VN [SL] |
| 9 | Công cụ AI xử lý âm thanh (tách giọng, giảm vang) | 4K Plus [CT][2] | Thấp; Q4 đã quyết "không thêm AI mới" |

### C2. Đang hơn — điểm bán nên nói rõ

| # | Điểm bán | Bằng chứng trong mã | Đối chiếu thị trường |
|---|---|---|---|
| 1 | **Bỏ logo bằng nguồn sạch, không nén lại** | mục A.6 | SnapTik/SSSTik cũng nói "không logo" nhưng không nói cách làm [CT][14][15]. VidGrab nói được vì mã rõ ràng [SL] |
| 2 | **"1080p dọc" ra đúng 1080x1920** (tính theo cạnh ngắn) | `downloader.py:1077-1090` (ghi chú đo thật 08/10); app `formats.rs:55-60` tính theo cạnh ngắn | Không trang nào tôi đọc nói về video dọc [SL: lợi thế vì TikTok/Reels/Shorts đều dọc] |
| 3 | **Mặc định mở được trên mọi máy** (H.264 + AAC) và báo "universal" thật theo file đã tải | `downloader.py:1060`, `3974-3981` | Đối thủ không nói về chuyện này trên trang đã đọc |
| 4 | **Nói đúng độ nét thật của file** (đo bằng ffprobe với TikTok) | `format_probe.py:1-12` | Không thấy đối thủ nào nói |
| 5 | **Dịch phụ đề bằng LLM + tạo phụ đề tự động (Whisper) + đốt vào video** | `docs/FEATURE_SPEC_transcript_translate.md` | Trang 4K Plus và UniTube nói SRT nhiều ngôn ngữ nhưng không nhắc tự dịch/ASR [CT][1][6]. Chỉ đúng trong phạm vi đã đọc |
| 6 | **Nền tảng Trung Quốc + TikTok + Facebook trong một sản phẩm**, kèm app, tiện ích, bot | `docs/MARKET-COMPARISON-2026-10.md` mục D1 | Đã so sánh ở tài liệu trước |
| 7 | Giá thấp (Pro 49.000đ/tháng) | `docs/MARKET-COMPARISON-2026-10.md` mục D2 | Không lặp lại ở đây |

**Lưu ý trung thực khi quảng bá:** điểm 1 và 3 trong C2 đúng cho TikTok, Douyin và các trang dùng H.264. Với YouTube 2K/4K, tới khi sửa A.1 thì **không nên** quảng cáo "tải 4K YouTube" cho đến khi đo lại bằng video thật.

---

## D. Tám đề xuất, xếp theo (giá trị cho người dùng Việt) ÷ (công sức)

Cách xếp: người Việt dùng nhiều TikTok/Douyin, YouTube, nhạc MP3, phụ đề tiếng Việt. Công sức: S = vài giờ AI, M = 1-3 ngày AI, L = hơn thế. (Quy ước của workspace: nén thời gian code tay về giờ AI.)

Đối chiếu kế hoạch Q4 (`docs/PLAN-2026-Q4.md`): kế hoạch này tập trung đo đạc và độ tin cậy, **không** có đề xuất nào dưới đây đã nằm trong đó. Các điểm giao nhau: đề xuất 1 liên quan Q4 P1.2 (giới hạn chất lượng hạng Free); đề xuất 7 chạm vào "đào sâu xử lý media" mà `PRODUCT-STATUS.md` liệt kê như một hướng chưa chọn; Q4 ghi "KHÔNG làm quý này: thêm tính năng AI mới", nên đề xuất 7 chỉ **nối cái đã có**, không thêm AI.

### 1. Sửa "chọn 1440p/4K ra đúng 1440p/4K" trên YouTube (có mini-spec ở E.1)

- **Người dùng thấy gì đổi:** chọn 4K thì file ra 4K (VP9 hoặc AV1). Nhãn "dễ mở / cần trình phát hiện đại" sẵn có được dùng lại.
- **Tệp có thể đổi:** `backend/app/services/downloader.py` (khoảng 1058-1161), `backend/tests/test_yt_degraded_extraction.py:207` (đang khẳng định chuỗi cũ).
- **Công sức:** S. **Rủi ro:** file VP9/AV1 không mở trên QuickTime; tải lớn hơn, tốn băng thông proxy (`youtube_gate.py` ghi hiện không giới hạn).
- **Kiểm chất lượng khách quan:** `ffprobe` đọc cao x rộng, codec; so với định dạng nguồn mà yt-dlp báo.

### 2. Tên file giữ tiếng Việt có dấu (có mini-spec ở E.2)

- **Đổi gì:** "Đường về nhà đẹp.mp4" ra đúng tên đó (hoặc bỏ dấu đúng "Duong-ve-nha-dep"), không mất Đ/đ.
- **Tệp:** `backend/app/api/routes.py` (các đoạn `normalize('NFKD')` ở 1836, 2016, 2146, 2602), `backend/app/api/processing.py:90`; có thể gom về một hàm dùng chung.
- **Công sức:** S. **Rủi ro:** thấp; cần đặt `Content-Disposition` dạng `filename*=UTF-8''...` đúng chuẩn để trình duyệt không hỏng tên.
- **Kiểm:** bộ tên mẫu (có Đ, đ, dấu hỏi ngã, emoji) đi qua đường tải, so với tên mong đợi; mở file trên Windows và macOS.

### 3. Báo thật chất lượng file đã tải (có mini-spec ở E.3)

- **Đổi gì:** sau mỗi lần tải hiện "3840x2160 · AV1 · 60 fps · 12 Mbps · HDR" lấy từ chính file. Cơ sở đã có: `downloaded_height` và `downloaded_vcodec` (`downloader.py:3966-3981`) và bộ đo `format_probe.py`.
- **Tệp:** `downloader.py` (khối kết quả), `backend/app/api/routes.py:1073` (trường trả về), giao diện web và app (hiển thị).
- **Công sức:** S-M. **Rủi ro:** thấp (chỉ đọc file); chạy ffprobe thêm vài chục mili giây.
- **Giá trị bán hàng:** điểm khác biệt "nói đúng sự thật về file". Đồng thời là công cụ kiểm cho mọi đề xuất còn lại.

### 4. Douyin/TikTok: luôn lấy luồng nét nhất

- **Đổi gì:** `video` và `HD` cũng chọn luồng `bit_rate` cao nhất như `video_4k` đang làm; tiện ích Chrome và app Windows cũng vậy; hiển thị độ nét thật.
- **Tệp:** `backend/app/services/douyin_extractor.py:973-987`, `chrome-extension/content.js:379-390`, `desktop/src-tauri/src/douyin_local.rs:152-157`; test `backend/tests/test_douyin_signed.py`.
- **Công sức:** M. **Rủi ro:** liên kết luồng cao có thể bị chặn hoặc cần Referer riêng; dung lượng lớn hơn.
- **Kiểm:** 10 link Douyin thật, so `play_addr` với `bit_rate` cao nhất bằng ffprobe (cao, rộng, kbps). **Phải làm bước đo này trước**, vì hiện chưa biết chênh lệch bao nhiêu.

### 5. Âm thanh chuẩn

- **Đổi gì:** chọn 128 / 192 / 256 / 320; thêm "giữ âm thanh gốc" (không nén lại M4A/Opus); sửa nút OGG và WEBM; tuỳ chọn chuẩn hoá âm lượng (đã có ở `processing.py:368`); bitrate hiển thị rõ trong app.
- **Tệp:** `downloader.py:1117-1119, 1196-1210`, `frontend/src/components/DashboardContent.jsx:1065-1070`, `desktop/src-tauri/src/engine.rs:49-60, 208-217`.
- **Công sức:** M. **Rủi ro:** thấp tới trung bình (đổi hợp đồng giữa web và máy chủ).
- **Kiểm:** ffprobe `codec_name`, `bit_rate`, `sample_rate`; đo lại âm lượng bằng `ffmpeg -af ebur128` để xác nhận chuẩn hoá.

### 6. Gắn tên bài, ảnh bìa, chương vào file

- **Đổi gì:** MP3/M4A có ảnh bìa, tên bài, nghệ sĩ; MP4 có tiêu đề và chương (nếu có). Làm bằng các bước có sẵn của yt-dlp, không viết lại bộ xử lý.
- **Tệp:** `downloader.py` (khối `postprocessors`, 1187-1210), `engine.rs` (thêm cờ), `smart_metadata.py:279` (dùng lại cách suy ra tên nghệ sĩ/album).
- **Công sức:** S-M. **Rủi ro:** ảnh bìa webp cần đổi sang jpg; app cần chứng minh ffmpeg đi kèm có đủ.
- **Kiểm:** `ffprobe -show_format -show_streams`: có luồng ảnh bìa (`codec_type=video, disposition attached_pic=1`), thẻ `title`, `artist`; `-show_chapters` có chương.

### 7. Phụ đề trọn gói: SRT chuẩn, nhúng mềm vào MP4, tự dịch sang tiếng Việt ngay khi tải

- **Đổi gì:** chọn "kèm phụ đề tiếng Việt" thì ra file SRT chuẩn; có thể nhúng mềm vào MP4; nếu nguồn không có tiếng Việt thì tự dịch bằng chức năng sẵn có; đốt cứng giữ chất lượng cao hơn (CRF 18 thay vì 23). Thêm tuỳ chọn phụ đề cho app Windows.
- **Tệp:** `downloader.py:3207-3213`, `routes.py:488-515`, `backend/app/api/transcript_translate.py`, `desktop/src-tauri/src/engine.rs`.
- **Công sức:** M-L. **Rủi ro:** chi phí LLM theo lượt; quota; `FEATURES.md` ghi "Subtitle backend partial" nên cần kiểm tra đầu-cuối trước.
- **Giao với Q4:** chỉ nối cái đã có, không thêm AI mới.
- **Kiểm:** ffprobe luồng phụ đề (`codec_name=mov_text` hoặc `subrip`); so số dòng và mốc thời gian SRT với nguồn.

### 8. App Windows: preset "Dễ mở" và "Chất lượng gốc"

- **Đổi gì:** thêm chọn "Dễ mở trên mọi máy (H.264 + AAC)" và "Chất lượng gốc (VP9/AV1/Opus)"; hiện fps, codec, HDR trên từng dòng chất lượng (dữ liệu đã có trong `ProbeFormat`).
- **Tệp:** `desktop/src-tauri/src/formats.rs:47`, `desktop/src/lib/quality.ts`, màn hình chọn chất lượng.
- **Công sức:** M. **Rủi ro:** thêm một lựa chọn cho người dùng; cần giữ mặc định đơn giản.
- **Kiểm:** ffprobe file đầu ra của hai preset trên cùng video; so khớp với nhãn.

**Không đề xuất (và vì sao):** nâng độ nét bằng AI (upscale) và khử nhiễu video: không thấy đối thủ nào trong phạm vi đọc có (4K Plus chỉ có AI âm thanh [CT][2]), Q4 đã loại "thêm AI mới", chi phí tính toán lớn. Chuyển đổi sang AVI/MOV/FLV: nhu cầu thấp với người dùng Việt [SL].

---

## E. Đặc tả rút gọn cho ba đề xuất đứng đầu

### E.1 Đề xuất 1 — Chọn 1440p/4K thì ra 1440p/4K (YouTube)

**Context.** Gói Pro bán "tới 2160p" (`docs/PRODUCT-STATUS.md`, bảng giới hạn). Chuỗi chọn định dạng đặt nhánh "H.264 + AAC" lên đầu (`downloader.py:1058-1069`, `1091-1104`, `1142-1160`). YouTube chỉ cung cấp H.264 tới 1080p (theo chú thích của chính mã: "YouTube has no H.264 there", `downloader.py:1644`). Kết quả mô phỏng [ĐO]: mọi lựa chọn từ `video` đến `video_4k` đều ra 1080p H.264.

**Goal.** Khi người dùng chọn một mức chất lượng cao hơn 1080p và nguồn có, file tải về phải có đúng mức đó (±5%), kèm nhãn codec.

**Constraints.**
- Không làm hỏng mặc định "dễ mở" cho mức ≤ 1080p.
- Không làm tăng lỗi cho TikTok, Instagram, X (các trang này không bị ảnh hưởng, chỉ H.264).
- Giữ nguyên cách tính theo cạnh ngắn cho video dọc (đã đo thật 08/10).
- Không viết lại luồng tải; chỉ đổi chuỗi chọn.

**Scope.**
- **A (phải có):** với `video_<N>` và `video_4k` khi N > 1080: thử trước nhánh "đúng chiều cao N, mọi codec" (ưu tiên VP9 trước AV1, giống thứ tự `downloader.py:1621-1628`), rồi mới tới nhánh H.264. Cập nhật test chuỗi chọn. Kết quả trả về đã có `downloaded_universal` (`downloader.py:3980`) để giao diện báo "cần trình phát hiện đại".
- **B (nên có):** thông báo trên giao diện khi file là VP9/AV1.
- **C (có thể):** cho tuỳ chọn "chuyển mã về H.264" (tốn CPU, chỉ Pro).
- **D (không làm):** không tự chuyển mã mặc định; không thêm HDR/fps ở đề xuất này.
- **E (câu hỏi mở):** (1) Đường dự phòng Cobalt (`cobalt_video_quality("video_4k") == "max"`, `tests/test_cobalt_upgrade.py:43`) có đang trả 4K thật? (2) Chủ sản phẩm muốn ghép 4K vào `.mp4` hay `.mkv`? (3) Băng thông proxy YouTube chịu được file 4K không (`youtube_gate.py:125-139` hiện bỏ giới hạn)?

**Audit Before Build.**
1. Chạy tải thật 3 video YouTube công khai có 4K (một ngang, một dọc kiểu Shorts, một có HDR) ở `video_1440`, `video_2160`, `video_4k`; ghi cao x rộng, codec bằng ffprobe. Mục tiêu: xác nhận lỗi trên YouTube thật, vì mô phỏng của tôi chưa phải thật.
2. Xem bản yt-dlp đang chạy trên máy chủ (mô phỏng dùng 2026.04.30).
3. Đọc `backend/tests/test_yt_degraded_extraction.py:207` và `test_format_selector_unknown_height.py` để biết test nào cần cập nhật.
4. Kiểm tra `YT_MAX_HEIGHT_USER` / `_ANON` (`plans.py:143-145`) và `check_youtube_tier` (`quotas.py`) để không mở nhầm 4K cho Free.

**Design Choice (một cách).** Thêm một nhánh đứng **trước** nhánh H.264 chỉ khi N > 1080: `bestvideo[height<=N][height>1080]+bestaudio` (cùng bản dọc tương ứng), và giữ nguyên toàn bộ chuỗi cũ phía sau làm dự phòng. Lý do: thay đổi nhỏ nhất, không đụng mức ≤ 1080 (nơi H.264 đang tốt), dễ viết test bằng danh sách định dạng giả lập, dễ rút lại. Cách thay thế (đổi `format_sort` sang sắp theo độ phân giải rồi codec) rộng hơn và dễ làm đổi kết quả của các trang khác, nên không chọn.

**Test Plan.**
- Test đơn vị: danh sách giả lập kiểu YouTube (như tôi dùng ở mục A) cho `video`, `video_1080`, `video_1440`, `video_2160`, `video_4k`, video dọc; khẳng định đúng format id.
- Test hồi quy: Facebook/Instagram/X/TikTok vẫn chọn như cũ.
- Thử thật (qua API, bằng `curl`): 3 video ở bước Audit, `ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,r_frame_rate,bit_rate -of json`.
- So với nguồn: định dạng yt-dlp liệt kê ở cùng chiều cao (`yt-dlp -F`).
- Mở thử file trên VLC và trình phát mặc định Windows (ghi nhận kết quả).

**Success Criteria (quan sát được).**
1. Với video nguồn có 2160p, `video_2160` cho file có `min(width,height) ≥ 2052` (tức ≥ 95% của 2160).
2. `video_1080` và `video` vẫn ra H.264 ở 1080p như trước.
3. Dọc 1080x1920 vẫn ra 1080x1920.
4. Kết quả trả về có `downloaded_universal = false` cho file VP9/AV1.
5. Không tăng tỷ lệ tải lỗi YouTube trong 7 ngày theo số liệu sự kiện `download_failed` (Q4 P0.3).

### E.2 Đề xuất 2 — Tên file giữ tiếng Việt

**Context.** Bốn nơi trên máy chủ đổi tên người dùng sang ASCII bằng `unicodedata.normalize('NFKD')` rồi `encode('ascii','ignore')` (`routes.py:1836`, `2016`, `2146`, `2602`; thêm `processing.py:90-94`). Chữ Đ/đ không có dạng tách dấu nên bị xoá. [ĐO] "Đường về nhà đẹp" thành `uong ve nha ep`. App Windows không bị.

**Goal.** Tên file tải qua web giữ đúng tiếng Việt, hoặc ít nhất bỏ dấu đúng (đ thành d, Đ thành D), không rơi chữ.

**Constraints.** Phải an toàn với ký tự cấm của hệ điều hành; không để tên chứa `..`, dấu `/` hoặc `\`; không phá chuẩn `Content-Disposition`; không đổi quy ước tên lưu trên đĩa của máy chủ (`downloader.py:521-534`, dùng token để chống đoán).

**Scope.**
- **A:** một hàm dùng chung `slug_vi(name)` thay bốn nơi; xử lý Đ/đ trước khi tách dấu.
- **B:** giữ tiếng Việt có dấu trong tên tải về (cần `filename*=UTF-8''...`), có công tắc để bỏ dấu.
- **C:** mẫu tên (ngày + tiêu đề) giống app Windows.
- **D:** không đổi tên file lưu trên đĩa máy chủ.
- **E (mở):** chủ sản phẩm muốn mặc định "giữ dấu" hay "bỏ dấu"? Tên có dấu có thể gây lỗi ở một số trình giải nén/ổ đĩa cũ [SL].

**Audit Before Build.** Đọc `app/core/local_download.py` (`download_url`, `file_fields`) xem tên hiển thị ghép ở đâu; kiểm tra tiện ích Chrome và bot Telegram có tự đặt tên riêng không (`chrome-extension/direct-download.js`, hàm `safeName`, đang giữ Unicode); chạy `grep -rn "NFKD"` toàn repo để không sót nơi thứ năm.

**Design Choice.** Giữ tiếng Việt có dấu và gửi tên qua `filename*=UTF-8''<percent-encoded>` kèm `filename="<bản ASCII đúng>"` dự phòng, vì trình duyệt hiện đại đều đọc được `filename*` và đây là cách chuẩn; bản ASCII dự phòng dùng bảng thay Đ→D, đ→d trước NFKD. Không chọn "chỉ bỏ dấu", vì như vậy vẫn xấu cho người dùng Việt.

**Test Plan.** Bộ tên mẫu: "Đường về nhà đẹp", "Tiếng Việt: ‘ngoặc’ ? * < > |", "😀 Video", tên rất dài (>200 ký tự), "..", "a/b\c". Chạy qua `proxy_download` và `trim`, đọc tiêu đề `Content-Disposition`; tải bằng Chrome và Edge trên Windows, Safari trên macOS (nếu có máy).

**Success Criteria.** (1) "Đường về nhà đẹp" tải về đúng tên "Đường về nhà đẹp.mp4" (hoặc "Duong-ve-nha-dep.mp4" nếu chọn bỏ dấu), không bao giờ "uong-ve-nha-ep". (2) Không có tên chứa `/ \ .. :`. (3) Test bộ tên mẫu chạy xanh trong CI.

### E.3 Đề xuất 3 — Báo thật chất lượng file đã tải

**Context.** Mã đã trả `downloaded_height` và `downloaded_vcodec` cho video lưu trên máy chủ (`downloader.py:3966-3981`); TikWM có bộ đo riêng bằng ffprobe (`format_probe.py`). Chưa có fps, bitrate, HDR, kênh âm thanh, tần số lấy mẫu. Giao diện chưa cho người dùng thấy "file này có gì".

**Goal.** Sau mỗi lần tải, người dùng thấy một dòng sự thật về file (độ nét, codec, fps, bitrate, HDR có/không) được đo từ chính file.

**Constraints.** Không chậm việc giao file (chạy sau, hoặc đo nhanh dưới 1 giây); không đo qua mạng (chỉ file cục bộ); không lộ đường dẫn máy chủ; không đo file tải trực tiếp trong trình duyệt (không có file ở máy chủ).

**Scope.**
- **A:** hàm đo bằng `ffprobe` (có sẵn mẫu `downloader.py:338-350`) mở rộng: `width, height, codec, fps (r_frame_rate), bitrate, color_transfer` (HDR nếu `smpte2084` hoặc `arib-std-b67`), `audio codec, bitrate`; trả trong kết quả tải; hiển thị trên web.
- **B:** hiển thị tương tự trong app Windows (ffprobe đi kèm ffmpeg đã xác minh, `engine.rs`).
- **C:** ghi vào lịch sử tải.
- **D:** không làm gì thay đổi file.
- **E (mở):** hiển thị mặc định hay chỉ khi bấm "Chi tiết"? Có dùng nhãn "HDR" cho người dùng thường không?

**Audit Before Build.** Đọc `downloader.py:330-360` (hàm đo hiện có và nơi gọi); kiểm tra ffprobe có trong ảnh Docker của máy chủ (nó đã được gọi ở đó); đọc `routes.py:1060-1080` để xem trường nào đã đi ra giao diện; đọc `desktop/src-tauri/src/proc.rs` về cách chạy tiến trình con.

**Design Choice.** Mở rộng hàm ffprobe đã có để trả một cấu trúc duy nhất và nối vào chỗ `downloaded_height/downloaded_vcodec` đang được điền, rồi để giao diện đọc từ đó. Lý do: tái dùng mã đã chạy ở sản xuất, một lần gọi ffprobe cho mọi trường, không thêm dịch vụ. Không chọn "đọc từ metadata yt-dlp" vì đó là điều nguồn nói, không phải điều file có (sai lệch chính là lỗi A.1).

**Test Plan.** Tạo 4 file mẫu bằng ffmpeg (H.264 30 fps, VP9 60 fps, HEVC HDR10 giả lập bằng cờ màu, AAC/Opus) rồi đo; so sánh giá trị hàm trả với `ffprobe -show_streams` chạy tay; thử file hỏng và file chỉ có âm thanh. Lưu ý (kinh nghiệm cũ của đội): ffmpeg `gradients` không tất định, dùng nguồn tất định cho mẫu.

**Success Criteria.** (1) 4/4 file mẫu có số đo khớp `ffprobe` chạy tay (cao, rộng, codec, fps sai số ≤ 0,01). (2) File HDR mẫu được gắn HDR; file SDR thì không. (3) Thời gian đo thêm ≤ 1 giây ở bộ file mẫu. (4) Một lần tải thật trên web hiện đúng dòng chất lượng.

---

## F. Nguồn (đọc ngày 27/10/2026)

Đọc được, trang chính thức [CT]:
- [1] https://www.4kdownload.com/products/videodownloader-plus và https://www.4kdownload.com/products/product-videodownloader
- [2] https://www.4kdownload.com/blog/2026/04/13/4k-video-downloader-plus-26-1-0-is-out/ (AI xử lý âm thanh)
- [3] https://www.4kdownload.com/blog/2026/08/20/4k-video-downloader-plus-26-3-1/ (Smart Mode tôn trọng codec đã chọn; khôi phục 4K/8K)
- [5] https://www.allavsoft.com/
- [6] https://www.vidjuice.com/unitube-video-downloader/ ; giá/gói: https://www.vidjuice.com/pricing/ ("Full support HD, 4K and 8K", "Download in MP3 format")
- [8] https://www.itubego.com/ (trang nêu hai con số số trang hỗ trợ, 1.000 và 10.000, mâu thuẫn; không dùng)
- [11] https://github.com/JunkFood02/Seal
- [12] https://www.downloadhelper.net/
- [13] https://jdownloader.org/jdownloader2 (chỉ có trang tải, không nói về chất lượng)
- [14] https://snaptik.app/en
- [15] https://ssstik.io/en
- [16] https://github.com/imputnet/cobalt/blob/main/docs/api.md ; https://github.com/imputnet/cobalt (trang chính trả ít thông tin)
- [17] https://coccoc.com/en/tai-video-douyin

Nguồn bên thứ ba [3P]:
- [4] kết quả tìm kiếm (chỉ đọc tóm tắt, chưa mở bài gốc): ghi chú phát hành 4K Video Downloader Plus, ví dụ https://www.4kdownload.com/blog/2025/07/10/4k-video-downloader-plus-rolls-out-25-2-0-update/ (hiển thị AV1/VP9) và https://www.4kdownload.com/blog/2025/05/13/new-4k-video-downloader-plus-update-25-1-0/ (tiêu đề nhắc "Original Audio Option", chưa đọc nội dung)
- [7] đại lý bán UniTube, ví dụ https://www.stacksocial.com/sales/vidjuice-unitube-video-and-audio-downloader , https://alternativeto.net/software/vidjuice-unitube/about (nội dung nhiều khả năng sao chép từ nhà cung cấp, đã được chính kết quả tìm kiếm cảnh báo)
- [9] https://thesweetbits.com/tools/downie-video-downloader/ (Downie); bổ sung qua tìm kiếm: https://eshop.macsales.com/blog/96182-downie-4-makes-downloading-videos-from-the-internet-extremely-easy/
- [10] https://alternativeto.net/software/stacher/about (Stacher)
- [18] tìm kiếm Douyin: https://dienthoaivui.com.vn/tai-video-douyin , https://dienthoaivui.com.vn/snaptik-douyin , https://cellphones.com.vn/sforum/tai-video-douyin (các bài hướng dẫn mang tính quảng bá, không kiểm chứng độc lập)

Không đọc được hoặc không dùng:
- Trang chính thức: Downie (`software.charliemonroe.net/downie/`, bị chặn), Stacher (`stacher.io` trống), `github.com/stacher7/stacher` (404), `cobalt.tools` (không mở được), `www.vidjuice.com/` trang chủ (bị chặn khi đọc lần này), SaveFrom (kết quả tìm kiếm chỉ là các trang sao chép không chính thức, **không dùng**), `www.4kdownload.com/blog/2026/07/07/meet-4k-video-downloader-plus-26-2-0/` (chỉ thấy tiêu đề).
- HDR và 60 fps của hầu hết đối thủ: không nêu trên trang chính thức đã đọc, nên ghi "không rõ".

Nguồn nội bộ: `docs/MARKET-COMPARISON-2026-10.md`, `FEATURES.md`, `docs/PRODUCT-STATUS.md`, `docs/PLAN-2026-Q4.md`, `docs/FEATURE_SPEC_transcript_translate.md`, và các tệp mã có ghi `tệp:dòng` ở mục A.

### Danh sách chưa kiểm chứng (cần làm trước khi cam kết)

1. Lỗi A.1 (YouTube 1440p/4K ra 1080p) mới được **mô phỏng**, chưa đo trên YouTube thật; chưa biết bản yt-dlp trên máy chủ.
2. Chênh lệch độ nét giữa `play_addr` và `bit_rate` cao nhất của Douyin; độ nét thật của TikWM `hdplay`: chưa đo.
3. Hành vi của `audio_webm` trong thực tế (tôi mới xác nhận danh sách định dạng được hỗ trợ, chưa chạy tải).
4. File MP3 tải về có thẻ tên bài/ảnh bìa hay không: tôi chỉ tìm không thấy mã gắn, chưa mở một file thật.
5. Đường dự phòng Cobalt có trả 4K thật hay không.
6. Mọi ô "không rõ" ở mục B.

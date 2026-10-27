# So sánh CHẤT LƯỢNG ĐẦU RA: VidGrab (sau bản 0.21.0) và đối thủ — 28/10/2026

Ngày đọc: **28/10/2026**. Chỉ đọc **trang công khai** (WebFetch/WebSearch). Không đăng ký, không dán link vào dịch vụ của ai, không tải gì từ đối thủ, không gọi API của đối thủ (tài liệu API của cobalt chỉ được ĐỌC trên GitHub).

Đọc trước: `docs/MARKET-COMPARISON-2026-10.md` (14/10, tự động hoá + giá) và `docs/QUALITY-UPGRADE-2026-10.md` (27/10, khoảng cách chất lượng). Báo cáo này là bản cập nhật cho **sau khi 8 hạng mục chất lượng đã vào code** (10 commit 27/10, app 0.21.0). Không lặp lại phần giá/tự động hoá.

## Cách đọc

- **[CT]** trang chính thức của đối thủ. **[3P]** bên thứ ba. **[SL]** suy luận của người viết, không phải dữ kiện. **[MÃ]** đọc từ repo VidGrab.
- "không rõ" = trang đã đọc không nói (không có nghĩa sản phẩm không có). "chưa đọc được" = trang không mở được / bị công cụ chặn.
- Công cụ đọc trang trả về bản tóm tắt của mô hình nhỏ; các câu trích trong mục 1 có thể bị cắt ngắn. Muốn dùng làm bằng chứng pháp lý, mở trang gốc và đọc lại.
- Không điền từ trí nhớ. Cột VidGrab chỉ ghi cái **đã có trong code** (commit `86ca7ad` … `f347315`, `notes-0.21.0.md`).


## 0. Cập nhật sau khi đối chiếu với code và máy chủ (28/10, cùng ngày)

- **T1 sai một nửa:** trên máy chủ thật, `/api/v1/plans` trả `youtube_max_height: 0` (= **không giới hạn**) cho khách và Free, vì `YT_MAX_HEIGHT_ANON`/`YT_MAX_HEIGHT_USER` đặt 0; `YT_MAX_HEIGHT_PRO` không đặt → **Pro bị chặn 2160** — ngược đời. Đã sửa ở `364e595` (gói trả phí ≥ Free; 0 = không giới hạn), chờ deploy. Việc "chưa đo 4K YouTube thật" vẫn đúng: workspace thấy luồng 2160p60/HDR nhưng không có ffmpeg để ghép + đo.
- **T4 / đề xuất 7 đã làm một phần:** app có nút **"Dịch phụ đề sang tiếng Việt"** trong Lịch sử (`dd6ee3a`, dùng job dịch của máy chủ, lưu `<tên>.vi.srt`). Phụ đề tự động/nhiều ngôn ngữ và ảnh bìa trong app vẫn chưa có.
- **H1 mở rộng:** số đo chất lượng giờ được giữ trong lịch sử web (`e2896b2`).
- Các ô đối thủ không đổi.

---

## 1. Điều khoản: có cấm dùng để so sánh / cạnh tranh / thu thập tự động không?

Từ khoá tìm: competitive / competitor / similar product / benchmark / reverse engineer / scrap / collect data / automated / robot.

| Đối thủ | URL điều khoản đã đọc | Có cấm so sánh / cạnh tranh / thu thập tự động? (trích ≤25 từ) | Chạy thử THỦ CÔNG để so sánh |
|---|---|---|---|
| 4K Video Downloader Plus | https://www.4kdownload.com/agreement/terms-of-use/8 ("4K Download Terms of Use", link ở chân trang). `/eula`, `/terms-of-service` trả 404 | Không có benchmark/competitor. Cấm công cụ tự động: "including robots, crawlers or data mining tools, to download, monitor or use data or Content from the Application". Cấm: "You agree not to reverse engineer any portion of the Application." | **CÓ** (thủ công, một người dùng thật; không script/crawler; không dịch ngược) |
| JDownloader 2 | https://jdownloader.org/ (chân trang chỉ có Impressum và Privacy), `/impressum`, `/legal/terms` ("This topic does not exist yet") | Không tìm thấy trang điều khoản/EULA trên web. Chưa đọc EULA trong bộ cài | **KHÔNG RÕ** |
| Allavsoft | https://www.allavsoft.com/privacy.html (chỉ privacy + hoàn tiền). `/license.html`, `/terms.html`, `/eula.html`, `/terms-of-use.html`, `/refund.html` đều 404 | Không có trang điều khoản công khai tìm được | **KHÔNG RÕ** |
| VidJuice UniTube | https://www.vidjuice.com/terms-of-service/ ("Terms of Service - VidJuice"); `/eula/` 404; `/copyright/` chỉ là DMCA | Không có điều khoản benchmark/competitor/automated. Có: "Any commercial use of vidjuice.com is strictly forbidden and will be pursued in a court of law." và "must only be used for private purposes." | **KHÔNG RÕ** (cấm dùng thương mại; làm báo cáo cho sản phẩm bán tiền có thể bị xem là thương mại [SL]) |
| iTubeGo | https://itubego.com/term-of-service/ ("Term Of Service | iTubeGo") và https://itubego.com/license-agreement/ ("End User License Agreement") | Cả hai: không thấy các từ khoá. Mục 4 "Restrictions on Use" chỉ nêu cấm sửa/dịch/cho thuê phần mềm. Một bài báo [3P] nói giấy phép thường là cá nhân, phi thương mại | **CÓ** (không thấy điều cấm; lưu ý giấy phép cá nhân [3P]) |
| Downie (macOS) | EULA ở https://charliemonroe.net/policy/eula/ (link chân trang) trả 404 khi đọc | Chưa đọc được | **KHÔNG RÕ** (chưa đọc được) |
| Video DownloadHelper | https://downloadhelper.net/tos-freemium ("End User License Agreement (Free License)", III.4); không thấy EULA riêng cho Premium | Có, rõ ràng: "for purposes of monitoring the Software's performance, functionality, or availability or for any other benchmarking or competitive purposes." Và: "access or attempt to access to the Software by way of automated means" | **KHÔNG** (với bản Free) |
| SSSTik | https://ssstik.io/en , `/contacts` (chân trang không có link điều khoản/privacy; `/en/terms` chỉ ra trang quảng cáo) | Không có điều khoản công khai | **KHÔNG RÕ** |
| SnapTik | https://snaptik.app/landing/terms-of-service ("Terms of Service | SnapTik") | Không thấy các từ khoá. Có: "Use the Content and Materials for any commercial purpose, or for any public display" (cấm, nói về nội dung) | **CÓ** (không thấy điều cấm) |
| SaveFrom.net | https://en.savefrom.net/ | Công cụ đọc trang bị chặn; tìm kiếm chỉ ra các trang sao chép không chính thức | **KHÔNG RÕ** (chưa đọc được) |
| Loader.to | https://en.loader.to/terms.html ("Terms of Use", Mục 6) | Không có competitor/benchmark. Có: "You agree not to use any automated means, including robots, crawlers or data mining tools," (tiếp: "to download, monitor or use data or Content from the Website") và "You agree not to reverse engineer any portion of the Website." | **CÓ** (thủ công; không tự động) |
| cobalt.tools | https://cobalt.tools/about/terms ("about / terms and ethics") | Không có điều khoản cấm so sánh/tự động. Phạm vi: "these terms are applicable only when using the official cobalt instance." Trang này không nhắc API/bot. (API công cộng có hạn chế riêng ở tài liệu API, xem báo cáo 14/10; KHÔNG gọi) | **CÓ** (chỉ thủ công trên web; không dùng API) |
| y2mate | https://y2mate.com/ | Công cụ đọc trang bị chặn | **KHÔNG RÕ** (chưa đọc được) |
| Cốc Cốc (VN, mục tải Douyin) | https://coccoc.com/termsofuse ("Điều khoản sử dụng | Trình duyệt Cốc Cốc"); trang tính năng https://coccoc.com/en/tai-video-douyin không có điều khoản riêng | Phần chữ hiển thị được chỉ có tiêu đề và mở đầu; không thấy các từ khoá nhưng chưa thấy nội dung từng mục ("Hành vi bị cấm…") | **KHÔNG RÕ** |
| SnapDouyin (VN/Douyin) | https://snapdouyin.app/terms-of-service/ ("Terms of Service - SnapDouyin") | Không thấy các từ khoá. Có: "Users only use video downloaded through the SnapDouyin App for personal, non-commercial, free purposes." | **CÓ** (không thấy điều cấm; ràng buộc là về việc dùng video tải về) |
| DLPanda (Douyin/TikTok) | https://dlpanda.com/static/terms-of-service ("Terms of Service - DLPanda.com") | Không thấy competitor/benchmark/scrap/reverse. Chỉ cấm "unauthorized commercial use of third-party content"; tự mô tả là công cụ tự động xử lý link người dùng đưa | **CÓ** (không thấy điều cấm) |

Kết luận mục 1:
- **Ai nói thẳng cấm benchmark/cạnh tranh:** chỉ **Video DownloadHelper** (EULA bản Free, III.4(xi)). Không chạy thử, không đưa vào bảng đo, trừ khi đọc EULA bản Premium và xác nhận khác đi.
- **Cấm công cụ tự động (robot/crawler) nhưng không cấm thử tay:** 4K Video Downloader Plus, Loader.to (cùng mẫu câu). Với hai nơi này chỉ được thử bằng tay, từng link, không script.
- **Chưa đọc được / không có điều khoản:** JDownloader, Allavsoft, Downie, SSSTik, SaveFrom, y2mate, Cốc Cốc (nội dung chi tiết), VidJuice (cấm dùng thương mại). Xử lý như "KHÔNG RÕ": không chạy thử cho tới khi chủ sản phẩm quyết.
- **Không thấy điều cấm:** iTubeGo, SnapTik, cobalt (web), SnapDouyin, DLPanda.
- [SL] Ngay cả "CÓ" ở đây chỉ nghĩa là điều khoản đọc được không cấm. Báo cáo này **không** chạy thử bất kỳ dịch vụ nào; mọi dữ kiện lấy từ trang giới thiệu.

---

## 2. Bảng chất lượng

Ký hiệu nhãn trong mỗi ô: [CT] / [3P] / [SL] / [MÃ]. Số liệu đối thủ đọc 28/10 trừ khi ghi khác. Hàng "đọc 27/10" lấy từ `docs/QUALITY-UPGRADE-2026-10.md` mục B (cùng nguồn công khai).

### 2.1 Hình ảnh: độ nét, codec, HDR/fps

| Công cụ | Độ nét tối đa | Chọn codec | HDR / 60 fps |
|---|---|---|---|
| **VidGrab** | YouTube: gói trả phí tới 2160p (`YT_MAX_HEIGHT_PRO`), Free 1080p, khách 720p; nền tảng khác theo `FREE_MAX_HEIGHT` [MÃ `0489f76`, `plans.py:142-145`]. Không có mã riêng cho 8K. **Chưa đo trên YouTube thật**: bộ chọn đã kiểm bằng yt-dlp thật, phần đo ffprobe bỏ qua ở workspace (không có ffmpeg) [MÃ `11744c8`] | Web: không có nút chọn; H.264+AAC đến 1080p, trên 1080p VP9 → AV1 → bất kỳ, ghép .mp4 không nén lại, có cờ "universal" + ghi chú [MÃ `0489f76`]. App: **Kiểu tệp video** "Dễ mở" (mặc định) / "Chất lượng gốc" [MÃ `e708468`, `notes-0.21.0`]. Douyin (máy chủ): H.264 trước, H.265 chỉ khi chọn 4K và nét hơn hẳn [MÃ `96056a4`] | Không chọn được. **Đo và hiện nhãn** HDR (PQ/HLG), fps, bitrate sau khi tải, cả web lẫn app [MÃ `11744c8`, `007b75d`] |
| 4K Video Downloader Plus | 4K và 8K [CT] ("8K UHD" trong FAQ) | Trang sản phẩm: không rõ. Ghi chú phát hành 26.3.1 [CT]: "Smart Mode now correctly respects whichever codec you've selected" (nhắc AV1/VP9, không nhắc H.264); container MP4 và MKV [CT] | HDR: không rõ. 60 fps: không rõ [CT] |
| JDownloader 2 | không rõ (trang chỉ để tải) [CT] | không rõ | không rõ |
| Allavsoft | 8K, 4K [CT]; muốn 4K YouTube phải bật AV1 trong Preferences > Advanced | Chỉ nhắc AV1 [CT] | không rõ |
| VidJuice UniTube | 720p, 1080p, 4K, 8K [CT] | không rõ | "supports a variety of 4K, 8K, HDR video formats" [CT] (nói về hỗ trợ định dạng, không nói đầu ra); 60 fps không rõ |
| iTubeGo | tới 8K [CT]; 4K, Full-HD | không rõ | không rõ |
| Downie | "Download YouTube videos in full 4K resolution" [CT]; 8K không rõ | không rõ | không rõ |
| Video DownloadHelper | không nêu mức tối đa ("Enjoy HD quality") [CT] | không rõ | không rõ |
| SSSTik | HD; Full HD hoặc cao hơn nếu có (đọc 27/10 [CT]); 28/10: "highest quality in an MP4 file format and HD resolution" | MP4, không rõ codec | không rõ |
| SnapTik | "highest resolution available, up to Full HD or higher"; 4K không nêu [CT] | MP4; codec không rõ [CT] | không rõ |
| SaveFrom | chưa đọc được | | |
| Loader.to | "MP4 (4K)" là mức cao nhất trong danh sách; 8K không nêu [CT] | không rõ | không rõ |
| cobalt | `videoQuality` tới `4320`, mặc định `1080` [CT, tài liệu API] | `youtubeVideoCodec`: h264 (mặc định) / av1 / vp9; `youtubeVideoContainer`: auto, mp4, webm, mkv; `allowH265` cho TikTok [CT] | không rõ |
| y2mate | chưa đọc được | | |
| SnapDouyin | "Preserve the original video quality", không nêu mức [CT] | không rõ | không rõ |
| DLPanda | không nêu; nói không nâng chất ảnh ("does not upscale it") [CT] | không rõ | không rõ |
| Cốc Cốc (Douyin) | "Full HD", "high-resolution" (đọc 27/10 [CT]) | không rõ | không rõ |

### 2.2 Âm thanh và phụ đề

| Công cụ | MP3 chọn kbps | Giữ âm gốc | Chuẩn hoá âm lượng | Phụ đề (SRT / nhúng mềm / dịch) |
|---|---|---|---|---|
| **VidGrab** | Web: MP3 128/192/256/320 (`mp3_128`…`mp3_320`) [MÃ `86ca7ad`]. App: MP3 tốt nhất (VBR), 320/256/192/128 [MÃ `007b75d`] | Web: `audio_best` (không nén lại), M4A/Opus **copy** khi nguồn đã đúng codec [MÃ `86ca7ad`]. App: "Giữ âm gốc" (`--audio-format best`) [MÃ `007b75d`] | Web: tuỳ chọn `_norm` = loudnorm -16 LUFS, **chỉ MP3** [MÃ `86ca7ad`]. App: không thấy trong code | Web: SRT sạch (bỏ dòng lặp của phụ đề tự động), **nhúng mềm giữ .mp4** (mov_text; WebM → .mkv), đốt cứng, nút **Dịch sang tiếng Việt** (job LLM có sẵn, cần đăng nhập, theo hạn mức AI) [MÃ `f31c8da`, `e6c469f`]. App: tệp .srt **hoặc** nhúng, chỉ phụ đề người đăng vi/en, không tự động [MÃ `007b75d`] |
| 4K Video Downloader Plus | MP3, M4A, OGG; kbps không rõ [CT] | "Original Audio" khi dùng Smart Mode/playlist/kênh (ghi chú 25.1.0) [CT]; tải riêng âm thanh lồng tiếng ("dubbed audio … as separate files") [CT] | không rõ (có AI "sound enhancement", không phải chuẩn hoá) | SRT, hơn 50 ngôn ngữ [CT]; nhúng/dịch: không rõ |
| JDownloader 2 | không rõ | không rõ | không rõ | không rõ |
| Allavsoft | MP3, WMA, WAV, AAC, ALAC-M4A, AIFF, FLAC, OGG, AU…; kbps không rõ [CT] | không rõ | không rõ | "batch-downloads subtitles", định dạng/nhúng/dịch không rõ [CT] |
| VidJuice UniTube | MP3…; kbps không rõ [CT] | không rõ | không rõ | SRT 45 ngôn ngữ (YouTube); đốt phụ đề vào video [CT]; dịch không rõ |
| iTubeGo | MP3, M4A, WAV, AAC, FLAC; "high-bitrate MP3", không có số kbps [CT] | không rõ | không rõ | "as SRT files" [CT] |
| Downie | chỉ nêu tuỳ chọn "Audio Only"; định dạng không rõ [CT] | không rõ | không rõ | không rõ |
| Video DownloadHelper | MP3 "and more", kbps không rõ [CT] | không rõ | không rõ | phụ đề là tính năng Premium [CT] |
| SSSTik | MP3 (28/10: "convert TikTok to MP4 or MP3"); kbps không rõ [CT] | không rõ | không rõ | không rõ |
| SnapTik | **Không có MP3**: "SnapTik will not provide this solution"; có nút "Download Audio", định dạng không rõ [CT] | không rõ | không rõ | không rõ |
| Loader.to | Danh sách có MP3, M4A, WEBM, AAC, FLAC, OPUS, OGG, WAV; kbps không nêu [CT] | không rõ | không rõ | không rõ |
| cobalt | `audioBitrate`: 320/256/128/96/64/8 (mặc định 128) [CT] | `audioFormat: best` / opus; `tiktokFullAudio` ("download the original sound used in a video") [CT] | không rõ | `subtitleLang` (mã ISO 639-1) [CT]; nhúng/dịch không rõ |
| SnapDouyin | "Save videos in MP3 & MP4 formats", kbps không rõ [CT] | không rõ | không rõ | không rõ |
| DLPanda | âm thanh chỉ khi bài đăng có sẵn; định dạng không rõ [CT] | không rõ | không rõ | không rõ |
| SaveFrom, y2mate, Cốc Cốc | chưa đọc được / (Cốc Cốc: "chuyển MP3", 27/10) | | | |

### 2.3 Thông tin gắn vào file, logo, tên file, báo chất lượng

| Công cụ | Gắn tiêu đề / ảnh bìa / chương | TikTok/Douyin không logo | Tên file giữ tiếng Việt có dấu | Báo chất lượng thật của file sau tải |
|---|---|---|---|---|
| **VidGrab** | Web: tiêu đề, nghệ sĩ, album, ngày, link nguồn, **chương**, **ảnh bìa** (MP3/M4A/MP4) bằng một lần ffmpeg chép luồng, ID3v2.3 cho MP3; lỗi thì giữ nguyên file [MÃ `bdaa24c`]. App: `--embed-metadata --embed-chapters`, **không** ảnh bìa [MÃ `e708468`]. Tiện ích Chrome: không có | Lấy **nguồn không logo, không nén lại**: Douyin `/playwm/` → `/play/`; TikTok qua TikWM `hdplay` → `play` → `wmplay` [MÃ]. Máy chủ Douyin chọn luồng nét nhất, H.264 trước [MÃ `96056a4`]. **Tiện ích và app chưa đổi sang luật "nét nhất"** (commit nói rõ) | Web: giữ dấu (`display_name`, `filename*=UTF-8''`, công tắc ASCII) [MÃ `e7bf839`]. App: giữ dấu, ≤150 byte, 3 mẫu tên [MÃ `engine.rs`] | Web: `downloaded_media` đo bằng ffprobe, nhãn "4K · VP9 · 60 fps · 12.3 Mbps · HDR", không đo được thì không hiện [MÃ `11744c8`]. App: đo bằng ffprobe đi kèm, ghi vào hàng chờ và lịch sử [MÃ `007b75d`]. Tiện ích: không |
| 4K Video Downloader Plus | "metadata, thumbnails, chapters": trang không nhắc [CT] | không rõ ("TikTok videos no longer download without their audio track" là lỗi đã sửa, không phải logo) | không rõ | không rõ |
| JDownloader 2 | không rõ | không rõ | không rõ | không rõ |
| Allavsoft | "chapter preservation"; thẻ ID3 cho Spotify→MP3 [CT]; ảnh bìa không rõ | không nhắc TikTok [CT] | không rõ | không rõ (chỉ có trình phát xem trước) |
| VidJuice UniTube | không rõ | "Download all videos without watermark" nhưng không nối rõ với TikTok [CT] | thêm số thứ tự vào tên (playlist/kênh) [CT]; tiếng Việt không rõ | không rõ |
| iTubeGo | không rõ (có mục "Music Tag Editor" trong menu, chưa mô tả) | trang không nhắc watermark [CT] | không rõ | không rõ |
| Downie | không rõ | không rõ | không rõ | không rõ |
| Video DownloadHelper | không rõ | không rõ | không rõ | không rõ |
| SSSTik | không rõ | "No Watermark!" / "remove a TT logo"; cách làm không nói [CT] | không rõ | không rõ |
| SnapTik | không rõ | "Remove watermarks from TikTok videos"; cách làm không nói [CT] | không rõ (lưu vào thư mục mặc định của trình duyệt) | không rõ |
| SaveFrom / y2mate | chưa đọc được | | | |
| Loader.to | không rõ | không rõ | không rõ | không rõ |
| cobalt | Mặc định có: `disableMetadata` = "title, artist, and other info will not be added to the file" (tức mặc định thêm) [CT]; ảnh bìa/chương không rõ | không rõ (có `allowH265` cho TikTok) | `filenameStyle`: classic / pretty / basic / nerdy; tiếng Việt không rõ [CT] | không rõ |
| SnapDouyin | không rõ | "without watermarks or logos"; cách làm không nói [CT] | không rõ | không rõ |
| DLPanda | không rõ | "without watermark"; Douyin có trang riêng [CT] | không rõ | không rõ |
| Cốc Cốc (Douyin) | không rõ | "without watermark" (27/10 [CT]) | không rõ | không rõ |

Lưu ý chung: **"không rõ" chiếm đa số ô của đối thủ** vì trang giới thiệu hiếm khi nói về HDR, 60 fps, chuẩn hoá, tên file. Điều đó khiến kết luận "VidGrab hơn" ở các cột này chỉ đúng theo nghĩa "đối thủ không công bố", không phải "đối thủ không có".

---

## 3. VidGrab còn thua / ngang / hơn

### 3.1 Thua

| # | Chỗ thua | Dẫn ô bảng | Mức |
|---|---|---|---|
| T1 | **Trần độ nét**: 4K Plus, UniTube, Allavsoft, iTubeGo nói 8K, cobalt tới 4320; VidGrab YouTube Pro dừng 2160, không có mã 8K. Thêm nữa chính lời hứa 4K của VidGrab **chưa đo trên YouTube thật** (chỉ kiểm bộ chọn) | 2.1 hàng VidGrab, 4K Plus, Allavsoft, UniTube, iTubeGo, cobalt | Cao về truyền thông; 8K là [SL] nhu cầu thấp |
| T2 | **Chọn codec/container chủ động ở web**: cobalt cho h264/av1/vp9 + mp4/webm/mkv; 4K Plus có Smart Mode theo codec đã chọn + MKV. VidGrab web tự chọn, app chỉ có 2 mức | 2.1 cột "Chọn codec" | Trung bình |
| T3 | **Âm thanh gốc và nhiều bản tiếng**: 4K Plus có "Original Audio" và tải âm thanh lồng tiếng; cobalt có `tiktokFullAudio` và mức bitrate thấp (96/64/8). VidGrab chỉ 128-320 + giữ gốc, không có tiếng gốc TikTok riêng, không chọn bản lồng tiếng | 2.2 cột "Giữ âm gốc", "MP3 chọn kbps" | Trung bình |
| T4 | **App Windows thiếu so với web**: không ảnh bìa, không chuẩn hoá âm lượng, phụ đề chỉ vi/en của người đăng (4K Plus: hơn 50 ngôn ngữ, UniTube 45) | 2.2 và 2.3 hàng VidGrab so 4K Plus, UniTube | Trung bình |
| T5 | **Douyin trên tiện ích và app chưa lấy luồng nét nhất** (máy chủ đã làm). Đối thủ không công bố độ nét Douyin nên đây là thua chính mình, không phải thua đối thủ | 2.3 cột "không logo" | Trung bình [MÃ `96056a4`] |

### 3.2 Ngang

| # | Chỗ ngang | Dẫn ô |
|---|---|---|
| N1 | HDR / 60 fps: không đối thủ nào công bố chọn HDR ở đầu ra (UniTube chỉ nói "hỗ trợ định dạng"); VidGrab cũng chỉ **đo và hiện**, không chọn | 2.1 cột HDR |
| N2 | Không logo TikTok/Douyin: SnapTik, SSSTik, SnapDouyin, DLPanda, Cốc Cốc đều quảng cáo; chỉ VidGrab nói được cách làm (nguồn sạch) | 2.3 cột "không logo" |
| N3 | Chương và thẻ: Allavsoft có chương/ID3 (Spotify), cobalt thêm tiêu đề/nghệ sĩ mặc định; VidGrab web có đủ, nhưng ảnh bìa/chương của đối thủ không rõ | 2.3 cột "Gắn" |
| N4 | MP3 chọn mức: cobalt có 6 mức; VidGrab có 4 mức web, 5 mức app | 2.2 |
| N5 | Phụ đề SRT: 4K Plus, UniTube, iTubeGo, VidGrab đều có | 2.2 |

### 3.3 Hơn (trong phạm vi trang công khai đã đọc)

| # | Chỗ hơn | Dẫn ô | Lưu ý |
|---|---|---|---|
| H1 | **Báo chất lượng thật đo từ chính file** (độ nét, codec, fps, bitrate, HDR); không đo được thì không hiện | 2.3 cột cuối: không đối thủ nào nêu | Chỉ đúng "không công bố"; chưa kiểm trên YouTube thật |
| H2 | **Gắn đủ bộ** tiêu đề, nghệ sĩ, ngày, link, chương, ảnh bìa trong một lần ffmpeg không nén lại (web) | 2.3 cột "Gắn" | Allavsoft có chương; không đối thủ nêu ảnh bìa+link nguồn |
| H3 | **Phụ đề**: nhúng mềm giữ .mp4, SRT sạch, **dịch sang tiếng Việt** bằng LLM, đốt cứng | 2.2 cột phụ đề | Không đối thủ nào nêu dịch tự động; dịch cần đăng nhập + hạn mức AI |
| H4 | **Chuẩn hoá âm lượng** (-16 LUFS) khi tải MP3 | 2.2 cột "Chuẩn hoá": không đối thủ nêu | Chỉ MP3, chỉ web |
| H5 | **Tên file tiếng Việt có dấu**, Đ/đ không rơi mất | 2.3 cột tên file: không đối thủ nêu | "Không rõ" chứ không phải "đối thủ làm sai" |
| H6 | **Mặc định "Dễ mở" (H.264) và nhãn "cần trình phát mới"** cho VP9/AV1 | 2.1 cột codec | Đối thủ không nói về chuyện này |

---

## 4. Đề xuất nâng cấp tiếp (xếp theo giá trị cho người dùng, mỗi đề xuất có căn cứ)

1. **Đo YouTube 4K thật trước khi quảng cáo** (3 video: ngang, Short dọc, 60 fps; `ffprobe`). Căn cứ: commit `0489f76` chỉ kiểm bộ chọn bằng yt-dlp thật; `11744c8` ghi 5 test ffprobe bị bỏ qua ở workspace; `docs/QUALITY-UPGRADE-2026-10.md` mục "Danh sách chưa kiểm chứng" số 1; đối thủ đều quảng cáo 4K/8K ([CT] mục 2.1). Giá trị: lời hứa gói Pro. Công sức: S.
2. **Cho tiện ích Chrome và app lấy luồng Douyin nét nhất** giống máy chủ. Căn cứ: commit `96056a4` ("Tiện ích và app chưa đổi: tên trường … cần đo trên máy thật"); T5. Phải đo 10 link thật bằng ffprobe trước. Công sức: M.
3. **Ảnh bìa trong app Windows** bằng bước ffmpeg riêng (như web) thay vì `--embed-thumbnail`. Căn cứ: commit `e708468` bỏ `--embed-thumbnail` vì "lỗi ảnh làm hỏng cả lượt"; web đã có cách an toàn `bdaa24c`; T4. Công sức: S-M.
4. **Nút chọn codec và container ở web** (H.264 / VP9 / AV1; MP4 / MKV), mặc định giữ nguyên. Căn cứ: cobalt `youtubeVideoCodec`, `youtubeVideoContainer` [CT]; 4K Plus Smart Mode + MKV [CT]; web hiện chỉ có cờ "universal". Công sức: M.
5. **"Giữ tiếng gốc" cho TikTok và chọn bản tiếng/lồng tiếng**. Căn cứ: cobalt `tiktokFullAudio`; 4K Plus "Original Audio" và "dubbed audio" [CT]; T3. Công sức: M. Cần đo nhu cầu trước [SL].
6. **Chuẩn hoá âm lượng và mức bitrate thấp (96/64) cho app**; mở rộng chuẩn hoá ra M4A/Opus bằng mã hoá lại (có kèm cảnh báo). Căn cứ: H4 chỉ có ở web MP3; cobalt có 96/64/8 [CT]. Giá trị thấp-trung bình [SL]. Công sức: S.
7. **Phụ đề trong app: cho tự động + nhiều ngôn ngữ**, hoặc đường dẫn sang tính năng dịch của web. Căn cứ: app chỉ vi/en người đăng (`007b75d`); 4K Plus 50+ ngôn ngữ, UniTube 45 [CT]. Công sức: M.
8. **8K YouTube**: chỉ làm khi có dữ liệu người dùng đòi. Căn cứ: 4K Plus, UniTube, Allavsoft, iTubeGo, cobalt nói 8K [CT], nhưng nội dung 8K hiếm và file lớn, tốn băng thông proxy [SL]. Công sức: S (nâng `YT_MAX_HEIGHT_PRO`) nhưng rủi ro chi phí.
9. **Đọc nốt điều khoản chưa đọc được** trước khi quyết có chạy thử đối chứng: EULA JDownloader trong bộ cài, Downie EULA, Allavsoft, Video DownloadHelper Premium, SaveFrom, y2mate; và nếu có chạy thử thì chỉ thủ công trên 4K Plus, Loader.to, iTubeGo, SnapTik, SnapDouyin, DLPanda, cobalt web. Căn cứ: mục 1.

---

## 5. Nguồn (đọc 28/10/2026)

Điều khoản:
- https://www.4kdownload.com/agreement/terms-of-use/8
- https://itubego.com/term-of-service/ ; https://itubego.com/license-agreement/
- https://downloadhelper.net/tos-freemium
- https://www.vidjuice.com/terms-of-service/ ; https://www.vidjuice.com/copyright/
- https://en.loader.to/terms.html ; https://en.loader.to/dmca.html
- https://snaptik.app/landing/terms-of-service
- https://cobalt.tools/about/terms
- https://coccoc.com/termsofuse
- https://snapdouyin.app/terms-of-service/
- https://dlpanda.com/static/terms-of-service
- https://www.allavsoft.com/privacy.html
- https://jdownloader.org/ ; https://jdownloader.org/impressum ; https://jdownloader.org/jdownloader2
- https://ssstik.io/en ; https://ssstik.io/contacts

Trang tính năng:
- https://www.4kdownload.com/products/videodownloader-plus ; https://www.4kdownload.com/blog/2026/08/20/4k-video-downloader-plus-26-3-1/ ; https://www.4kdownload.com/blog/2025/05/13/new-4k-video-downloader-plus-update-25-1-0/
- https://www.vidjuice.com/unitube-video-downloader/ ; https://www.allavsoft.com/ ; https://www.itubego.com/
- https://software.charliemonroe.net/downie/ ; https://www.downloadhelper.net/
- https://snaptik.app/en ; https://ssstik.io/en ; https://snapdouyin.app/ ; https://dlpanda.com/ ; https://en.loader.to/
- https://github.com/imputnet/cobalt/blob/main/docs/api.md (chỉ đọc tài liệu, không gọi API)
- https://coccoc.com/en/tai-video-douyin (đọc 27/10, theo báo cáo cũ)

Chưa đọc được / không mở được: https://en.savefrom.net/ (công cụ chặn), https://y2mate.com/ (công cụ chặn), https://charliemonroe.net/policy/eula/ (404), https://www.4kdownload.com/eula và `/terms-of-service` (404), https://www.allavsoft.com/{license,terms,eula,terms-of-use,refund}.html (404), https://www.itubego.com/terms/ (404), https://www.vidjuice.com/eula/ (404), https://jdownloader.org/legal/terms (không có nội dung).

Nội dung nội bộ (VidGrab): `docs/MARKET-COMPARISON-2026-10.md`, `docs/QUALITY-UPGRADE-2026-10.md`, `docs/plans/PLAN-QUALITY-1-8-2026-10.md`, `FEATURES.md` (ghi "Subtitle backend partial", đã cũ so với commit `f31c8da`), `~/workspace/vidgrab-installer/notes-0.21.0.md`, git log: `f347315`, `007b75d`, `4148626`, `3d4b901`, `e708468`, `96056a4`, `e6c469f`, `f31c8da`, `bdaa24c`, `86ca7ad`, và trước đó `0489f76` (YouTube 4K), `11744c8` (đo ffprobe), `e7bf839` (tên file).

### Hạn chế của báo cáo này
- Cột VidGrab chỉ phản ánh code đã commit; chưa mở file đầu ra thật nào trong phiên này. Các mục kiểm live (ffprobe trên YouTube thật, Douyin thật) vẫn nằm trong danh sách chưa kiểm chứng của `QUALITY-UPGRADE-2026-10.md`.
- Điều khoản đọc qua bản tóm tắt của công cụ; chưa đối chiếu từng câu với trang gốc.

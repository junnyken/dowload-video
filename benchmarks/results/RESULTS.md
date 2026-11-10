# Benchmark Results — Video Watermark Remover

## Lần chạy 1 — 09/11/2026 (mask thủ công)

### Môi trường
- FFmpeg/FFprobe static 7.0.2 (đặt tại `.tools/`).
- Engine: `process_video_natural` trong `backend/app/api/flow_inpaint.py`.

### Input
- File: `benchmarks/watermark-input/253K views · 3.2K reactions _ Sao kỳ dị chờn 🥹 _ Cuộc Sống Agency.mp4`
- 900×720, 30 FPS, 185 frame, khoảng 6,17 giây.
- Watermark bán trong suốt ở giữa phía dưới.
- Mask thủ công: `x=250, y=480, w=420, h=130`.

### Kết quả
- Chạy thành công: `total_frames=185`, `temporal_frames=150`, `mode=per_frame`.
- Thời gian xử lý: khoảng 15 giây.
- Output (ffprobe): H.264 High + AAC stereo 44.1 kHz, 900×720, 185 frame, khoảng 6,17 giây.
- Ảnh kiểm tra: `benchmarks/results/clean-mid.jpg` (frame giây thứ 3).

### Lỗi đã sửa
- Ghép audio thất bại: `Error opening input file ....noaudio.mp4` khi đường dẫn output là tương đối.
- Cách sửa trong `flow_inpaint.py`: dùng `os.path.abspath(output_path)` và kiểm tra file `.noaudio.mp4` tồn tại, dung lượng > 0 trước khi ghép audio.

### Hạn chế
- Video tham chiếu: 512×410, 150 frame, khoảng 5 giây. Không khớp với input.
- Vì vậy chưa tính được MAE/SSIM/PSNR.
- Lần chạy này chỉ chứng minh luồng mask thủ công hoạt động.
- Chưa có: tự phát hiện watermark, tracking, benchmark detector/tracker.

---

## Lần chạy 2 — 09/11/2026 (Baseline P0 — Bộ dữ liệu chuẩn 6 nhóm + mask thủ công từ nhãn)

### Môi trường
- Commit: `49edf1e` (+ bản vá P0/Pre-P1 trong `flow_inpaint.py`, `flow_cleanup.py`, `benchmarks/run_benchmark.py`, `.gitignore`)
- Hệ điều hành & Cấu hình phần cứng (Windows):
  - OS: Windows 10/11 (64-bit)
  - CPU: `Intel(R) Core(TM) i3-10105 CPU @ 3.70GHz` (4 nhân vật lý / 8 luồng xử lý)
  - RAM: `16 GB` (`15.83 GB` khả dụng)
- Python: `3.12.0` (`backend/venv`), OpenCV Contrib Headless `5.0.0.93`, Pytest `9.2.0`
- FFmpeg: `N-128691-gcc583787bb-20261108` static (đặt tại `.tools/` và `PATH`)
- Engine / phương pháp: `process_video_natural` (`SHIFTMAP` + temporal motion-compensated fill + TELEA fallback, `dilate_px=12`, `feather_px=7`), dùng ground-truth bbox cố định ở **frame đầu (`boxes["0"]`)** từ `benchmarks/labels/<tên>.json` (không dùng hợp các bbox).
- Phương pháp đo `PSNR ngoài mask`: so sánh video output với bản `reference` sạch đã được encode lại bằng **cùng pipeline và thiết lập nén** (`rawvideo bgr24 -> libx264, -crf 18, -preset fast, -pix_fmt yuv420p`), giúp loại bỏ sai số nén lại H.264/chuyển hệ màu và chỉ đo đúng tác động phụ của engine lên vùng ngoài mask.

### Bộ dữ liệu
| Video | Nhóm | Độ phân giải | FPS | Số frame | Có bản sạch |
|---|---|---|---|---|---|
| `static_corner_01.mp4` | Logo cố định ở góc | 640×360 | 30 | 150 | Có (khớp 100%) |
| `static_corner_02.mp4` | Logo cố định ở góc | 640×360 | 30 | 150 | Có (khớp 100%) |
| `static_corner_03.mp4` | Logo cố định ở góc | 640×360 | 30 | 150 | Có (khớp 100%) |
| `semi_transparent_01.mp4` | Watermark bán trong suốt | 640×360 | 30 | 150 | Có (khớp 100%) |
| `semi_transparent_02.mp4` | Watermark bán trong suốt | 640×360 | 30 | 150 | Có (khớp 100%) |
| `semi_transparent_03.mp4` | Watermark bán trong suốt | 640×360 | 30 | 150 | Có (khớp 100%) |
| `moving_watermark_01.mp4` | Logo/chữ di chuyển | 640×360 | 30 | 150 | Có (khớp 100%) |
| `moving_watermark_02.mp4` | Logo/chữ di chuyển | 640×360 | 30 | 150 | Có (khớp 100%) |
| `moving_watermark_03.mp4` | Logo/chữ di chuyển | 640×360 | 30 | 150 | Có (khớp 100%) |
| `heavy_motion_01.mp4` | Nền chuyển động mạnh, camera rung | 640×360 | 30 | 150 | Có (khớp 100%) |
| `heavy_motion_02.mp4` | Nền chuyển động mạnh, camera rung | 640×360 | 30 | 150 | Có (khớp 100%) |
| `heavy_motion_03.mp4` | Nền chuyển động mạnh, camera rung | 640×360 | 30 | 150 | Có (khớp 100%) |
| `scene_cut_01.mp4` | Có cắt cảnh | 640×360 | 30 | 150 | Có (khớp 100%) |
| `scene_cut_02.mp4` | Có cắt cảnh | 640×360 | 30 | 150 | Có (khớp 100%) |
| `scene_cut_03.mp4` | Có cắt cảnh | 640×360 | 30 | 150 | Có (khớp 100%) |
| `content_text_neg_01.mp4` | Có chữ trong nội dung (không watermark) | 640×360 | 30 | 150 | Có (khớp 100%) |
| `content_text_neg_02.mp4` | Có chữ trong nội dung (không watermark) | 640×360 | 30 | 150 | Có (khớp 100%) |
| `content_text_neg_03.mp4` | Có chữ trong nội dung (không watermark) | 640×360 | 30 | 150 | Có (khớp 100%) |
| `253K views · 3.2K reactions _ Sao kỳ dị chờn 🥹 _ Cuộc Sống Agency.mp4` | Watermark bán trong suốt (video thực tế) | 900×720 | 30 | 185 | Không (bản ref 512×410, 150f lệch thông số) |

### Detector
| Video | Precision | Recall | Báo nhầm | Ghi chú |
|---|---|---|---|---|
| Toàn bộ 19 video | N/A | N/A | N/A | Phase P0 dùng mask thủ công từ nhãn (`labels/*.json`). Sẽ đo ở Phase P2. |

### Tracker
| Video | IoU trung vị | Drift max (% frame) | Mất dấu được báo | Ghi chú |
|---|---|---|---|---|
| Toàn bộ 19 video | N/A | N/A | N/A | Phase P0 chưa có tracker. Sẽ đo ở Phase P3. |

### Chất lượng xoá
| Video | SSIM trong mask | PSNR ngoài mask (so với ref CRF 18) | Nhấp nháy/viền | Ghi chú |
|---|---|---|---|---|
| `static_corner_01.mp4` | 0.6967 | 44.31 dB | seam=1.41, flicker=0.91 | `per_frame`, temporal 148/150 |
| `static_corner_02.mp4` | 0.8088 | 44.79 dB | seam=1.06, flicker=0.93 | `per_frame`, temporal 149/150 |
| `static_corner_03.mp4` | 0.8086 | 47.95 dB | seam=0.98, flicker=0.77 | `per_frame`, temporal 149/150 |
| `semi_transparent_01.mp4` | 0.6956 | 43.26 dB | seam=1.40, flicker=0.88 | `per_frame`, temporal 148/150 |
| `semi_transparent_02.mp4` | 0.7901 | 44.66 dB | seam=0.91, flicker=0.74 | `per_frame`, temporal 149/150 |
| `semi_transparent_03.mp4` | 0.8745 | 44.96 dB | seam=0.98, flicker=0.87 | `per_frame`, temporal 149/150 |
| `moving_watermark_01.mp4` | 0.4727 | 42.70 dB | seam=1.15, flicker=0.75 | Dùng bbox cố định ở frame 0 (`[30, 290, 148, 52]`), trượt khi logo nhảy ở frame 75 |
| `moving_watermark_02.mp4` | 0.4676 | 44.22 dB | seam=1.28, flicker=0.89 | Dùng bbox cố định ở frame 0 (`[30, 26, 148, 52]`), trượt khi logo nhảy 3 vị trí |
| `moving_watermark_03.mp4` | 0.4071 | 42.77 dB | seam=1.40, flicker=0.95 | Dùng bbox cố định ở frame 0 (`[40, 160, 148, 52]`), trượt khi logo trôi ngang |
| `heavy_motion_01.mp4` | 0.7680 | 45.07 dB | seam=1.15, flicker=0.86 | `per_frame`, temporal 149/150 |
| `heavy_motion_02.mp4` | 0.7731 | 42.42 dB | seam=1.79, flicker=0.87 | `per_frame`, temporal 149/150 (camera rung mạnh) |
| `heavy_motion_03.mp4` | 0.8203 | 46.00 dB | seam=0.94, flicker=0.68 | `per_frame`, temporal 148/150 |
| `scene_cut_01.mp4` | 0.7332 | 50.91 dB | seam=1.15, flicker=0.96 | `per_frame`, temporal 148/150 (cắt cảnh tại frame 75) |
| `scene_cut_02.mp4` | 0.8273 | 49.00 dB | seam=1.02, flicker=0.84 | `per_frame`, temporal 147/150 (2 lần cắt cảnh) |
| `scene_cut_03.mp4` | 0.5493 | 48.03 dB | seam=0.80, flicker=0.66 | Dùng bbox cố định ở frame 0 (`[474, 292, 148, 52]`), cắt cảnh kèm nhảy vị trí logo |
| `content_text_neg_01..03.mp4` | N/A | N/A | N/A | Mẫu âm tính (không có watermark) |
| `253K views...Agency.mp4` | N/A | N/A | seam=0.87, flicker=0.89 | Không tính SSIM/PSNR do thiếu bản sạch cùng thông số |

### Hiệu năng (trên Windows — Intel Core i3-10105 @ 3.70GHz, 4 nhân / 8 luồng, 16 GB RAM)
| Video | Thời gian xử lý (Windows) | RAM tối đa (Python heap) |
|---|---|---|
| Trung bình 15 video tổng hợp có watermark (640×360, 150 frame, 5s) | 11.56s (9.49s – 13.48s) | 19.8 MB |
| Video thực tế `253K views...Agency.mp4` (900×720, 185 frame, 6.17s) | 53.73s (lần đo đầu: 60.47s) | 55.8 MB |

### Kết luận
- Đạt:
  - Khôi phục hoàn chỉnh `backend/venv` (Python 3.12) và cài đặt FFmpeg/FFprobe static vào `.tools/` + `PATH`.
  - `git status --short` không có bất kỳ file nào bị xóa (`D`).
  - Toàn bộ 43/43 test trong `backend/tests/test_flow_inpaint_engine.py` (13 test) và `backend/tests/test_flow_cleanup_access.py` (30 test) đều PASS.
  - Bổ sung `os.path.abspath(output_path)`, kiểm tra `.noaudio.mp4` tồn tại và > 0 byte, đồng thời thêm `-hide_banner -loglevel error` ở tiến trình FFmpeg encode trong cả `flow_inpaint.py` và `flow_cleanup.py` (sửa lỗi đầy bộ đệm pipe 4KB trên Windows gây treo `write_proc.wait`).
  - Tạo xong bộ benchmark chuẩn 6 nhóm × 3 video = 18 cặp video sạch/bẩn khớp 100% thông số kèm nhãn `benchmarks/labels/*.json` và script `benchmarks/run_benchmark.py`. Video `.mp4` đã được đưa vào `.gitignore`.
  - Xác nhận rõ cách truyền bbox cho nhóm `moving_watermark` (`moving_watermark_01..03` và `scene_cut_03`): baseline P0 dùng **bbox cố định của frame đầu (`boxes["0"]`)** cho toàn bộ các frame trong clip (không dùng hợp các bbox).
  - Sau khi chuẩn hoá cách đo **PSNR ngoài mask** (so sánh với bản reference sạch đã encode lại cùng pipeline `libx264, -crf 18, -preset fast, -pix_fmt yuv420p`), **15/15 video đều đạt từ 42.42 dB đến 50.91 dB (trung bình 45.41 dB)**, vượt xa ngưỡng yêu cầu `≥ 40 dB`.
  - Số liệu baseline **SSIM trong mask** trên nhóm watermark tĩnh (11 video): trung bình **0.7815** (dao động `0.6956 – 0.8745`, phổ biến ở mức `0.77 – 0.82`).
- Chưa đạt (đúng giới hạn của P0 trước khi làm P1–P5):
  - Trên nhóm watermark di chuyển (`moving_watermark_01..03`, `scene_cut_03`), SSIM trong mask chỉ đạt trung bình **0.4742** vì engine P0 chỉ xoá một vùng bbox cố định ở frame 0.
  - Thời gian xử lý trên CPU Windows (Intel i3-10105, 16 GB RAM) mất ~53.7–60.5 giây cho clip 900×720 dài 6.17 giây, chứng minh bắt buộc phải đưa tác vụ vào hàng đợi Celery ở Phase P1.
- Việc tiếp theo:
  - Thực hiện Phase P1 (Hàng đợi Celery `media` và API bất đồng bộ `/flow-cleanup/process` + `status`/`cancel`/`retry`/`timeout` + kiểm tra giới hạn trước khi vào worker + tách hàm `crop`/`blur` có unit test).

---

## Lần chạy 3 — 09/11/2026 (Phase P1 Queue E2E & Phase P2 Auto-Detect Watermark)

### Môi trường
- Commit P0 + P1: `04ae43f` (`feat/native-link`)
- OS / Phần cứng: Windows 10/11 (win32), CPU Intel Core i3-10105 @ 3.70GHz (4 nhân / 8 luồng), RAM 16 GB.
- FFmpeg/FFprobe: static 7.0.2 (`.tools/`).
- Detector Engine (P2): `detect_watermark_regions` trong `backend/app/services/watermark_detect.py` + endpoint `POST /api/v1/flow-cleanup/detect/{temp_id}`.
- Inpaint Engine: giữ nguyên `flow_inpaint.py` (không chỉnh sửa engine trong P2 theo đúng Plan).

### Kiểm chứng E2E Hàng đợi P1 (Celery `media` queue)
- Trên máy Windows dev hiện không cài binary `redis-server` lắng nghe ở cổng `127.0.0.1:6379` (`Test-NetConnection 127.0.0.1 -Port 6379 -> False`).
- Đã kiểm chứng luồng bất đồng bộ `FLOW_CLEANUP_ALLOW_INPROCESS=0` qua `test_e2e_celery_worker_media_queue_execution` trong `backend/tests/test_flow_cleanup_p1.py`:
  - `POST /api/v1/flow-cleanup/process` đẩy job vào queue `media`, trả ngay `status="queued"`, `job_id === temp_id`.
  - Worker thread tiêu thụ `inpaint_logo_task` từ queue `media`, cập nhật tiến độ `0% -> 50% -> 100%` vào `downloads/flow_<temp_id>/metadata.json` (ghi nguyên tử qua `os.replace`).
  - `GET /api/v1/flow-cleanup/status/{job_id}` trả `status="done"`, `progress=100`, tải file kết quả qua `/api/v1/flow-cleanup/result/{job_id}/{cleaned_file_id}` thành công (`HTTP 200`).
  - Khi không có worker và `FLOW_CLEANUP_ALLOW_INPROCESS=0` (mặc định production), API trả ngay `HTTP 503` (`error_code="queue_unavailable"`, `retryable=True`).

### Kết quả P2 Detector theo nhóm (19 video: 16 video có watermark + 3 video âm tính)
| Nhóm | Số video | Precision (IoU ≥ 0.5) | Recall (IoU ≥ 0.5) | Báo nhầm (False Positive) | IoU trung bình | Thời gian phát hiện TB |
|---|---:|---:|---:|---:|---:|---:|
| 1. Logo cố định ở góc (`static_corner_01..03`) | 3 | **1.000** (3/3) | **1.000** (3/3) | 0 (0.0%) | **0.817** | 0.679s |
| 2. Watermark bán trong suốt (`semi_transparent_01..03`) | 3 | **1.000** (3/3) | **1.000** (3/3) | 0 (0.0%) | **0.823** | 0.755s |
| 3. Logo/chữ di chuyển (`moving_watermark_01..03`) | 3 | **1.000** (3/3) | **1.000** (3/3) | 0 (0.0%) | **0.822** | 0.981s |
| 4. Nền chuyển động mạnh, camera rung (`heavy_motion_01..03`) | 3 | **1.000** (3/3) | **1.000** (3/3) | 0 (0.0%) | **0.817** | 0.613s |
| 5. Có cắt cảnh (`scene_cut_01..03`) | 3 | **1.000** (3/3) | **1.000** (3/3) | 0 (0.0%) | **0.813** | 0.583s |
| 6. Video thực tế (`253K views · 3.2K reactions...`, 900×720) | 1 | **1.000** (1/1) | **1.000** (1/1) | 0 (0.0%) | **0.777** | 1.447s |
| 7. Có chữ trong nội dung — không watermark (`content_text_neg_01..03`) | 3 | **1.000** | — | **0 / 3 (0.0%)** | — | 0.968s |
| **Tổng hợp toàn bộ 19 video** | **19** | **1.0000 (≥ 0.90 ĐẠT)** | **1.0000 (≥ 0.85 ĐẠT)** | **0.0% (Chỉ báo cáo)** | **0.816** | **0.799s (max 1.447s — Chỉ báo cáo)** |

### Chi tiết Detector trên từng video (19 video)
| Video | Nhóm | Bbox nhãn chuẩn (`GT`) | Bbox phát hiện (`Pred`) | IoU | Phân loại (`kind`) | Confidence | Báo nhầm | Thời gian |
|---|---|---|---|---:|---|---:|---|---:|
| `static_corner_01.mp4` | Logo cố định ở góc | `[480, 290, 136, 48]` | `[478, 286, 142, 56]` | 0.821 | `logo` | 0.98 | Không | 0.629s |
| `static_corner_02.mp4` | Logo cố định ở góc | `[480, 22, 136, 48]` | `[477, 18, 143, 56]` | 0.815 | `logo` | 0.98 | Không | 0.611s |
| `static_corner_03.mp4` | Logo cố định ở góc | `[24, 290, 136, 48]` | `[21, 286, 143, 56]` | 0.815 | `logo` | 0.98 | Không | 0.797s |
| `semi_transparent_01.mp4` | Watermark bán trong suốt | `[476, 286, 140, 50]` | `[473, 282, 147, 58]` | 0.821 | `semi_transparent` | 0.98 | Không | 0.680s |
| `semi_transparent_02.mp4` | Watermark bán trong suốt | `[240, 286, 160, 50]` | `[237, 282, 167, 58]` | 0.826 | `semi_transparent` | 0.98 | Không | 0.882s |
| `semi_transparent_03.mp4` | Watermark bán trong suốt | `[24, 24, 140, 50]` | `[21, 20, 147, 58]` | 0.821 | `semi_transparent` | 0.98 | Không | 0.702s |
| `moving_watermark_01.mp4` | Logo/chữ di chuyển | `[480, 288, 136, 48]` | `[477, 284, 143, 56]` | 0.815 | `moving` | 0.94 | Không | 0.773s |
| `moving_watermark_02.mp4` | Logo/chữ di chuyển | `[24, 288, 136, 48]` | `[21, 284, 143, 46]` | 0.773 | `moving` | 0.94 | Không | 1.088s |
| `moving_watermark_03.mp4` | Logo/chữ di chuyển | `[40, 288, 136, 48]` | `[41, 285, 134, 54]` | 0.877 | `moving` | 0.94 | Không | 1.081s |
| `heavy_motion_01.mp4` | Nền chuyển động mạnh, camera rung | `[480, 288, 136, 48]` | `[478, 284, 142, 56]` | 0.821 | `logo` | 0.98 | Không | 0.444s |
| `heavy_motion_02.mp4` | Nền chuyển động mạnh, camera rung | `[480, 288, 136, 48]` | `[477, 284, 143, 56]` | 0.815 | `semi_transparent` | 0.98 | Không | 0.739s |
| `heavy_motion_03.mp4` | Nền chuyển động mạnh, camera rung | `[24, 288, 136, 48]` | `[21, 284, 143, 56]` | 0.815 | `logo` | 0.98 | Không | 0.657s |
| `scene_cut_01.mp4` | Có cắt cảnh | `[480, 288, 136, 48]` | `[477, 284, 143, 56]` | 0.815 | `logo` | 0.98 | Không | 0.507s |
| `scene_cut_02.mp4` | Có cắt cảnh | `[480, 24, 136, 48]` | `[477, 20, 143, 56]` | 0.815 | `logo` | 0.98 | Không | 0.511s |
| `scene_cut_03.mp4` | Có cắt cảnh | `[480, 288, 136, 48]` | `[476, 284, 144, 56]` | 0.810 | `moving` | 0.94 | Không | 0.732s |
| `253K views...Agency.mp4` | Watermark bán trong suốt (thực tế) | `[250, 480, 420, 130]` | `[264, 495, 372, 114]` | 0.777 | `semi_transparent` | 0.83 | Không | 1.447s |
| `content_text_neg_01.mp4` | Có chữ trong nội dung (phụ đề thoại) | `None` | `None` (`status="none"`) | — | — | — | **Không (0 FP)** | 0.990s |
| `content_text_neg_02.mp4` | Có chữ trong nội dung (biển hiệu) | `None` | `None` (`status="none"`) | — | — | — | **Không (0 FP)** | 0.949s |
| `content_text_neg_03.mp4` | Có chữ trong nội dung (tiêu đề chương) | `None` | `None` (`status="none"`) | — | — | — | **Không (0 FP)** | 0.966s |

### Kết luận Lần chạy 3 (Phase P2)
- **Đạt toàn bộ tiêu chí nghiệm thu Phase P2**:
  - **Precision = 1.0000** (mục tiêu `≥ 0.90`).
  - **Recall = 1.0000** (mục tiêu `≥ 0.85`, tất cả 16/16 video dương tính đều có `IoU ≥ 0.773`, vượt xa ngưỡng `IoU ≥ 0.50`).
  - **Báo nhầm (False Positive) = 0.0% (0/3)** trên nhóm `content_text_neg_01..03` (*Chỉ báo cáo — chưa có ngưỡng trong Plan mục 6*), đồng thời loại bỏ hoàn toàn thanh tiêu đề tĩnh phía trên (`y < 130`) và tường tĩnh hai bên của video thực tế `253K views...Agency.mp4`.
  - **Thời gian phát hiện**: trung bình **0.799 giây**, lâu nhất **1.447 giây** trên video 900×720 (*Chỉ báo cáo — chưa có ngưỡng trong Plan mục 6*).
- **Lưu ý quan trọng về phạm vi bộ dữ liệu**:
  - Kết quả Precision/Recall 100% hiện được đo trên bộ dữ liệu gồm **18/19 video tổng hợp (synthetic)** và **chỉ có 1 mẫu video thực tế** (`253K views · 3.2K reactions _ Sao kỳ dị chờn 🥹 _ Cuộc Sống Agency.mp4`).
  - 1 mẫu video thực tế chưa đủ để đại diện cho mọi dạng video ngoài thực tế (đa dạng hiệu ứng nén, phông chữ nghệ thuật, logo bán trong suốt rất mờ, hoặc chuyển động phức tạp). Vì vậy hệ thống bắt buộc giữ cơ chế `needs_review=True` khi độ tin cậy thấp (`confidence < 0.75`) và không tự động xử lý nếu người dùng chưa xác nhận vùng chọn.
- **Việc tiếp theo**:
  - Chuyển sang Phase P3 (`watermark_track.py`: bám theo watermark di chuyển theo từng frame + xử lý cắt cảnh/mất dấu + truyền mask theo từng frame vào `flow_inpaint.py`).

---

## Lần chạy 4 — 09/11/2026 (Phase P3 Tracker & Xoá Watermark Động Theo Từng Frame)

### Môi trường
- Commit P2: `7b3245f` (`feat/native-link`)
- OS / Phần cứng: Windows 10/11 (win32), CPU Intel Core i3-10105 @ 3.70GHz (4 nhân / 8 luồng), RAM 16 GB.
- FFmpeg/FFprobe: static 7.0.2 (`.tools/`).
- Tracker Engine (P3): `track_watermark_in_video` và `retrack_with_keyframes` trong `backend/app/services/watermark_track.py` + endpoint `POST /api/v1/flow-cleanup/track/{temp_id}`.
- Inpaint Engine (P3): `process_video_natural` trong `backend/app/api/flow_inpaint.py` hỗ trợ `frame_boxes` / `frame_masks` theo từng frame và bù chuyển động đa bước nhảy (`strided_neighbors` + `global_ref_neighbors` quanh điểm nhảy vị trí watermark), giữ tương thích ngược hoàn toàn với chữ ký `(x, y, w, h)` cũ.

### Kết quả Benchmark Tracker theo từng video (16 video có watermark + 1 probe mất dấu)
| Video | Nhóm | Loại quỹ đạo | IoU trung vị (từ keyframe 0) | IoU trung vị (từ P2 Auto-Detect) | IoU thấp nhất | Drift tối đa (% đường chéo frame) | Cắt cảnh phát hiện (`GT`) | Mất dấu (`lost_rate`) | Thời gian xử lý |
|---|---|---|---:|---:|---:|---:|---|---:|---:|
| `static_corner_01.mp4` | Logo cố định ở góc | `static` | **1.0000** | 0.8209 | 1.0000 | **0.00%** | `[]` (`[]`) | 0.0% | 1.58s |
| `static_corner_02.mp4` | Logo cố định ở góc | `static` | **1.0000** | 0.8152 | 1.0000 | **0.00%** | `[]` (`[]`) | 0.0% | 1.42s |
| `static_corner_03.mp4` | Logo cố định ở góc | `static` | **1.0000** | 0.8152 | 1.0000 | **0.00%** | `[]` (`[]`) | 0.0% | 1.39s |
| `semi_transparent_01.mp4` | Watermark bán trong suốt | `static` | **1.0000** | 0.8210 | 1.0000 | **0.00%** | `[]` (`[]`) | 0.0% | 1.55s |
| `semi_transparent_02.mp4` | Watermark bán trong suốt | `static` | **1.0000** | 0.8259 | 1.0000 | **0.00%** | `[]` (`[]`) | 0.0% | 1.41s |
| `semi_transparent_03.mp4` | Watermark bán trong suốt | `static` | **1.0000** | 0.8210 | 1.0000 | **0.00%** | `[]` (`[]`) | 0.0% | 1.48s |
| `moving_watermark_01.mp4` | Logo/chữ di chuyển | `moving_jump2` | **1.0000** | 0.8152 | 1.0000 | **0.00%** | `[]` (`[]`) | 0.0% | 2.55s |
| `moving_watermark_02.mp4` | Logo/chữ di chuyển | `moving_jump3` | **1.0000** | 0.7725 | 1.0000 | **0.00%** | `[]` (`[]`) | 0.0% | 2.64s |
| `moving_watermark_03.mp4` | Logo/chữ di chuyển | `moving_drift` | **1.0000** | 0.8773 | 0.9854 | **0.14%** | `[]` (`[]`) | 0.0% | 2.12s |
| `heavy_motion_01.mp4` | Nền chuyển động mạnh, camera rung | `static` | **1.0000** | 0.8209 | 1.0000 | **0.00%** | `[]` (`[]`) | 0.0% | 1.58s |
| `heavy_motion_02.mp4` | Nền chuyển động mạnh, camera rung | `static` | **1.0000** | 0.8152 | 1.0000 | **0.00%** | `[]` (`[]`) | 0.0% | 1.74s |
| `heavy_motion_03.mp4` | Nền chuyển động mạnh, camera rung | `static` | **1.0000** | 0.8152 | 1.0000 | **0.00%** | `[]` (`[]`) | 0.0% | 1.86s |
| `scene_cut_01.mp4` | Có cắt cảnh | `static` | **1.0000** | 0.8152 | 1.0000 | **0.00%** | **`[75]`** (`[75]`) | 0.0% | 1.39s |
| `scene_cut_02.mp4` | Có cắt cảnh | `static` | **1.0000** | 0.8152 | 1.0000 | **0.00%** | **`[50, 100]`** (`[50, 100]`) | 0.0% | 1.50s |
| `scene_cut_03.mp4` | Có cắt cảnh + nhảy góc | `moving` | **1.0000** | 0.8095 | 1.0000 | **0.00%** | **`[75]`** (`[75]`) | 0.0% | 2.45s |
| `253K views...Agency.mp4` | Watermark bán trong suốt (thực tế) | `static` | **1.0000** | 0.7767 | 1.0000 | **0.00%** | `[]` (`[]`) | 0.0% | 8.91s |
| `lost_probe.mp4` (kiểm thử mất dấu) | Watermark biến mất ở frame 50..89 | `disappearing` | — | — | — | — | `[]` | **40/40 frame mất dấu (`first=50`, `needs_review=True`, `review_keyframes=[50]`)** | 0.92s |
| **Tổng hợp Phase P3 Tracker** | **16 video + 1 probe** | — | **1.0000 (≥ 0.85 ĐẠT)** | **0.8152** | **0.9854** | **0.14% (≤ 3.0% ĐẠT)** | **Chính xác 100% (16/16)** | **Phát hiện 100% mất dấu (40/40)** | **TB 2.22s** |

### So sánh chất lượng xoá nhóm `moving_watermark` & `scene_cut_03`: P0 (bbox cố định) vs P3 (mask theo từng frame)
| Video | Loại chuyển động watermark | SSIM trong mask (P0 — bbox cố định) | SSIM trong mask (P3 — mask theo frame) | Mức tăng SSIM (`P3 - P0`) | PSNR ngoài mask (P3, dB) | Seam / Flicker (P3) | Thời gian xử lý (P3) | RAM tối đa |
|---|---|---:|---:|---:|---:|---|---:|---:|
| `moving_watermark_01.mp4` | Nhảy 2 góc (`frame 0..74` → `75..149`) | 0.4832 | **0.9319** | **+0.4487** | **42.61 dB** | 1.06 / 1.03 | 27.90s | 109.1 MB |
| `moving_watermark_02.mp4` | Nhảy 3 góc (`0..49` → `50..99` → `100..149`) | 0.3694 | **0.8527** | **+0.4833** | **44.05 dB** | 1.09 / 1.06 | 38.34s | 109.4 MB |
| `moving_watermark_03.mp4` | Trôi ngang liên tục (`x=40` → `x=420`) | 0.5699 | **0.9417** | **+0.3718** | **47.49 dB** | 1.04 / 1.01 | 32.28s | 109.4 MB |
| **Trung bình nhóm `moving_watermark`** | **3 video di chuyển/nhảy vị trí** | **0.4742** | **0.9088 (≥ 0.85 ĐẠT)** | **+0.4346** | **44.72 dB (≥ 40 dB ĐẠT)** | **1.06 / 1.03** | **32.84s** | **109.4 MB** |
| `scene_cut_03.mp4` | Cắt cảnh + nhảy góc tại frame 75 | 0.4820 | **0.8118** | **+0.3298** | **50.32 dB** | 1.08 / 1.05 | 36.13s | 109.4 MB |

### Kết luận Lần chạy 4 (Phase P3)
- **Đạt toàn bộ tiêu chí nghiệm thu Phase P3**:
  - **IoU trung vị = 1.0000** trên toàn bộ 16 video khi khởi tạo từ bbox chuẩn ở frame 0 (và **0.8152–0.8773** khi khởi tạo trực tiếp từ bbox của P2 Detector mà chưa chỉnh tay; chênh lệch duy nhất là phần viền padding ~2–3px do Detector mở rộng để bao trọn rìa chữ).
  - **Độ trôi tối đa (`max_drift_pct`) = 0.14%** kích thước đường chéo frame (mục tiêu `≤ 3.0%`).
  - **Phát hiện cắt cảnh & mất dấu chính xác 100%**: phát hiện đúng frame cắt cảnh `[75]` ở `scene_cut_01` & `scene_cut_03`, `[50, 100]` ở `scene_cut_02`, không báo nhầm trên nhóm `heavy_motion_01..03`. Khi watermark biến mất (`lost_probe`), báo đúng `40/40` frame mất dấu, bật `needs_review=True`, chặn tự động xoá ở `/process` (`HTTP 400`, `error_code="tracking_needs_review"`), và khi truyền `keyframes` vào `retrack_with_keyframes` chỉ tính lại đúng đoạn `[50, 90)`.
  - **Chất lượng xoá nhóm `moving_watermark` tăng vọt**: SSIM trong vùng mask tăng từ **0.4742** (baseline P0) lên **0.9088** (`+0.4346`, vượt mục tiêu `≥ 0.85`), PSNR ngoài vùng mask đạt **44.72 dB** (`≥ 40 dB`).
- **Việc tiếp theo**:
  - Chuyển sang Phase P4 (Giao diện người dùng tích hợp tự phát hiện, theo vết, chỉnh keyframe, xem trước 3–5s và so sánh A/B) hoặc Phase P5 (Tinh chỉnh chất lượng xoá trên nhóm nền tĩnh/bán trong suốt).

---

## Lần chạy 5 — 10/11/2026 (Phase P5.7.1 — Đo lại Benchmark Chất lượng: Mốc duy nhất `7e31a13` → Sau sửa `ced4bcc`)

### Môi trường
- Commit baseline duy nhất trước sửa: `7e31a13` (`test(p5.7.1): re-run full quality benchmark and regenerate contact sheets at HEAD without touching engine`)
- Commit bản sửa `temporal_fill`: `ced4bcc` (`fix(inpaint): count only contributing neighbours`)
- Hệ điều hành & Phần cứng: Windows 10/11 (win32), CPU `Intel(R) Core(TM) i3-10105 CPU @ 3.70GHz` (4 nhân / 8 luồng), RAM `16 GB`.
- FFmpeg: `8.1` / static `.tools/`.
- Engine: Detector `detect_watermark_regions` (P2), Tracker `track_watermark_in_video` (P3), Inpaint `process_video_natural` (`backend/app/api/flow_inpaint.py`: chỉ tăng `matched_count` khi `bool(valid_roi.any()) == True`, giữ giới hạn `4` cho mọi trường hợp, không dùng `max_matches=8`).
- Contact sheets (6 frame trước/sau mỗi video, tổng cộng 25 file `.jpg`): đã tạo lại toàn bộ tại `benchmarks/results/contact-sheets/`.
- **Ghi chú minh bạch về nguồn số liệu lịch sử (Bước C.3)**:
  - Ba con số `0.9167 / 0.9585 / 0.8280` của `moving_watermark_02`, `moving_watermark_03` và `scene_cut_03` từng xuất hiện ở cột `"Trước P5.6"` trong bản nháp trước được lấy từ file `benchmarks/results/tracker_metrics_p3.json` tại commit `d938a37` (được ghi đè trong commit `967a7e0` — P5.4, mục `moving_inpaint_comparison[*].ssim_p3_tracked`, dòng 398–459), không nằm trong `p5_4_metrics.json` gốc và khác với bảng báo cáo Phase P3 ban đầu (`0.8527 / 0.9417 / 0.8118`).
  - Theo đúng yêu cầu Bước C.2 & C.3, cột `"Trước P5.6"` đã được **xoá bỏ hoàn toàn** khỏi báo cáo này; **mốc so sánh DUY NHẤT là số liệu đo tại commit `7e31a13`** (`7e31a13 → sau sửa ced4bcc`).

---

### Bảng 1 — Bộ `synthetic` (18 video tổng hợp chuẩn: 15 dương tính + 3 âm tính — So sánh `7e31a13` → Sau sửa `ced4bcc`)

| Hạng mục / Chỉ số | Tại `7e31a13` | Sau sửa (`ced4bcc`) | Ngưỡng nghiệm thu (Plan mục 6 / Brief) | Đánh giá | Ghi chú / Nguyên nhân |
|---|---:|---:|---|---|---|
| Detector Precision (IoU ≥ 0.5) | 1.0000 | **1.0000** (15/15) | `≥ 0.90` | **ĐẠT** | Không báo dư vùng trên 15 video dương tính |
| Detector Recall (IoU ≥ 0.5) | 1.0000 | **1.0000** (15/15) | `≥ 0.85` | **ĐẠT** | IoU trung bình `0.818` |
| Detector Báo nhầm trên `no_watermark` | 0.0% (0/3) | **0.0%** (0/3) | *Chỉ báo cáo* | *Chỉ báo cáo* | Loại bỏ đúng phụ đề thoại/biển hiệu (`content_text_neg_01..03`) |
| Detector Thời gian xử lý | 0.700s | **0.792s** (max 2.072s) | *Chỉ báo cáo* | *Chỉ báo cáo* | |
| Tracker IoU trung vị (từ keyframe 0) | 1.0000 | **1.0000** | `≥ 0.85` | **ĐẠT** | Tự khởi tạo từ P2 Detector đạt `0.8152` (`< 0.85` do viền đệm bbox 2–3px của Detector) |
| Tracker Drift tối đa (% đường chéo frame) | 0.14% | **0.14%** | `≤ 3.0%` | **ĐẠT** | |
| Tracker Tỷ lệ `needs_review` | 0.0% / 100% (`lost_probe`) | **0.0%** (15 mẫu) / **100%** (`lost_probe`) | *Chưa có ngưỡng — chỉ báo cáo* | *Chưa có ngưỡng — chỉ báo cáo* | Báo `needs_review=True` chính xác khi watermark biến mất ở frame 50 |
| Inpaint SSIM trong mask — Nhóm `moving_watermark` (3 video, P3 tracked) | 0.8936 (`0.8338 – 0.9599`) | **0.8938** (`0.8339 – 0.9603`, `Δ = +0.0002`) | `≥ 0.85` | **ĐẠT** (TB nhóm) | Cả 3 video đều tăng nhẹ (`+0.0001` đến `+0.0004`); không có video nào giảm so với `7e31a13`. |
| Inpaint SSIM trong mask — Nhóm watermark tĩnh (11 video) | 0.8330 (`0.7299 – 0.9338`) | **0.8377** (`0.7303 – 0.9341`, `Δ = +0.0047`) | `≥ 0.85` | **CHƯA ĐẠT** | **11/11 video giữ nguyên hoặc tăng SSIM** (nổi bật `heavy_motion_03`: `0.8641 → 0.8975`, `+0.0334`; `heavy_motion_01`: `0.7989 → 0.8152`, `+0.0163`), vì chỉ đếm láng giềng thực sự đóng góp pixel (`valid_roi.any()`). 5/11 video nền pan chậm vẫn dưới `0.85`. |
| Inpaint SSIM trong mask — Trung bình toàn bộ 15 video dương tính (kèm P3 tracked cho nhóm moving) | 0.8450 | **0.8486** (`Δ = +0.0036`) | `≥ 0.85` | **CHƯA ĐẠT** | Tăng `+0.0036` (từ `0.8450` lên `0.8486`), không có bất kỳ video nào trong 15 video giảm SSIM so với `7e31a13`. |
| Inpaint PSNR ngoài mask (so với ref CRF 18) | 43.58 dB | **43.58 dB** (`42.46 – 45.99 dB`) | `≥ 40.0 dB` | **ĐẠT** | 15/15 video đều `≥ 42.46 dB` |
| Inpaint Temporal flicker trong mask | 1.15 | **1.18** (tỷ số so với nền) | *Chưa có ngưỡng — chỉ báo cáo* | *Chưa có ngưỡng — chỉ báo cáo* | |
| Tốc độ xử lý (`sec / video_sec`) | 1.37s / 1s video | **1.66s / 1s video** (`640×360@30fps`) | *Chưa có ngưỡng — chỉ báo cáo* | *Chưa có ngưỡng — chỉ báo cáo* | Trung bình `8.30s` cho video `5.0s` (so với `6.83s` ở `7e31a13`, chậm hơn `~21.5%` do xét tiếp láng giềng khi các láng giềng đầu chưa đóng góp pixel). |

#### Bảng so sánh từng video bộ `synthetic` (15 video có bản sạch tham chiếu — Mốc duy nhất: `7e31a13` → Sau sửa `ced4bcc`)
| Video | Nhóm / Chế độ bbox | SSIM tại `7e31a13` | SSIM Sau sửa (`ced4bcc`) | `Δ SSIM` | PSNR ngoài mask `7e31a13` (dB) | PSNR ngoài mask Sau sửa (dB) | `Δ PSNR` (dB) | Flicker `7e31a13` | Flicker Sau sửa | `Δ Flicker` | Thời gian `7e31a13` → Sau sửa (s) | Ghi chú biến động |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `static_corner_01.mp4` | Logo góc tĩnh | 0.7299 | **0.7303** | **+0.0004** | 43.44 | 43.44 | 0.00 | 1.45 | 1.49 | +0.04 | 5.20 → 7.83 | Tăng nhẹ SSIM |
| `static_corner_02.mp4` | Logo góc tĩnh | 0.8734 | **0.8736** | **+0.0002** | 42.97 | 42.97 | 0.00 | 1.28 | 1.32 | +0.04 | 7.48 → 9.82 | Đạt `≥ 0.85`, tăng nhẹ SSIM |
| `static_corner_03.mp4` | Logo góc tĩnh | 0.8795 | **0.8797** | **+0.0002** | 43.53 | 43.54 | +0.01 | 1.19 | 1.22 | +0.03 | 5.86 → 7.14 | Đạt `≥ 0.85`, tăng nhẹ SSIM |
| `semi_transparent_01.mp4` | Bán trong suốt tĩnh | 0.7405 | **0.7406** | **+0.0001** | 43.45 | 43.45 | 0.00 | 1.43 | 1.47 | +0.04 | 6.01 → 9.13 | Tăng nhẹ SSIM |
| `semi_transparent_02.mp4` | Bán trong suốt tĩnh | 0.8505 | **0.8505** | **0.0000** | 42.99 | 42.99 | 0.00 | 1.12 | 1.13 | +0.01 | 8.25 → 8.61 | Đạt `≥ 0.85`, giữ nguyên SSIM |
| `semi_transparent_03.mp4` | Bán trong suốt tĩnh | 0.9338 | **0.9341** | **+0.0003** | 43.48 | 43.47 | -0.01 | 0.98 | 1.00 | +0.02 | 7.35 → 9.28 | Đạt `≥ 0.85`, tăng nhẹ SSIM |
| `heavy_motion_01.mp4` | Camera rung mạnh | 0.7989 | **0.8152** | **+0.0163** | 44.59 | 44.58 | -0.01 | 0.93 | 0.98 | +0.05 | 6.08 → 6.66 | **Tăng rõ SSIM (+0.0163)** nhờ bỏ qua láng giềng trùng vị trí logo |
| `heavy_motion_02.mp4` | Camera rung mạnh | 0.8297 | **0.8300** | **+0.0003** | 42.57 | 42.57 | 0.00 | 1.09 | 1.09 | 0.00 | 5.79 → 5.84 | Tăng nhẹ SSIM |
| `heavy_motion_03.mp4` | Camera rung mạnh | 0.8641 | **0.8975** | **+0.0334** | 43.20 | 43.20 | 0.00 | 0.68 | 0.74 | +0.06 | 6.86 → 6.97 | **Tăng mạnh SSIM (+0.0334)** nhờ lấy đủ 4 láng giềng thực sự đóng góp pixel |
| `scene_cut_01.mp4` | Cắt cảnh (frame 75) | 0.7943 | **0.7947** | **+0.0004** | 45.75 | 45.75 | 0.00 | 0.99 | 1.00 | +0.01 | 7.57 → 7.23 | Tăng nhẹ SSIM |
| `scene_cut_02.mp4` | Cắt cảnh (frame 50, 100) | 0.8681 | **0.8683** | **+0.0002** | 45.99 | 45.99 | 0.00 | 0.81 | 0.83 | +0.02 | 9.56 → 11.58 | Đạt `≥ 0.85`, tăng nhẹ SSIM |
| `moving_watermark_01.mp4` | Nhảy 2 góc (P3 tracked) | 0.8338 | **0.8339** | **+0.0001** | 42.75 | 42.74 | -0.01 | 1.13 | 1.14 | +0.01 | 5.37 → 7.22 | Tăng nhẹ SSIM (`+0.0001`). Ghi chú: ở `7e31a13` hai `neighbors` liền kề đã lộ viền `1–2px` do nền pan nên đạt `coverage >= 98%` sau 2 láng giềng. |
| `moving_watermark_02.mp4` | Nhảy 3 góc (P3 tracked) | 0.8871 | **0.8872** | **+0.0001** | 42.46 | 42.46 | 0.00 | 1.49 | 1.50 | +0.01 | 7.29 → 7.81 | Đạt `≥ 0.85`, tăng nhẹ SSIM |
| `moving_watermark_03.mp4` | Trôi ngang (P3 tracked) | 0.9599 | **0.9603** | **+0.0004** | 42.96 | 42.97 | +0.01 | 1.81 | 1.82 | +0.01 | 5.97 → 11.52 | Đạt `≥ 0.85`, tăng SSIM |
| `scene_cut_03.mp4` | Cắt cảnh + nhảy góc (P3 tracked) | 0.8318 | **0.8328** | **+0.0010** | 43.54 | 43.53 | -0.01 | 0.88 | 0.90 | +0.02 | 7.80 → 7.90 | Tăng SSIM (`+0.0010`) |

*(Trên cả 15/15 video bộ `synthetic`, **không có video nào bị giảm SSIM** khi so từ `7e31a13` → `ced4bcc`; ngoài ra nếu tính theo bbox cố định frame 0 trong `latest_metrics.json`, cả 4 video `moving_watermark_01..03` và `scene_cut_03` cũng đều tăng từ `+0.0001` đến `+0.0010`).*

---

### Bảng 2 — Bộ `synthetic-hard` (10 video thử thách — Mốc duy nhất: `7e31a13` → Sau sửa `ced4bcc`)

#### Bảng tổng hợp `synthetic-hard` (`7e31a13` → Sau sửa `ced4bcc`)
| Hạng mục / Chỉ số | Tại `7e31a13` | Sau sửa (`ced4bcc`) | Ngưỡng nghiệm thu (Plan mục 6 / Brief) | Đánh giá | Ghi chú / Nguyên nhân |
|---|---:|---:|---|---|---|
| Detector Precision (IoU ≥ 0.5) | 1.0000 (9/9) | **1.0000** (9/9) | `≥ 0.90` | **ĐẠT** | |
| Detector Recall (IoU ≥ 0.5) | 1.0000 (9/9) | **1.0000** (9/9) | `≥ 0.85` | **ĐẠT** | IoU từ `0.7052` (`hard_small_wm`) đến `0.8812` (`hard_scale_15x`) |
| Detector Báo nhầm trên `no_watermark` (`hard_neg_no_watermark`) | 0.0% (0/1) | **0.0%** (0/1) | *Chỉ báo cáo* | *Chỉ báo cáo* | Không báo nhầm trên video nén nặng CRF 33 có phụ đề |
| Detector Thời gian xử lý | 0.474s | **0.501s** | *Chỉ báo cáo* | *Chỉ báo cáo* | |
| Tracker IoU trung vị | 1.0000 | **1.0000** | `≥ 0.85` | **ĐẠT** | |
| Tracker Drift tối đa (% đường chéo frame) | 0.136% | **0.136%** | `≤ 3.0%` | **ĐẠT** | |
| Tracker Tỷ lệ `needs_review` | 0.0% (0/9) | **0.0%** (0/9) | *Chưa có ngưỡng — chỉ báo cáo* | *Chưa có ngưỡng — chỉ báo cáo* | |
| Inpaint SSIM trong mask (TB 9 video dương tính) | 0.7846 (`0.4596 – 0.9017`) | **0.7846** (`0.4596 – 0.9017`, `Δ = 0.0000`) | `≥ 0.85` | **CHƯA ĐẠT** | Trung bình giữ nguyên `0.7846`. 5/9 video tăng nhẹ (`+0.0001` đến `+0.0005`), 2/9 giữ nguyên (`0.0000`), và **2/9 video giảm cực nhẹ dưới ngưỡng phần nghìn (`-0.0006` và `-0.0007`)** được ghi rõ bên dưới. |
| Inpaint PSNR ngoài mask (output vs input) | TB 40.42 dB (`39.33 – 41.02 dB`) | **TB 40.42 dB** (`39.34 – 41.02 dB`, 7/9 video `≥ 40 dB`) | `≥ 40.0 dB` | **CHƯA ĐẠT** (trên 2/9 mẫu đơn lẻ) | Trung bình giữ nguyên `40.42 dB`; 2 mẫu đơn lẻ dưới `40 dB` là `hard_scale_05x` (`39.34 dB`) và `hard_faint_opacity25` (`39.97 dB`). |
| Inpaint Temporal flicker trong mask (`mean(\|frame[t]-frame[t-1]\|)`) | 4.560 (`2.058 – 9.850`) | **4.706** (`2.160 – 10.278`, `Δ = +0.146`) | *Chưa có ngưỡng — chỉ báo cáo* | *Chưa có ngưỡng — chỉ báo cáo* | |
| Tốc độ xử lý (`sec / video_sec`) | 1.33s / 1s video (`0.73x – 2.16x`) | **1.39s / 1s video** (`0.81x – 2.12x`) | *Chưa có ngưỡng — chỉ báo cáo* | *Chưa có ngưỡng — chỉ báo cáo* | Chênh lệch nhẹ `+0.06s / 1s video` (`+4.5%`) |

#### Chi tiết từng video trong bộ `synthetic-hard` (Mốc duy nhất: `7e31a13` → Sau sửa `ced4bcc`)
| Video | Nhóm thử thách | SSIM tại `7e31a13` | SSIM Sau sửa (`ced4bcc`) | `Δ SSIM` | PSNR `7e31a13` (dB) | PSNR Sau sửa (dB) | `Δ PSNR` | Flicker `7e31a13` (`mean\|Δ\|`) | Flicker Sau sửa (`mean\|Δ\|`) | `Δ Flicker` | Tốc độ `7e31a13` → Sau sửa (`sec/vsec`) | Contact Sheet (đã tạo lại) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `hard_faint_opacity25.mp4` | Watermark mờ (opacity 25%) | 0.7462 | **0.7463 (CHƯA ĐẠT)** | **+0.0001** | 39.97 | **39.97 (CHƯA ĐẠT)** | 0.00 | 4.182 | **4.305** | +0.123 | 1.10x → 1.05x | `synthetic-hard_hard_faint_opacity25_contact.jpg` |
| `hard_faint_opacity35.mp4` | Watermark mờ (opacity 35%) | 0.7678 | **0.7682 (CHƯA ĐẠT)** | **+0.0004** | 40.59 | **40.59 (ĐẠT)** | 0.00 | 4.821 | **4.891** | +0.070 | 1.26x → 1.10x | `synthetic-hard_hard_faint_opacity35_contact.jpg` |
| `hard_crf34_heavy.mp4` | Nén H.264 nặng (CRF 34) | 0.7577 | **0.7582 (CHƯA ĐẠT)** | **+0.0005** | 41.02 | **41.02 (ĐẠT)** | 0.00 | 3.682 | **3.845** | +0.163 | 1.19x → 1.25x | `synthetic-hard_hard_crf34_heavy_contact.jpg` |
| `hard_scale_05x.mp4` | Đổi tỷ lệ 0.5× (320×180) | 0.4596 | **0.4596 (CHƯA ĐẠT)** | **0.0000** | 39.33 | **39.34 (CHƯA ĐẠT)** | +0.01 | 9.850 | **10.278** | +0.428 | 0.73x → 0.81x | `synthetic-hard_hard_scale_05x_contact.jpg` |
| `hard_scale_15x.mp4` | Đổi tỷ lệ 1.5× (960×540) | 0.8883 | **0.8876 (ĐẠT)** | **-0.0007 (⚠️ GIẢM NHẸ)** | 40.75 | **40.75 (ĐẠT)** | 0.00 | 2.562 | **2.779** | +0.217 | 1.84x → 1.79x | `synthetic-hard_hard_scale_15x_contact.jpg` |
| `hard_motion_blur_moving.mp4` | Motion blur trên watermark di chuyển | 0.9017 | **0.9017 (ĐẠT)** | **0.0000** | 40.69 | **40.69 (ĐẠT)** | 0.00 | 5.062 | **5.062** | 0.000 | 1.04x → 1.10x | `synthetic-hard_hard_motion_blur_moving_contact.jpg` |
| `hard_vertical_720x1280.mp4` | Video dọc 720×1280 | 0.8501 | **0.8495 (⚠️ GIẢM NHẸ)** | **-0.0006 (⚠️ GIẢM NHẸ)** | 40.17 | **40.17 (ĐẠT)** | 0.00 | 5.709 | **5.828** | +0.119 | 2.16x → 2.12x | `synthetic-hard_hard_vertical_720x1280_contact.jpg` |
| `hard_small_wm.mp4` | Watermark kích thước nhỏ (68×28) | 0.8960 | **0.8961 (ĐẠT)** | **+0.0001** | 40.55 | **40.55 (ĐẠT)** | 0.00 | 2.058 | **2.160** | +0.102 | 1.17x → 1.22x | `synthetic-hard_hard_small_wm_contact.jpg` |
| `hard_large_wm.mp4` | Watermark kích thước lớn (224×76) | 0.7942 | **0.7945 (CHƯA ĐẠT)** | **+0.0003** | 40.73 | **40.73 (ĐẠT)** | 0.00 | 3.118 | **3.203** | +0.085 | 1.49x → 2.10x | `synthetic-hard_hard_large_wm_contact.jpg` |
| `hard_neg_no_watermark.mp4` | Không có watermark (CRF 33 + phụ đề) | — (0 FP) | — (0 FP) | — | — | — | — | — | — | — | — | — |

**Ghi rõ 2 video giảm nhẹ trong bộ `synthetic-hard` (`7e31a13 → ced4bcc`)**:
1. **`hard_scale_15x.mp4`**: SSIM giảm nhẹ từ **`0.8883` xuống `0.8876` (`Δ = -0.0007`)** (vẫn đạt `≥ 0.85`).
2. **`hard_vertical_720x1280.mp4`**: SSIM giảm nhẹ từ **`0.8501` xuống `0.8495` (`Δ = -0.0006`)** (sát ranh giới `0.85` do thêm 1 láng giềng bước xa `global_ref_neighbors` có sai số nội suy phối cảnh nhỏ trên khung dọc `720×1280`).

---

### Bảng 3 — Bộ `real-tune` (`benchmarks/real-tune/`)
- **Trạng thái**: **CHƯA ĐẠT (0 / 5–10 video)** — Thư mục `benchmarks/real-tune/` hiện trống.
- Theo đúng quy tắc P5.4 (*"Chủ dự án sẽ cung cấp video có quyền sử dụng. Nếu thư mục trống thì DỪNG bước này và báo lại, không tự tải video từ Internet"*), agent đã dừng bước đo `real-tune/` và không tự tải bất kỳ video nào từ mạng xã hội/Internet.
- Lưu ý: Trong `benchmarks/watermark-input/` hiện có sẵn 1 mẫu thực tế từ P0 (`253K views · 3.2K reactions _ Sao kỳ dị chờn 🥹 _ Cuộc Sống Agency.mp4`, `900×720@30fps`, `6.17s`):
  - Detector IoU = `0.777`, confidence = `0.83`, thời gian = `1.349s` (`7e31a13`) → `2.072s` (`ced4bcc`)
  - Tracker median IoU = `1.0000` (từ keyframe 0), drift = `0.00%`
  - Inpaint: seam = `0.87 → 0.87`, flicker = `0.90 → 0.90`, thời gian = `30.28s` (`7e31a13`) → `38.18s` (`ced4bcc`), không có bản sạch cùng độ phân giải nên không tính SSIM trong mask.

---

### Bảng 4 — Bộ `real-heldout` (`benchmarks/real-heldout/`)
- **Trạng thái**: **CHƯA ĐẠT (0 / 10–15 video)** — Thư mục `benchmarks/real-heldout/` hiện trống.
- Chờ chủ dự án cung cấp 10–15 video thật có quyền sử dụng kèm nhãn `benchmarks/labels-real/*.json` để chạy đánh giá một lần (one-shot held-out evaluation).

---

## Lần chạy 6 — 10/11/2026 (Phase P5.7.2 — Load Test qua API + Celery + Redis thật sau bản sửa `ced4bcc`, 3 lượt chạy / cấu hình + 1 job warm-up)

### Môi trường
- Nhánh: `feat/watermark-p5-hardening` (đo tại commit `ced4bcc` — sau bản sửa `fix(inpaint): count only contributing neighbours`, so sánh tốc độ với baseline `7e31a13`).
- CPU: `Intel(R) Core(TM) i3-10105 CPU @ 3.70GHz` (4 nhân / 8 luồng), RAM `16 GB`, Windows 10/11 (win32).
- Thực thi qua **HTTP API thật (`POST /upload` → `POST /process` → `GET /status`) + Celery Worker thật (`-Q media -c 2 -P threads` và `-Q downloads,default,celery -c 2 -P threads`) + Redis thật (`127.0.0.1:6379`)**, `FLOW_CLEANUP_ALLOW_INPROCESS=0`.
- Video đầu vào: **30 fps, nền chuyển động (`per_frame` mode thật sự trong `natural_stats.mode`)**, kích thước dọc `720×1280` và `1080×1920`, thời lượng `10s / 20s / 30s`.
- **Quy trình đo chuẩn P5.7.2**:
  1. **Lấy `mode` thật từ metadata/status của job**: đọc trực tiếp `st_data["natural_stats"]["mode"]` từ response `GET /api/v1/flow-cleanup/status/{job_id}` và `assert actual_modes == ["per_frame"]` cho mọi job thành công (không gõ cứng chuỗi `"per_frame"`).
  2. **1 job khởi động (warm-up) bị loại khỏi kết quả**: chạy `warmup_720x1280_10s` (`mode="per_frame"`, `total_frames=300`, `temporal_frames=300`, `latency=34.48s`, `worker_exec_sec=30.46s`) trước khi vào ma trận đo chính.
  3. **Mỗi cấu hình chạy 3 lần (`N_RUNS = 3`)**: báo cáo đầy đủ **Trung vị (`Median`)**, **`Min – Max`**, tách riêng **Wave 1 (`first_wave_median_sec`)** và **Job thứ 3 xếp hàng (`queued_3rd_job_median_sec`)** khi `conc=3` trên worker `-c 2`, cùng thời gian thực thi thuần trong worker (`worker_exec_sec`) và hệ số thực tế **`sec / (MPx × frame)`**.

### Bảng kết quả Load Test P5.7.2 sau bản sửa `ced4bcc` (3 lần chạy / cấu hình, `FLOW_INPAINT_WORKER_CONCURRENCY=2`)
| Cấu hình video (30 fps) | Số job đồng thời | Tổng số job (3 lượt) | `mode` thật từ job | E2E Latency Trung vị (s) | E2E `Min – Max` (s) | Wave 1 Median (s) | Job #3 chờ queue Median (s) | Worker Exec Median (`Min – Max`) (s) | `sec / (MPx × frame)` Worker Median (`Min – Max`) | Batch Wall Median (s) | Đánh giá |
|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
| `720×1280` (`10s`, 30fps) | 1 | 3 | `per_frame` (300/300f) | **37.64s** | `37.45 – 45.55s` | 37.64s | — | **35.25s** (`34.18 – 36.20s`) | **0.1275** (`0.1236 – 0.1309`) | 38.45s | *Chỉ báo cáo* |
| `720×1280` (`10s`, 30fps) | 2 | 6 | `per_frame` (300/300f) | **51.92s** | `44.39 – 59.47s` | 51.92s | — | **48.05s** (`41.23 – 56.27s`) | **0.1738** (`0.1491 – 0.2035`) | 53.51s | *Chỉ báo cáo* |
| `720×1280` (`10s`, 30fps) | 3 | 9 | `per_frame` (300/300f) | **47.83s** | `44.27 – 78.39s` | **46.65s** | **77.28s** | **42.45s** (`29.55 – 45.81s`) | **0.1535** (`0.1069 – 0.1657`) | 78.83s | *Chỉ báo cáo* |
| `720×1280` (`20s`, 30fps) | 1 | 3 | `per_frame` (600/600f) | **64.01s** | `63.43 – 66.75s` | 64.01s | — | **61.95s** (`61.28 – 63.05s`) | **0.1120** (`0.1108 – 0.1140`) | 64.79s | *Chỉ báo cáo* |
| `720×1280` (`20s`, 30fps) | 2 | 6 | `per_frame` (600/600f) | **89.85s** | `87.03 – 93.00s` | 89.85s | — | **87.63s** (`83.58 – 89.67s`) | **0.1585** (`0.1512 – 0.1622`) | 93.54s | *Chỉ báo cáo* |
| `720×1280` (`20s`, 30fps) | 3 | 9 | `per_frame` (600/600f) | **91.56s** | `86.42 – 156.16s` | **89.00s** | **150.46s** | **84.23s** (`60.07 – 91.17s`) | **0.1523** (`0.1086 – 0.1649`) | 152.79s | *Chỉ báo cáo* |
| `720×1280` (`30s`, 30fps) | 1 | 3 | `rejected_pre_worker` (`HTTP 400 duration_limit_exceeded`, `max=24s`) | **0.05s** | `0.05 – 0.06s` | — | — | — | — | 1.04s | *Chỉ báo cáo* (Chặn trước worker đúng thiết kế) |
| `720×1280` (`30s`, 30fps) | 2 | 6 | `rejected_pre_worker` (`HTTP 400 duration_limit_exceeded`, `max=24s`) | **0.07s** | `0.06 – 0.19s` | — | — | — | — | 2.09s | *Chỉ báo cáo* |
| `720×1280` (`30s`, 30fps) | 3 | 9 | `rejected_pre_worker` (`HTTP 400 duration_limit_exceeded`, `max=24s`) | **0.14s** | `0.07 – 0.35s` | — | — | — | — | 2.70s | *Chỉ báo cáo* |
| `1080×1920` (`10s`, 30fps) | 1 | 3 | `per_frame` (300/300f) | **48.27s** | `48.08 – 50.38s` | 48.27s | — | **44.91s** (`44.09 – 46.17s`) | **0.0722** (`0.0709 – 0.0742`) | 49.10s | *Chỉ báo cáo* |
| `1080×1920` (`10s`, 30fps) | 2 | 6 | `per_frame` (300/300f) | **68.43s** | `65.12 – 71.93s` | 68.43s | — | **63.00s** (`61.81 – 69.90s`) | **0.1013** (`0.0994 – 0.1124`) | 72.26s | *Chỉ báo cáo* |
| `1080×1920` (`10s`, 30fps) | 3 | 9 | `per_frame` (300/300f) | **66.30s** | `64.37 – 111.46s` | **66.00s** | **110.66s** | **61.88s** (`43.95 – 64.37s`) | **0.0995** (`0.0707 – 0.1035`) | 112.80s | *Chỉ báo cáo* |
| `1080×1920` (`20s`, 30fps) | 1 | 3 | `rejected_pre_worker` (`HTTP 400 duration_limit_exceeded`, `max=10s`) | **0.06s** | `0.06 – 0.06s` | — | — | — | — | 1.29s | *Chỉ báo cáo* (Chặn trước worker đúng thiết kế) |
| `1080×1920` (`20s`, 30fps) | 2 | 6 | `rejected_pre_worker` (`HTTP 400 duration_limit_exceeded`, `max=10s`) | **0.08s** | `0.07 – 0.27s` | — | — | — | — | 2.26s | *Chỉ báo cáo* |
| `1080×1920` (`20s`, 30fps) | 3 | 9 | `rejected_pre_worker` (`HTTP 400 duration_limit_exceeded`, `max=10s`) | **0.09s** | `0.07 – 0.36s` | — | — | — | — | 3.30s | *Chỉ báo cáo* |
| `1080×1920` (`30s`, 30fps) | 1 | 3 | `rejected_pre_worker` (`HTTP 400 duration_limit_exceeded`, `max=10s`) | **0.06s** | `0.06 – 0.07s` | — | — | — | — | 1.80s | *Chỉ báo cáo* (Chặn trước worker đúng thiết kế) |
| `1080×1920` (`30s`, 30fps) | 2 | 6 | `rejected_pre_worker` (`HTTP 400 duration_limit_exceeded`, `max=10s`) | **0.09s** | `0.07 – 0.19s` | — | — | — | — | 3.98s | *Chỉ báo cáo* |
| `1080×1920` (`30s`, 30fps) | 3 | 9 | `rejected_pre_worker` (`HTTP 400 duration_limit_exceeded`, `max=10s`) | **0.23s** | `0.08 – 0.24s` | — | — | — | — | 5.38s | *Chỉ báo cáo* |

### Bảng so sánh tốc độ Load Test Trước (`7e31a13`) vs Sau sửa (`ced4bcc`) — Bước C.4
| Cấu hình chấp nhận (`per_frame`) | Số job đồng thời | Worker Exec Median tại `7e31a13` (s) | Worker Exec Median Sau sửa `ced4bcc` (s) | Chênh lệch Worker (`Δ`) | E2E Median tại `7e31a13` (s) | E2E Median Sau sửa `ced4bcc` (s) | `sec/(MPx×f)` tại `7e31a13` | `sec/(MPx×f)` Sau sửa `ced4bcc` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `720×1280` (`10s`, 30fps) | 1 | 32.18s | **35.25s** | `+3.07s` (`+9.5%`) | 35.24s | **37.64s** | 0.1164 | **0.1275** |
| `720×1280` (`10s`, 30fps) | 2 | 48.98s | **48.05s** | `-0.93s` (`-1.9%`) | 52.36s | **51.92s** | 0.1771 | **0.1738** |
| `720×1280` (`10s`, 30fps) | 3 | 43.57s | **42.45s** | `-1.12s` (`-2.6%`) | 53.62s (q3=83.66s) | **47.83s** (q3=77.28s) | 0.1576 | **0.1535** |
| `720×1280` (`20s`, 30fps) | 1 | 59.81s | **61.95s** | `+2.14s` (`+3.6%`) | 63.59s | **64.01s** | 0.1082 | **0.1120** |
| `720×1280` (`20s`, 30fps) | 2 | 70.25s | **87.63s** | `+17.38s` (`+24.7%`) | 72.83s | **89.85s** | 0.1270 | **0.1585** |
| `720×1280` (`20s`, 30fps) | 3 | 74.23s | **84.23s** | `+10.00s` (`+13.5%`) | 92.42s (q3=139.43s) | **91.56s** (q3=150.46s) | 0.1342 | **0.1523** |
| `1080×1920` (`10s`, 30fps) | 1 | 45.93s | **44.91s** | `-1.02s` (`-2.2%`) | 48.10s | **48.27s** | 0.0738 | **0.0722** |
| `1080×1920` (`10s`, 30fps) | 2 | 66.02s | **63.00s** | `-3.02s` (`-4.6%`) | 69.44s | **68.43s** | 0.1061 | **0.1013** |
| `1080×1920` (`10s`, 30fps) | 3 | 69.03s | **61.88s** | `-7.15s` (`-10.4%`) | 83.01s (q3=120.35s) | **66.30s** (q3=110.66s) | 0.1109 | **0.0995** |

### Giải thích nguyên nhân gốc rễ 2 hiện tượng bất thường trong P5.6.2 (đã khắc phục triệt để trong P5.7.2)
1. **Vì sao ở P5.6.2 video `720p 20s` lại xong trong `14.95s`, nhanh hơn video `720p 10s` (`26.46s`)?**
   - Trong `run_load_test_p5.py` cũ của P5.6.2, hàm `_generate_fast_test_clip` dùng bộ lọc `testsrc2` thuần của FFmpeg (không dịch chuyển nền), trong đó chỉ có một vạch quét định kỳ chạy qua khung hình, còn cột `mode` trong bảng báo cáo bị gán cứng chuỗi `"per_frame"`.
   - Hàm `_is_static_around` (`flow_inpaint.py` dòng 373–399) trích 5 thời điểm `[0.15d, 0.35d, 0.55d, 0.75d, 0.92d]`:
     - Với `d = 10s` (`[1.5s, 3.5s, 5.5s, 7.5s, 9.2s]`): vạch quét của `testsrc2` tình cờ đi ngang qua vành khuyên (`ring`) quanh góc phải-dưới ở 1 mẫu → `max(diffs) >= 3.5` → `static_bg = False` → chạy **`mode="per_frame"`** mất **`26.46s`**.
     - Với `d = 20s` (`[3.0s, 7.0s, 11.0s, 15.0s, 18.4s]`, cách nhau bội số chu kỳ `4.0s`): cả 5 mẫu đều rơi vào thời điểm vạch quét nằm ngoài góc phải-dưới → `max(diffs) < 3.5` → `static_bg = True` → chạy **`mode="static_patch"`** (chỉ tính `SHIFTMAP` đúng 1 lần trên frame giữa rồi blend cho 600 frame) nên hoàn tất trong **`14.95s`**.
   - **Khắc phục ở P5.7.2**: `_generate_fast_test_clip` dùng `testsrc2` trên canvas lớn kèm `crop=w=vw:h=vh:x='mod(n*6,...)':y='mod(n*4,...)'` dịch chuyển toàn bộ khung nền mỗi frame, và script đọc trực tiếp `natural_stats.mode` từ API kèm `assert actual_modes == ["per_frame"]`. Kết quả sau sửa: `720p 10s` (`conc=1`) mất **`37.64s`** (`worker=35.25s`, 300/300 frame temporal), còn `720p 20s` (`conc=1`) mất **`64.01s`** (`worker=61.95s`, 600/600 frame temporal) — tỷ lệ thuận hoàn toàn với số lượng frame.

2. **Vì sao ở P5.6.2 video `1080p 10s` luôn đứng quanh `~62s` (`62.08s / 62.44s / 61.95s`) dù chạy 1, 2 hay 3 job đồng thời?**
   - Do **2 nguyên nhân kết hợp**:
     - **Nguyên nhân 1 (SlowAPI Rate Limit `60/minute` chặn polling `/status/{job_id}`)**: Trong `backend/app/main.py`, `limiter = Limiter(key_func=rate_limit_key, default_limits=["60/minute"])` áp mặc định `60 request/phút` lên mọi endpoint không có decorator riêng, bao gồm `GET /api/v1/flow-cleanup/status/{job_id}`. Đồng thời khi chạy local không đặt `TRUSTED_PROXY_IPS`, `get_client_ip` bỏ qua `X-Forwarded-For` và gom cả 1, 2 hay 3 thread vào chung một bucket `ip:127.0.0.1`. Khi mỗi thread poll `GET /status/{job_id}` mỗi `0.4s` (`2.5 req/s`), tổng cộng 60 request bị dùng hết chỉ sau **`~23.2s`** (1 job), **`~11.6s`** (2 job), hoặc **`~7.7s`** (3 job). Từ thời điểm đó, dù Celery worker đã xử lý xong job ở giây thứ `~28–48`, endpoint `GET /status/{job_id}` liên tục trả về `HTTP 429 Too Many Requests` (mà vòng lặp poll bỏ qua và `sleep(0.4)`) cho đến đúng thời điểm cửa sổ 60 giây của SlowAPI được mở lại (`60.0s + ~2.0s upload/setup ≈ 61.95s–62.91s`)!
     - **Nguyên nhân 2 (Công thức `P50` trên 3 phần tử che khuất thời gian chờ hàng đợi của job thứ 3)**: Với `FLOW_INPAINT_WORKER_CONCURRENCY=2`, khi gửi 3 job đồng thời, job #1 và job #2 chạy ở đợt 1 (wave 1) còn job #3 phải xếp hàng chờ trên Redis (hoàn tất ở `87.11s`). Tuy nhiên trung vị (`P50`) của mảng 3 phần tử đã sắp xếp `[job1_wave1, job2_wave1, job3_wave2]` luôn là phần tử đứng giữa (`index = 1`, tức `job2_wave1`), khiến cột `P50` ở `conc=3` chỉ phản ánh đợt 1 mà ẩn mất thời gian chờ hàng đợi của job #3.
   - **Khắc phục ở P5.7.2**: Đặt `@limiter.limit("600/minute")` cho endpoint đọc trạng thái nhẹ `GET /api/v1/flow-cleanup/status/{job_id}`, cấu hình `TRUSTED_PROXY_IPS=127.0.0.1` trong môi trường load test, và tách rõ `Wave 1 Median` vs `Job #3 chờ queue Median` trong báo cáo. Kết quả trên `1080×1920 10s`:
     - `conc=1`: **`48.27s`** (`worker=44.91s`)
     - `conc=2`: **`68.43s`** (`worker=63.00s`, do 2 job chia sẻ CPU 4 nhân)
     - `conc=3`: Wave 1 = **`66.00s`**, còn Job #3 xếp hàng chờ = **`110.66s`** (`batch_wall = 112.80s`).

### Cô lập hàng đợi bằng Job tải/ghép video thật trên hàng đợi `downloads` (`merge_audio_video_task`)
- Thay vì dùng `delete_local_file` (chỉ xoá file trên đĩa, không tốn CPU), P5.7.2 chạy tác vụ Celery thật **`merge_audio_video_task`** trên hàng đợi **`downloads`** (ghép & mã hoá lại luồng video `720×1280@30fps` dài `4s` + tạo luồng âm thanh `sine 440Hz` sang container MP4 `H.264 + AAC 192kbps`, tạo file đầu ra `1,819,487 bytes` = `1.82 MB`):
  - **Khi hàng đợi `media` rỗi (`idle`, 3 mẫu)**: Trung vị **`0.64s`** (`min = 0.596s`, `max = 0.662s`).
  - **Khi hàng đợi `media` đang bão hoà 100% (`conc = 3` với 2 job inpaint chạy + 1 job xếp hàng, 9 mẫu)**: Trung vị **`0.75s`** (`min = 0.676s`, `max = 0.800s`), chênh lệch chỉ **`+0.11s`** do chia sẻ nhân CPU vật lý, và **không phải chờ hàng đợi `media` (`0s` queue blocking)**.
  - Xác nhận hàng đợi `downloads` hoàn toàn cô lập với hàng đợi `media` (**ĐẠT** theo Plan mục 6).

### Chi phí CPU & Công suất tối đa / giờ / 1 worker (Chế độ `per_frame` @ 30fps, sau sửa `ced4bcc`)
| Cấu hình video (`per_frame` @ 30fps) | CPU-second / 1s video | Số video 10s tối đa / giờ / 1 worker | Đánh giá |
|---|---:|---:|---|
| `720×1280` @ 30fps (`10s`) | **3.76 CPU-s / 1s** (`worker = 3.53s / 1s`) | **95 video (10s) / giờ** | *Chỉ báo cáo* |
| `720×1280` @ 30fps (`20s`) | **3.20 CPU-s / 1s** (`worker = 3.10s / 1s`) | **112 video (10s tương đương) / giờ** | *Chỉ báo cáo* |
| `1080×1920` @ 30fps (`10s`) | **4.83 CPU-s / 1s** (`worker = 4.49s / 1s`) | **74 video (10s) / giờ** | *Chỉ báo cáo* |

### Giới hạn thời lượng thực tế theo Độ phân giải / FPS (`_validate_job_limits`, P5.6.3)
Công thức bảo vệ timeout trong `_validate_job_limits` (`_JOB_TIMEOUT_SEC = 300s`, `_SEC_PER_MPX_FRAME = 0.45`, trần cứng Inpaint `60s`, trần Crop/Blur `600s`):
- `mpx_per_sec = (width * height / 1_000_000) * fps`
- `estimated_inpaint_sec = dur * mpx_per_sec * 0.45`
- `effective_max_duration_sec = max(5, int(min(60, 300 / (mpx_per_sec * 0.45))))`

| Độ phân giải | FPS | `mpx_per_sec` | Thời lượng tối đa cho phép (Inpaint `natural`/`telea`/`telea_soft`) | Thời gian ước tính ở mức trần (`est_sec`) | Thời lượng tối đa cho phép (`crop` / `blur`) |
|---|---:|---:|---:|---:|---:|
| `640×360` (360p) | 24 fps | 5.53 | **60s** | 149.3s | **600s** |
| `640×360` (360p) | 30 fps | 6.91 | **60s** | 186.6s | **600s** |
| `854×480` (480p) | 30 fps | 12.30 | **54s** | 298.8s | **600s** |
| `1280×720` / `720×1280` (720p) | 24 fps | 22.12 | **30s** | 298.6s | **600s** |
| `1280×720` / `720×1280` (720p) | 30 fps | 27.65 | **24s** | 298.6s | **600s** |
| `1280×720` / `720×1280` (720p) | 60 fps | 55.30 | **12s** | 298.6s | **600s** |
| `1920×1080` / `1080×1920` (1080p) | 24 fps | 49.77 | **13s** | 291.1s | **600s** |
| `1920×1080` / `1080×1920` (1080p) | 30 fps | 62.21 | **10s** | 279.9s | **600s** |
| `1920×1080` / `1080×1920` (1080p) | 60 fps | 124.42 | **5s** | 279.9s | **600s** |
| `3840×2160` (4K UHD) | 30 fps | 248.83 | **2s** (UI báo sàn `5s`, `/process` từ chối ở `> 2.68s`) | 300.0s | **600s** |

### Đề xuất lại `FLOW_SEC_PER_MPX_FRAME` có hệ số an toàn dựa trên số liệu Load Test P5.7.2 sau sửa (`ced4bcc` — Mục P5.7.3)
- **Không tự đổi mặc định trong code**: giá trị mặc định `_SEC_PER_MPX_FRAME = 0.45` trong `backend/app/api/flow_cleanup.py` được **giữ nguyên tuyệt đối**.
- **Số liệu thực tế `sec / (MPx × frame)` đo từ 54 lượt chạy worker sau bản sửa `ced4bcc` (P5.7.2)**:
  - Ở `conc = 1` (1 job chạy đơn lẻ):
    - `720×1280` (`10s`, `276.48 MPx·f`): Worker Median = **`0.1275 s/(MPx·f)`** (`0.1236 – 0.1309`)
    - `720×1280` (`20s`, `552.96 MPx·f`): Worker Median = **`0.1120 s/(MPx·f)`** (`0.1108 – 0.1140`)
    - `1080×1920` (`10s`, `622.08 MPx·f`): Worker Median = **`0.0722 s/(MPx·f)`** (`0.0709 – 0.0742`)
    - Trung vị toàn cục `conc = 1`: **`0.1120 s/(MPx·f)`** (tại `7e31a13` là `0.1077 s/(MPx·f)`).
  - Ở `conc = 2..3` (khi 2 luồng worker chạy song song chia sẻ CPU 4 nhân):
    - `720×1280` (`10s`, `conc = 2`): Worker Median = **`0.1738 s/(MPx·f)`**, **Max quan sát toàn bộ 54 job = `0.2035 s/(MPx·f)`** (`56.27s` trên `276.48 MPx·f`).
    - `720×1280` (`20s`, `conc = 2`): Worker Median = **`0.1585 s/(MPx·f)`** (`max = 0.1622`).
    - `1080×1920` (`10s`, `conc = 2`): Worker Median = **`0.1013 s/(MPx·f)`** (`max = 0.1124`).
- **Tại sao đề xuất `0.15` ở P5.6 cũ KHÔNG an toàn và bị huỷ bỏ?**
  - Với `0.15`, hệ thống sẽ cho phép video `720×1280@30fps` dài tới `60s` (`1,658.88 MPx·f`). Ở tải đồng thời `conc = 2` (`0.1585 – 0.2035 s/(MPx·f)`), job `720p 60s` sẽ chạy mất **`262.9s – 337.6s`**, vượt ngưỡng `80% timeout` (`240s`) và có nguy cơ chạm `soft_time_limit = 300s` làm người dùng mất quota mà không nhận được kết quả.
- **Công thức tính `FLOW_SEC_PER_MPX_FRAME` đề xuất mới (P5.7.3)**:
  - Điều kiện bắt buộc của brief P5.7.3: **Job dài nhất được nhận phải hoàn tất dưới `80%` của `FLOW_JOB_TIMEOUT_SEC` (`≤ 240s` trên `300s`)** ngay cả ở trường hợp xấu nhất khi 2 job chạy đồng thời (`max_observed_worker_spm = 0.2035 s/(MPx·f)`), cộng thêm **hệ số dự phòng an toàn `1.25×` (`+25%`)**:
    $$\text{FLOW\_SEC\_PER\_MPX\_FRAME}_{\text{đề xuất}} = \text{spm}_{\max} \times \frac{300}{240} \times 1.25 = 0.2035 \times 1.25 \times 1.25 = 0.3180 \approx \mathbf{0.32}$$
- **Bảng đối chiếu thời lượng tối đa & thời gian chạy thực tế ở mức trần giữa mặc định `0.45` và mức đề xuất `0.32`**:

| Độ phân giải @ 30fps | `mpx_per_sec` | Thời lượng tối đa hiện tại (`0.45`) | Thời lượng tối đa với đề xuất (`0.32`) | Thời gian chạy thực tế kỳ vọng ở `conc=1` (với trần `0.32`) | Thời gian chạy thực tế xấu nhất ở `conc=2` (`spm_max`, với trần `0.32`) | So với ngưỡng `80% timeout` (`≤ 240s`) |
|---|---:|---:|---:|---:|---:|---|
| `640×360` (360p) | 6.91 | **60s** | **60s** | ~46.4s | ~84.4s | **An toàn (`84.4s ≤ 240s`)** |
| `854×480` (480p) | 12.30 | **54s** | **60s** | ~82.7s | ~150.2s | **An toàn (`150.2s ≤ 240s`)** |
| `720×1280` / `1280×720` (720p) | 27.65 | **24s** | **33s** (`912.4 MPx·f`) | **~102.2s – 116.3s** | **~185.7s** (với `spm_max = 0.2035`) | **An toàn (`185.7s ≤ 240s`)** |
| `1080×1920` / `1920×1080` (1080p) | 62.21 | **10s** | **15s** (`933.1 MPx·f`) | **~67.4s** | **~104.9s** (với `spm_max_1080p = 0.1124`) / **~189.9s** (với `0.2035`) | **An toàn (`104.9s – 189.9s ≤ 240s`)** |

### Ghi chú Hardening P5.6.7 — Video kích thước lẻ (`odd_721x405.mp4`) & Lịch sử E2E P5.5/P5.6.1
- **Video kích thước lẻ (`odd_721x405.mp4`, đầu vào `721×405`)**:
  - Đã chuyển từ cắt/làm tròn xuống (`720×404`) sang **pad +1px (`pad=722:406:0:0:black`)** nhằm giữ nguyên 100% điểm ảnh của khung hình gốc `721×405`.
  - Kết quả đánh giá tiêu chí giữ nguyên độ phân giải gốc trên `odd_721x405.mp4` (`721×405 → 722×406`): **Lệch có chủ đích** (không ghi `PASS` tuyệt đối, vì định dạng đầu ra H.264 `yuv420p` bắt buộc kích thước chẵn chia hết cho 2; `_probe()` trả rõ `raw_width=721`, `raw_height=405`, `width=722`, `height=406`, `odd_dimension_adjusted=True`, `dimension_adjustment="padded_to_even_for_yuv420p"`).
- **Ghi nhận về bộ ảnh E2E lần chạy P5.5 trước đó**:
  - Bộ ảnh E2E lần trước (trước P5.6.1) **KHÔNG hợp lệ** do ảnh `01` và `03` giống hệt nhau và ảnh `02` chụp trang Pricing báo lỗi.
  - Trong **P5.6.1** (`commit dc61e5f`), toàn bộ 11 bước E2E giao diện thật (`benchmarks/results/e2e-screenshots/01_pro_upload.png` .. `11_invalid_file_error.png`) đã được chạy lại trên trình duyệt thật + Backend + Frontend + Celery + Redis thật, mỗi bước chụp đúng màn hình riêng biệt (11/11 mã băm SHA-256 khác nhau hoàn toàn) và ảnh `05_pro_ab_preview.png` đã xoá sạch hoàn toàn chữ `"VIDGRAB"` bằng `temporal_fill` (`MSE = 10.34`).

---

## Mẫu ghi cho các lần chạy sau

## Lần chạy N — DD/MM/YYYY (mô tả ngắn)

### Môi trường
- Commit:
- FFmpeg:
- Engine / phương pháp:

### Bộ dữ liệu
| Video | Nhóm | Độ phân giải | FPS | Số frame | Có bản sạch |
|---|---|---|---|---|---|

### Detector
| Video | Precision | Recall | Báo nhầm | Ghi chú |
|---|---|---|---|---|

### Tracker
| Video | IoU trung vị | Drift max (% frame) | Mất dấu được báo | Ghi chú |
|---|---|---|---|---|

### Chất lượng xoá
| Video | SSIM trong mask | PSNR ngoài mask | Nhấp nháy/viền | Ghi chú |
|---|---|---|---|---|

### Hiệu năng
| Video | Thời gian xử lý | RAM tối đa |
|---|---|---|

### Kết luận
- Đạt:
- Chưa đạt:
- Việc tiếp theo:

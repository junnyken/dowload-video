# QA / UAT Package — Flow/Veo Visible-Logo Cleanup

**Feature:** Visible on-frame logo removal for Flow/Veo video exports
**Stack:** React 19 + FastAPI + FFmpeg (synchronous, no Celery for this feature) + Redis + temp files
**Scope:** On-frame visible logo only. SynthID (invisible Google AI watermark) is NOT in scope and must never appear in any pass/fail criterion, copy, or label.
**Admin/QA tab:** `/vid-admin` → "Flow QA" tab (tab id: `flowqa`)
**Backend endpoints:** `POST /api/v1/flow-cleanup/upload`, `GET /api/v1/flow-cleanup/frame/{temp_id}`, `POST /api/v1/flow-cleanup/process`
**Job TTL:** ~20 minutes (temp dirs under `downloads/flow_<hex>/`)
**Audience:** QA engineers, developers, product manager

---

## Section 1 — QA/UAT Checklist

### A. Entry / Eligibility

- [ ] Uploading a `.mp4` file transitions: `idle → uploading → preview`
- [ ] Uploading a `.mov` file succeeds and reaches preview state
- [ ] Uploading a `.webm` file succeeds and reaches preview state
- [ ] Uploading a `.mkv` file succeeds and reaches preview state
- [ ] Uploading a `.pdf` file is rejected client-side with error state and message "Chỉ hỗ trợ MP4, MOV, WebM, MKV."
- [ ] Uploading a `.png` file is rejected client-side before any network request is made
- [ ] Uploading a `.gif` file is rejected client-side before any network request is made
- [ ] Uploading a `.mp3` file is rejected client-side before any network request is made
- [ ] Uploading a file larger than 500 MB shows an error "File vượt quá 500MB." and transitions to `error` state — no network upload is attempted (client-side guard fires first)
- [ ] A borderline 499 MB file uploads without being rejected for size
- [ ] Upload progress is visually indicated (spinner + "Đang tải lên video..." copy) while in `uploading` state
- [ ] A corrupted or truncated MP4 (unreadable by ffprobe) returns a 422 error with message "Không đọc được video. Kiểm tra lại file." and transitions to `error` state
- [ ] After a failed upload, the error state shows a retry/new-upload button (not stuck in `uploading`)
- [ ] On successful upload: preview frame (first-frame JPEG) is displayed
- [ ] On successful upload: video resolution (`width × height`), duration (seconds), and fps are displayed in the preview overlay
- [ ] `temp_id` is set after upload and is passed correctly in the subsequent process request
- [ ] The upload rate limit (10/minute per IP) does not affect normal usage; confirm 429 response after exceeding limit

### B. Suitability Assessment

> Reminder: suitability = technical quality of the video for FFmpeg processing, NOT logo detection. Suitability is computed from `{duration, fps, width, height}` only.

- [ ] Short HD clip (15s, 30fps, 1920×1080): suitability banner shows "Trường hợp này phù hợp để làm sạch tự động." (emerald/green styling, level = `good`)
- [ ] Mid-score clip (25s, 60fps, 1920×1080, score = 75): banner shows "Hệ thống cần bạn xác nhận vùng logo để xử lý chính xác hơn." (amber styling, level = `manual`) — no "auto-clean guaranteed" copy present
- [ ] Long clip (180s, 30fps, 1920×1080, cropSuggested=true): banner shows "Với video này, cắt khung hình có thể cho kết quả tốt hơn lấp pixel." (sky/blue styling, level = `crop`) AND method is automatically switched to `crop` on upload completion
- [ ] Low-quality clip (score < 60, no cropSuggested): banner shows "Vùng này chưa phù hợp để làm sạch tự động — kết quả có thể thấy vệt." (orange styling, level = `low`) — honest quality risk, no overconfident language
- [ ] Warning flags for `long_clip`, `medium_length_clip`, `high_fps`, `low_res`, `crop_preferred` appear in admin metadata (backend) for qualifying videos — verify via Flow QA tab detail panel
- [ ] No suitability message says anything like "tự động làm sạch hoàn toàn", "đảm bảo kết quả", or any overconfident claim
- [ ] The suitability banner is absent (null) when `videoInfo` is not available

### C. Mode Selection

- [ ] `delogo` is the default method on a fresh page load (before any upload)
- [ ] `reset()` — triggered by "Xử lý video khác" or "Đổi video" — resets method back to `delogo`
- [ ] Note: `reset()` does NOT reset the preset selection; preset stays at last selection (or `lower-right` if never changed). Verify this is intentional UX (no stale region visible since preview is hidden after reset)
- [ ] Recommendation badge "Khuyến nghị" appears on `delogo` when suitability is `good` or `manual`
- [ ] Recommendation badge "Khuyến nghị" appears on `crop` when suitability is `crop` or `low`
- [ ] When suitability = `crop`, `crop` method is pre-selected AND shows "✓ Khuyến nghị" (selected+recommended state)
- [ ] User can freely switch method delogo → crop → blur → delogo without error
- [ ] Switching to `crop` method activates crop preview mode: dark overlays cover the removed edge strips; orange boundary outlines the surviving frame
- [ ] Switching away from `crop` returns to the orange logo-region box on the corner preset
- [ ] "Không thấy logo hiển thị trên video này" button is full-width, elevated as a distinct option, clearly visible without scrolling in typical viewport
- [ ] The secondary nudge button "Vẫn muốn thử lấp pixel thay thế" appears only when suitability=`crop` AND method=`crop` (not in other states)
- [ ] The secondary nudge button "Dùng cách cắt khung hình thay thế" appears only when suitability=`low`

### D. Manual Region Flow

- [ ] All four presets selectable: "Dưới phải" (↘), "Dưới trái" (↙), "Trên phải" (↗), "Trên trái" (↖)
- [ ] Active preset is highlighted with orange styling (`bg-[#FB923C]/20 border-[#FB923C]/50 text-[#FB923C]`)
- [ ] Selecting a preset updates the orange bounding box position on the preview frame immediately
- [ ] Orange box position matches the backend `preset_map` math: lower-right box is at top-right area of lower-right corner, not just any corner area
- [ ] Preset coordinates sent to backend match the frontend PRESETS constants (verify via admin detail panel: `region.x/y/w/h` match preset math for video dimensions)
- [ ] Custom region preset (`custom`) is available in the ProcessRequest model; frontend currently uses only the four named presets — if custom UI is not yet exposed, document this as known gap
- [ ] Region validation: backend rejects w=0 or h=0 with 400 "Kích thước vùng không hợp lệ."
- [ ] Region validation: backend rejects region extending outside frame bounds with 400 "Vùng nằm ngoài khung hình."
- [ ] `region_source` in admin metadata is `user_selected` when a named preset is used

### E. Crop Fallback Flow

- [ ] When `crop` method is selected, dark overlay strips appear on the appropriate edge(s) of the preview frame
- [ ] Orange boundary line outlines the surviving (kept) frame area, not the removed strip
- [ ] "Phần giữ lại sau khi cắt" label appears in the bottom-left of the crop preview
- [ ] Crop boundary computation: for lower-right preset (logo at right/bottom), crop removes right edge — verify surviving frame stops at approximately `x-4` pixels from left of logo region
- [ ] Crop computation mirrors `computeCropBounds()` in frontend and the crop math in `flow_cleanup.py`: both use same 0.6 threshold logic
- [ ] Output file is named `{slug}_cropped.mp4` (not `{slug}_logo_cleaned.mp4`)
- [ ] Result card title reads "Video đã được cắt viền để loại bỏ logo" (not the generic delogo title)
- [ ] `result_type` in backend metadata and API response = `cropped_export`
- [ ] `selected_mode` in metadata = `crop_fallback`

### F. Processing States

- [ ] Process button triggers `processing` state: spinner shows with method-specific message ("Đang phân tích và lấp vùng logo..." / "Đang tính toán và cắt viền..." / "Đang che mờ vùng logo...")
- [ ] Process button is not double-submittable — there is no explicit `disabled` attribute on the process button, but React state change to `processing` removes the preview section including the button. Verify no double-submit can occur by rapid clicking
- [ ] Processing state shows estimated time range "15–90 giây tùy độ dài clip"
- [ ] FFmpeg failure (e.g. unsupported codec, corrupted input) transitions to `error` state — not stuck in `processing`
- [ ] Error state message shows the backend error detail (from `body.detail`)
- [ ] Error state for a processing failure (when `previewUrl` is set) shows "Thử vùng hoặc phương thức khác" with `RotateCcw` icon, returning user to `preview` state
- [ ] Error state for an upload failure (no `previewUrl`) shows "Thử lại" button, calling `reset()`
- [ ] No success toast, spinner, or UI element appears after a processing failure
- [ ] For a 5-minute+ video processed with `delogo`, FFmpeg subprocess has a 300-second timeout — verify error state is reached on timeout, not an uncaught 500

### G. Result Card / Preview

- [ ] After successful processing, step transitions to `done`
- [ ] Done state shows the original preview frame at reduced opacity (75%) with region/crop overlay
- [ ] For delogo/blur: emerald-colored box overlays the treated corner with label "Vùng đã xử lý"
- [ ] For crop: crop overlay (dark strips + emerald boundary + "Khung hình sau khi cắt" label) shown in done state
- [ ] "Khung hình gốc · để tham chiếu" label in top-right of done preview
- [ ] Method summary row shows: "Phương thức: {methodLabel} · Vùng xử lý: {presetLabel}" for delogo/blur, or "Phương thức: {methodLabel} · Viền cắt: {presetLabel}" for crop
- [ ] Result card title is method-aware: "Video đã được làm sạch logo hiển thị" (delogo/blur) or "Video đã được cắt viền để loại bỏ logo" (crop)
- [ ] File size in MB is displayed below the title ("X.XX MB · Bản xuất hết hạn sau ~20 phút")
- [ ] Download anchor (`<a>`) is present pointing to `/api/v1/download-local?filepath=...&filename=...` with `download` attribute set to the correct filename
- [ ] Download succeeds: file is retrieved and saved locally with correct filename
- [ ] Download filename for delogo/blur ends with `_logo_cleaned.mp4`
- [ ] Download filename for crop ends with `_cropped.mp4`
- [ ] Download filename slug is ASCII-normalized (no unicode characters, no spaces)
- [ ] "Chỉnh lại vùng xử lý" button (secondary) returns to `preview` state with same video/region
- [ ] "Xử lý video khác" button triggers `reset()`, returning to `idle` state with clean form
- [ ] `SynthIDNote` component is rendered below the result for scope reminder
- [ ] For `suitability=low`, additional orange quality note appears: "Xử lý từ trường hợp độ tin cậy thấp..."

### H. History / Job Labels

- [ ] Admin metadata `result_type = cleaned_visible_logo` for delogo and blur methods
- [ ] Admin metadata `result_type = cropped_export` for crop method
- [ ] Admin metadata `job_kind = visible_logo_cleanup` for every completed job (not `synthid_removal` or any other value)
- [ ] Admin metadata `selected_mode = visible_logo_cleanup` for delogo/blur, `crop_fallback` for crop
- [ ] No-logo path (user clicked "Không thấy logo hiển thị trên video này"): no `process` API call is made, no `result_type` is set, no `cleaned` label shown. Verify via browser network tab.
- [ ] Frontend result card label and backend `result_type` agree — no mismatch
- [ ] No label anywhere reads "watermark đã xóa hoàn toàn", "SynthID removed", or equivalent

### I. Retry / Reprocess Behavior

- [ ] "Chỉnh lại vùng xử lý" from done state re-enters `preview` state with same `tempId`, `videoInfo`, `previewUrl`, `suitability` intact
- [ ] Switching method (delogo→crop) in preview and reprocessing produces `result_type=cropped_export`
- [ ] Full `reset()` clears: `step→idle`, `tempId→null`, `videoInfo→null`, `previewUrl→null`, `result→null`, `error→null`, `suitability→null`, `method→delogo`
- [ ] Full `reset()` does NOT clear the file input value (it does — `fileInputRef.current.value = ''` is called)
- [ ] New upload after reset starts completely fresh — no stale `result` or `suitability` data from previous session visible
- [ ] Re-uploading the same file produces the same `suitability` level and same mode recommendation (deterministic scoring from metadata)
- [ ] After `reset()`, preset defaults back to `lower-right` visually (because preset state is not cleared by `reset()` — default is `'lower-right'` which is what most users start with; this is the initial value, not a state cleared on reset)

### J. Admin / QA / Debug Visibility

- [ ] Navigating to `/vid-admin` loads without error
- [ ] Admin login flow works (X-Admin-Token header sent via sessionStorage)
- [ ] "Flow QA" tab is visible in the tab bar (8th tab with Wand2 icon)
- [ ] Clicking "Flow QA" tab triggers `fetchFlowQA()` and calls `GET /api/v1/admin/flow-cleanup/jobs` with `X-Admin-Token` header
- [ ] `GET /api/v1/admin/flow-cleanup/jobs` returns 401 without valid `X-Admin-Token` header — no public exposure
- [ ] Flow QA tab shows loading spinner while data is fetching
- [ ] Summary cards show correct counts: Tổng, Logo cleaned, Cropped, Chờ xử lý, Failed
- [ ] Each job row displays: 8-char temp_id prefix (monospace), status badge, result_type badge (✓ cleaned / ✂ cropped), method·preset text, suitability level, warning flag chips, timestamp
- [ ] Job row click expands inline detail panel; clicking again collapses
- [ ] Detail panel left column: preview frame JPEG loaded from `/api/v1/flow-cleanup/frame/{temp_id}`, with orange region overlay box positioned using `region.x/y/w/h` divided by `video_info.width/height`
- [ ] Detail panel right column: Job Metadata section shows `job_kind`, `result_type`, `selected_mode`, `region_source`, `suitability`, `method`, `preset`, `output_size`
- [ ] Detail panel: Video section shows `resolution`, `duration / fps`, `upload size`
- [ ] Detail panel: Region (px) section shows `x:N y:N w:N h:N`
- [ ] Detail panel: Warning Flags section shows colored flag chips for any flags present
- [ ] Detail panel: Error section (red box) appears for failed jobs with the error string from metadata
- [ ] Detail panel: Timestamps section shows `created`, `processed`, `failed` in vi-VN locale where applicable
- [ ] Scope guard text visible below summary cards: "Workflow này chỉ xử lý logo hiển thị trên khung hình. SynthID (invisible watermark) không được xử lý..."
- [ ] Refresh button (RefreshCw icon) in Flow QA tab header re-fetches job list from server
- [ ] "Không có job nào trong window hiện tại" empty state shows with Eye icon when no jobs exist

### K. Mobile UX

- [ ] At 375px viewport width (iPhone SE): upload drop zone is visible and tappable without horizontal scroll
- [ ] At 375px: suitability banner text is readable and not clipped
- [ ] At 375px: all three method cards (Lấp pixel, Cắt viền, Che mờ) are visible without horizontal scroll
- [ ] At 375px: method label + "Khuyến nghị" badge do not overflow the card bounds
- [ ] At 375px: all four preset buttons (2-col grid on mobile) have adequate tap target size (≥44px height is met by `py-3` class)
- [ ] At 375px: orange region box overlay is visible on the preview frame (relative positioning works)
- [ ] At 375px: crop mode dark strips are visible on the preview frame
- [ ] At 375px: "Không thấy logo hiển thị trên video này" button is full-width and tappable
- [ ] At 375px: process/CTA button is accessible without scrolling after preset selection
- [ ] At 375px: processing state spinner and message are not blocked by browser chrome
- [ ] At 375px: result card download button is tappable; file size text visible
- [ ] At 375px: "Xử lý video khác" button reachable after done state
- [ ] At 390px viewport (iPhone 14): test same items; check that larger screen does not introduce layout regression
- [ ] Admin Flow QA tab at 375px: summary cards wrap to 3+2 grid; readable without overflow; job row text does not overflow horizontally

### L. Copy / Wording Safety

- [ ] No UI element (toast, button, title, label, description, banner, placeholder, info note) contains "xóa toàn bộ watermark"
- [ ] No UI element contains "gỡ SynthID" or "xóa SynthID"
- [ ] No UI element contains "video sạch watermark 100%" or "video hoàn toàn sạch"
- [ ] No UI element contains "remove all AI watermarks" or "AI watermark removed" or "AI watermark cleaned"
- [ ] No UI element contains "invisible watermark" or "watermark vô hình đã xóa"
- [ ] No UI element contains "watermark detection success" or implies auto-detection of logo
- [ ] Processing state message says "Đang làm sạch logo hiển thị" — not "watermark"
- [ ] Done state title for delogo/blur says "Video đã được làm sạch logo hiển thị" — not "watermark removed"
- [ ] Done state title for crop says "Video đã được cắt viền để loại bỏ logo" — not "cleaned"
- [ ] `SynthIDNote` component text is present in: idle/upload/preview header AND no-logo state AND done state — check all three
- [ ] SynthID note explicitly says "không thể xóa bằng bất kỳ công cụ nào" — confirm this accurate disclaimer is present
- [ ] No-logo state title: "Video này không cần làm sạch logo" — not "không có watermark"
- [ ] Admin scope guard says "SynthID (invisible watermark) không được xử lý và không xuất hiện trong bất kỳ trạng thái nào ở đây"
- [ ] Admin `job_kind` field value is always `visible_logo_cleanup` — never references SynthID
- [ ] Download filename never contains "synthid" or "watermark_removed"

---

## Section 2 — Test Case Matrix

| Test ID | Scenario | Input characteristics | Visible logo present? | Logo position | Background complexity | Motion level | Subject overlap | Expected suitability | Expected mode recommendation | Expected UI state (done) | Expected result_type | Expected history label | Pass criteria | Edge notes |
|---------|----------|-----------------------|----------------------|---------------|-----------------------|--------------|-----------------|---------------------|------------------------------|--------------------------|---------------------|----------------------|---------------|------------|
| TC-001 | Short HD, simple bg, logo lower-right | 15s · 30fps · 1920×1080 | YES | Lower-right | Simple gradient | Low | None | `good` | `delogo` (Khuyến nghị badge) | Frame + emerald box overlay, title "làm sạch logo hiển thị" | `cleaned_visible_logo` | ✓ cleaned | Suitability=good, delogo recommended, download succeeds, no SynthID copy | Score: 100 → good |
| TC-002 | Short HD, busy bg, logo lower-right | 20s · 30fps · 1920×1080 | YES | Lower-right | Busy texture / busy bg behind logo | Medium | None | `good` | `delogo` (Khuyến nghị badge) | Frame + emerald box overlay, result file present | `cleaned_visible_logo` | ✓ cleaned | Correct flow and label; QA notes output quality separately (may have visible delogo artifact on busy bg) | Suitability is metadata-driven — busy bg does not change score |
| TC-003 | Long clip (180s), normal fps, HD | 180s · 30fps · 1920×1080 | YES | Lower-right | Simple | Low | None | `crop` | `crop` (auto-selected on upload; Khuyến nghị badge) | Crop overlay in done state, "cắt viền để loại bỏ logo" title | `cropped_export` | ✂ cropped | Suitability=crop, method auto-switched to crop, `medium_length_clip` flag in admin; if user switches to delogo: `crop_preferred` flag added | Score: 100−15=85 → ≥80 = good BUT cropSuggested=true → level=crop |
| TC-004 | Very long clip (360s) | 360s · 30fps · 1920×1080 | YES | Lower-right | Simple | Low | None | `crop` | `crop` | Crop done state | `cropped_export` | ✂ cropped | `long_clip` flag in admin, quality risk banner shown (crop level), crop prominently recommended | Score: 100−35=65 → cropSuggested=true → level=crop; note: score 65 ≥ 60 but cropSuggested takes priority |
| TC-005 | High fps clip (60fps), short | 25s · 60fps · 1920×1080 | YES | Lower-right | Simple | High | None | `manual` | `delogo` (Khuyến nghị, but honest quality note) | Frame + emerald box overlay | `cleaned_visible_logo` | ✓ cleaned | `high_fps` flag in admin, suitability=manual, banner says "Hệ thống cần bạn xác nhận...", no overconfident auto-clean claim | Score: 100−25=75 → manual; cropSuggested=false (high_fps blocks it) |
| TC-006 | Low resolution (480×270) | 30s · 30fps · 480×270 | YES | Lower-right | Simple | Low | None | `good` (score=80) | `delogo` (Khuyến nghị) | Frame + emerald box overlay | `cleaned_visible_logo` | ✓ cleaned | `low_res` flag present in admin, pipeline proceeds, suitability=good (score exactly 80), QA notes output quality separately | Score: 100−20=80 → good; flag present but level is good by score |
| TC-007 | No visible logo (clean export) | 20s · 30fps · 1920×1080 | NO | N/A | N/A | Low | N/A | N/A (no suitability computed before no-logo is clicked) | N/A | `no-logo` state reached | None (no process call) | (no label — no processing occurred) | No-logo state shows correctly; no process API call (verify via network tab); "Tiếp tục với video gốc" resets cleanly; not treated as failure | suitability is computed but user exits without processing |
| TC-008 | Logo overlaps subject, custom region, center | 20s · 30fps · 1920×1080 | YES | Center (large region) | Complex | Medium | Yes — logo over subject | `good` | `delogo` | Frame + emerald box overlay | `cleaned_visible_logo` | ✓ cleaned | Custom region accepted (region_source=user_custom in admin), process completes, correct label; QA notes output quality | Custom UI not exposed in current frontend — test via API directly; frontend only offers four named presets |
| TC-009 | Crop mode explicitly selected, lower-right, HD | 20s · 30fps · 1920×1080 | YES | Lower-right | Simple | Low | None | `good` (but user overrides to crop) | `crop` (user-selected) | Crop done state, "cắt viền" title | `cropped_export` | ✂ cropped | result_type=cropped_export, filename `_cropped.mp4`, crop label correct | crop_preferred flag will NOT appear because fps≤30 and dur>120 is false (20s) |
| TC-010 | Corrupted / unreadable file | Renamed .txt as .mp4 or truncated MP4 | N/A | N/A | N/A | N/A | N/A | N/A | N/A | `error` state | None | (error, no label) | Error state reached with "Không đọc được video. Kiểm tra lại file." message; retry button available; no stuck spinner | 422 from backend ffprobe failure |
| TC-011 | File over 500 MB | 600 MB MP4 | N/A | N/A | N/A | N/A | N/A | N/A | N/A | `error` state | None | (error, no label) | Client-side guard fires: "File vượt quá 500MB." error shown before any upload; no network request made | Frontend checks `file.size > 500 * 1024 * 1024` before sending |
| TC-012 | FFmpeg failure (unsupported codec / backend 500) | Valid-looking MP4 that triggers FFmpeg error | YES | Lower-right | N/A | N/A | N/A | `good` | `delogo` | `error` state | None (status=failed in admin) | (error) | Error state reached (not stuck processing); admin metadata shows `status=failed`, `failed_at` timestamp, `error` message; retry works from error state | Simulate by uploading a VP9/HEVC file that FFmpeg delogo rejects |
| TC-013 | Re-run same file twice | Same 15s · 30fps · 1920×1080 MP4 | YES | Lower-right | Simple | Low | None | `good` (both runs) | `delogo` (both runs) | Done state both runs | `cleaned_visible_logo` (both) | ✓ cleaned (both) | Second run matches first: same suitability level, same recommendation, same result type; no state leakage between runs after reset | Confirms deterministic suitability model |
| TC-014 | Switch method mid-session (delogo→crop) | 20s · 30fps · 1920×1080 | YES | Lower-right | Simple | Low | None | `good` | `delogo` initially | Crop done state (after switch) | `cropped_export` | ✂ cropped | Crop preview updates on method switch (dark strips appear); result_type=cropped_export; no stale delogo result in admin | Tests method state change after initial upload |
| TC-015 | Mobile upload + preset selection | 375px viewport, touch device | YES | Lower-right | Simple | Low | None | `good` | `delogo` | Done state | `cleaned_visible_logo` | ✓ cleaned | All UI elements reachable; preset buttons tappable (2-col grid); orange box visible; process button accessible; download button tappable | 2-col preset grid on mobile vs 4-col on sm+ |
| TC-016 | Partial logo presence (logo in first 10s of 60s video) | 60s · 30fps · 1920×1080 | YES (partial) | Lower-right | Simple | Low | None | `manual` (score 100−15=85, but duration=60s > 30s... wait: 60s is not > 120s so no duration penalty → score=100 → good) | `delogo` | Done state | `cleaned_visible_logo` | ✓ cleaned | Pipeline completes; delogo applied to entire video (all frames); no crash; QA manually checks that region where logo was absent is not noticeably degraded | System processes all frames uniformly — no per-frame detection |
| TC-017 | Ultra-like source unexpectedly showing logo (regression) | Any duration · standard fps · HD | YES (unexpected) | Any corner | Any | Any | N/A | Per video metadata | Per suitability | Done state | Per method | Per method | Feature handles identically to TC-001 — no special "this shouldn't have a logo" blocking logic | Ultra users normally logo-free; regressions happen |
| TC-018 | Admin tab after job TTL expired (~20min) | Any previously processed job | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | Flow QA tab loads without crash; expired job is absent from list (temp dir deleted externally); no 500 error on glob scan of missing dirs | glob pattern `downloads/flow_*/metadata.json` — deleted dirs simply return no matches |

---

## Section 3 — Mode-Selection Validation Checks

For each processed job visible in the Flow QA admin tab, QA should verify the following explicitly.

| Check | How to verify |
|-------|--------------|
| Was `delogo` the right choice for this video? | Inspect output: delogo works well on solid/gradient backgrounds (e.g. corner of a talking-head clip). On busy/textured or motion-heavy backgrounds, delogo may leave a blotchy patch. Note quality outcome separately as Pass/Needs-Tuning. |
| Should `crop` have been recommended instead of `delogo`? | Check `crop_preferred` flag in admin detail panel. If `crop_preferred` is present AND output quality is poor, flag as Needs-Tuning. |
| Was the region preset sufficient or was manual (custom) region required? | Check `region_source` in admin metadata: `user_selected` = named preset used, `user_custom` = custom region. For full-coverage test, also verify region coordinates match the preset math for the video dimensions. |
| Was the no-logo path handled correctly? | Confirm in browser network tab that no `/process` API call was made when user clicked "Không thấy logo hiển thị trên video này". No `result_type` or `job_kind` should be set for no-logo jobs. |
| Did any UI element overpromise? | Check all copy surfaces listed in Section 6. Any prohibited phrase = Misleading UI/label outcome. |
| Does the computed suitability level match the video metadata? | Manually apply the scoring formula to the video's `{duration, fps, width, height}` and compare to `suitability_level` shown in admin. A mismatch = Wrong-mode-choice outcome. |
| Does the crop preview in the frontend accurately reflect the actual crop dimensions in the output? | Use `computeCropBounds()` logic with the preset percentages, convert to pixels for the video dimensions, and compare to the `crop` FFmpeg command in the output. Differences beyond a few pixels = pipeline bug. |
| Is `job_kind` always `visible_logo_cleanup`? | Inspect every job in admin. `job_kind` must never be absent, `null`, `synthid_removal`, or any variant. |

---

## Section 4 — Result-Label Validation Checks

For every completed job, verify the following. QA should cross-check frontend display against admin `metadata.json` values.

| Check | Pass | Fail |
|-------|------|------|
| Frontend result card title | "Video đã được làm sạch logo hiển thị" (delogo/blur) or "Video đã được cắt viền để loại bỏ logo" (crop) | "watermark removed", "SynthID cleaned", or identical copy for both crop and delogo |
| Admin `result_type` value | `cleaned_visible_logo` (delogo/blur) or `cropped_export` (crop) | Missing field, `null`, wrong type, or any reference to SynthID |
| Admin `job_kind` value | `visible_logo_cleanup` always | Absent, `null`, `synthid_removal`, or "full_watermark" |
| Admin `selected_mode` value | `visible_logo_cleanup` (delogo/blur) or `crop_fallback` (crop) | Absent or inconsistent with `method` field |
| Frontend label vs backend `result_type` agreement | Admin `result_type` matches what was shown in the UI result card | `result_type=cropped_export` but UI says "logo đã xóa"; or vice versa |
| No-logo result not labeled failure | No processing triggered; no `status`, `result_type`, or `job_kind` set in metadata for the no-logo job | No-logo job shows `status=failed` or `result_type=cleaned_visible_logo` with 0 processing |
| Crop result not labeled as "logo cleaned" | Admin badge shows "✂ cropped"; frontend title says "cắt viền"; filename ends in `_cropped.mp4` | Admin shows "✓ cleaned" for a crop job; frontend title identical to delogo done title |
| `warning_flags` array present for qualifying videos | Array populated correctly for `long_clip`, `medium_length_clip`, `high_fps`, `low_res`, `crop_preferred` | Array missing entirely for a video that should have flags |
| Download filename safe for filesystems | ASCII slug only, no unicode, no spaces, no special characters beyond `_` and `-` | Unicode filename, spaces, or `%20` in downloaded filename |
| `SynthIDNote` present in done state | Visible scope note below result card | Absent from done state |

---

## Section 5 — Mobile-Specific QA Items

Test on both 375px (iPhone SE) and 390px (iPhone 14) viewports in Chrome DevTools. Rotate to landscape and back to verify no layout break.

1. **Upload zone**: Drag-drop container is reachable by tap; file picker opens on tap. No horizontal scroll required to see the upload zone.
2. **Suitability banner**: Full banner text readable; icon + text do not overlap; border visible. At `good` level (emerald) and `low` level (orange) specifically tested.
3. **Method selector**: All three method cards (Lấp pixel, Cắt viền, Che mờ) visible in the vertical stack without horizontal scroll. No card is clipped.
4. **Recommendation badge**: "Khuyến nghị" or "✓ Khuyến nghị" badge fits within the card — text does not overflow the card border. Verify with a long `good` suitability scenario.
5. **Preset selector**: 2-column grid on mobile (`grid-cols-2`). All four preset buttons visible and tappable. Each button meets ≥44px height (confirmed by `py-3` + `text-xs font-bold` = ~44px height). Tap selects correctly.
6. **Preview frame + orange box**: Image renders at `w-full h-auto`. Orange region box is positioned correctly using percentage CSS. Box is visible and not zero-size on a small preview.
7. **Crop preview strips**: Black overlay strips visible on small frame. Orange crop boundary outline renders. "Phần giữ lại sau khi cắt" label not cut off.
8. **"Không thấy logo hiển thị trên video này" button**: Full width (`w-full`). Elevated as a distinct button between presets and method selector. Tappable without hitting adjacent elements.
9. **Process CTA button**: Visible without scrolling after preset + method selection. Full width and tappable. Gradient renders correctly.
10. **Processing spinner**: Spinner and "15–90 giây" message visible during FFmpeg operation. Not obscured by browser navigation chrome (bottom bar on iOS Safari).
11. **Result card download button**: `<a>` element renders as full-width block link. Download button (`Tải video đã xử lý`) is tappable; file size text visible without truncation.
12. **"Xử lý video khác" button**: In the two-button row at the bottom of done state — both buttons fit side-by-side without overflow. "Xử lý video khác" button is tappable.
13. **Admin Flow QA tab at 375px**: Summary cards wrap to 3-column then 2-column rows (grid-cols-3 sm:grid-cols-5). Job row chips (status, result_type, flags) wrap gracefully. Detail panel two-column grid collapses to 1-column on mobile. Preview frame in detail panel visible.

---

## Section 6 — Risky Wording Inventory

QA must audit these surfaces for both prohibited phrases and required scoped language.

### Surfaces to check

- All toast messages and error notifications
- Method selector card descriptions (`desc` field in `METHODS` array)
- Suitability banner notes (`SUIT_CONFIG` notes)
- Header subtitle and feature badge ("Flow/Veo · Làm sạch logo hiển thị")
- Upload zone sub-labels
- Processing state message
- Done state result card title and quality note
- Error state title and troubleshooting copy
- No-logo state title, explanation bullets, SynthID info block
- "SynthIDNote" component text (appears in header, no-logo, and done states)
- Admin Flow QA tab scope guard text
- Admin status badges (STATUS_CLR labels), result_type badges (RESULT_CLR)
- Download filename (check slug normalization)

### Prohibited phrases — any language equivalent

| Prohibited phrase | Why prohibited |
|-------------------|---------------|
| xóa toàn bộ watermark | Implies SynthID or full watermark removal |
| gỡ SynthID / xóa SynthID | SynthID is explicitly out of scope |
| video sạch watermark 100% | False certainty + scope creep |
| video hoàn toàn sạch / video đã hoàn toàn được làm sạch | "Completely clean" is an overclaim |
| remove all AI watermarks | Implies invisible watermark removal |
| AI watermark removed / cleaned | Same |
| invisible watermark removed / hidden watermark | Out of scope |
| watermark detection success / phát hiện watermark thành công | Implies auto-detection capability the feature does not have |
| logo detection / tự động phát hiện logo | Feature does not auto-detect logos — user declares position |

### Required scoped language — confirm present

| Required phrase or concept | Expected location |
|---------------------------|-------------------|
| "logo hiển thị trên khung hình" or "logo hiển thị" | Feature badge, processing message, done title, method descriptions |
| "Watermark vô hình SynthID không nằm trong phạm vi hỗ trợ và không thể xóa bằng bất kỳ công cụ nào" | SynthIDNote component (header, no-logo, done) |
| "Chỉ áp dụng cho logo hiển thị trên khung hình" | SynthIDNote opening phrase |
| "cắt viền" / result is distinct from "làm sạch" | Done state title for crop method, admin badge "✂ cropped" |
| Scope guard in admin tab | FlowQATab header paragraph: "Visible on-frame logo only · SynthID không trong phạm vi" |
| No-logo is a valid non-failure outcome | No-logo state title "không cần làm sạch logo"; primary button "Tiếp tục với video gốc" |

---

## Section 7 — QA Outcome Tiers

Use these tiers when logging test results in the test tracking sheet.

| Tier | Meaning | Example |
|------|---------|---------|
| **Pass** | All criteria met; pipeline correct; output acceptable for scenario | TC-001 short HD clip, delogo works, file downloads, correct label |
| **Pass with warning** | Pipeline correct and label correct, but output quality is borderline | TC-002 delogo on busy bg — pipeline correct but visible delogo artifact; acceptable for release, note for improvement backlog |
| **Needs tuning** | Mode recommendation may be suboptimal for the video characteristics | `good` suitability recommended `delogo` but crop would clearly produce better output; suitability model is deterministic by design and does not assess bg complexity |
| **Fail** | Incorrect state transition, pipeline stuck, download fails, wrong `result_type`, crash | Stuck in `processing` after FFmpeg error; download 404; wrong filename suffix |
| **Misleading UI/label** | Copy overclaims, wrong label for result type, implies SynthID removal | Done title reads "watermark removed"; admin shows `job_kind=synthid_removal` |
| **Wrong mode choice** | Suitability model output does not match the scoring formula for the input | `manual` shown for a 10s 24fps 1080p clip (should be `good`); likely a frontend/backend scoring divergence |

---

## Section 8 — Explicit Pass/Fail Criteria Per Test Case

| Test ID | Pass conditions | Fail conditions |
|---------|----------------|-----------------|
| TC-001 | suitability=good, delogo recommended, download succeeds, result_type=cleaned_visible_logo, no SynthID copy | Stuck state, wrong result_type, any SynthID copy, download fails |
| TC-002 | Pipeline completes, no crash, result_type=cleaned_visible_logo, correct label; QA notes output quality (may be poor — not a fail) | Crash, stuck in processing, wrong label, pipeline returns success with no output file |
| TC-003 | suitability=crop, method auto-switched to crop on upload, crop recommended, medium_length_clip flag in admin; if user switches to delogo: crop_preferred flag appears | Suitability shown as good, no flag, delogo auto-selected without crop recommendation |
| TC-004 | long_clip flag in admin, suitability=crop level, crop prominently recommended, quality warning banner shown | No warning, suitability shown as good, long_clip flag missing |
| TC-005 | high_fps flag in admin, suitability=manual, banner says "xác nhận vùng logo", no overconfident delogo auto-clean claim | high_fps ignored, suitability=good, banner claims guaranteed result |
| TC-006 | low_res flag in admin, suitability=good (score 80), pipeline proceeds normally, QA notes output quality | Pipeline blocked by low_res, or flag missing from admin, or suitability shown as low |
| TC-007 | no-logo state reached cleanly (no process API call), "Tiếp tục với video gốc" resets to idle, not labeled as failure | Processing triggered on no-logo click, "failed" state shown, confusing error copy |
| TC-008 | Custom region accepted via API (region_source=user_custom), process completes, result_type correct for chosen method, label correct | Region rejected spuriously, crash on large or center region, wrong result_type |
| TC-009 | result_type=cropped_export, filename ends in `_cropped.mp4`, done title "cắt viền", admin badge "✂ cropped", crop_preferred flag absent (video too short) | result_type=cleaned_visible_logo, filename `_logo_cleaned.mp4`, same done title as delogo |
| TC-010 | Error state with "Không đọc được video. Kiểm tra lại file.", retry button functional, no stuck spinner | Stuck in uploading/processing, misleading success toast, no retry path |
| TC-011 | Client-side guard shows "File vượt quá 500MB." before upload starts; no network request made; error state with retry | Silent failure, partial upload state, success toast for oversized file |
| TC-012 | Error state reached (not stuck), admin metadata status=failed, failed_at timestamp set, error message present; retry from error state works | Stuck in processing state indefinitely, no error record in admin |
| TC-013 | Second run on same file: same suitability level, same method recommendation, same result_type; no stale data from first run | Different suitability on identical re-upload, state leakage visible in UI |
| TC-014 | Crop preview dark strips appear on method switch; result_type=cropped_export; admin method=crop; no stale delogo state in result | Orange box stays after switch to crop (no crop preview), admin shows method=delogo for a crop result |
| TC-015 | All preset buttons tappable at 375px, region box visible on frame, process button accessible, done state download tappable | Preset buttons clipped or inaccessible, region box zero-size, download button unreachable |
| TC-016 | Pipeline completes with no crash, result_type=cleaned_visible_logo, no special handling for partial-logo video; QA notes visual consistency | Crash or error on partial-logo video, special "partial logo detected" logic that doesn't exist |
| TC-017 | Feature handles video identically to TC-001 — no special blocking or error for unexpected logo on Ultra-tier source | Feature refuses to process, throws "this video should not have logo" error, or any special-case branch |
| TC-018 | Flow QA tab loads without JavaScript error or 500; expired jobs simply absent from list (glob finds no metadata.json in deleted dirs) | Tab throws uncaught error, 500 from admin API on missing dirs, tab freezes |

---

## Section 9 — Missing Cases To Add Before Public Production Rollout

These scenarios are not covered by the matrix above and should be prioritized before broad rollout.

1. **WebM input processing**: Upload and fully process a `.webm` file through delogo. Codec compatibility between VP8/VP9 WebM and FFmpeg `delogo` filter is not guaranteed. Verify no FFmpeg error; if codec-incompatible, document the failure mode and error message.

2. **Very short clip (< 2s)**: Upload a 1-second clip. Frontend `computeSuitability`: score = 100−20 = 80 → `good`, but `very_short` flag is set in frontend flags array. Backend does NOT have a `very_short` warning flag in its `warning_flags` logic (backend only has `long_clip`, `medium_length_clip`, `high_fps`, `low_res`, `crop_preferred`). Verify whether UI should surface a "too short" warning and whether FFmpeg processes it correctly.

3. **Vertical video (9:16, e.g. 1080×1920)**: Upload a portrait-orientation clip. Verify: (a) region presets map correctly — lower-right in portrait means the logo is near the narrow right edge, not a wide strip; (b) crop preview boundary math is correct for portrait dimensions; (c) `computeCropBounds()` produces sensible results for portrait aspect ratios.

4. **Square video (1:1, e.g. 1080×1080)**: Same validation as vertical video for preset region mapping and crop boundary math.

5. **Upper-left preset + crop mode**: Test specifically the `upper-left` preset with `crop` method. This exercises the `cy = yPct + hPct; ch = 1 − cy` branch of `computeCropBounds()` combined with `cx = xPct + wPct; cw = 1 − cx`. The resulting crop strips top and left edges simultaneously — verify FFmpeg crop command `cw:ch:cx:cy` is correct and output matches preview.

6. **Blur method dedicated test**: Upload a short HD clip, select blur method, process. Verify: (a) `result_type = cleaned_visible_logo` (not `cropped_export`); (b) `method = blur` in admin metadata; (c) output file has a visible blur patch over the logo area; (d) filename ends in `_logo_cleaned.mp4`; (e) done title says "làm sạch logo hiển thị" (same as delogo, not a crop title).

7. **Browser back/forward navigation**: After reaching done state, press browser back then forward. Verify React state does not partially restore (stale result or processing state). Since the app uses `popstate`-based routing without page reload, test that navigating away and back resets the component state.

8. **Concurrent jobs from same browser device**: Open two tabs simultaneously, upload different videos in each, process both. Verify: (a) `temp_id` values are distinct (UUIDs); (b) no `cleaned_path` collision between the two jobs; (c) each tab shows its own result independently.

9. **Admin Flow QA tab with 20+ simultaneous jobs**: Create many jobs in quick succession (or via API), then load the Flow QA tab. Verify: (a) tab does not freeze or lag; (b) all job rows render correctly; (c) detail panel expansion works; (d) summary counts are accurate.

10. **Admin tab refresh button behavior**: While on the Flow QA tab, process a new job in another tab/window, then click the RefreshCw button. Verify the new job appears (i.e. the refresh calls `fetchFlowQA()` and re-fetches from server, not from stale React state). Note: `fetchFlowQA` is triggered by the `onRefresh` prop passed to `FlowQATab`.

11. **Download filename with unicode or special characters in source filename**: Upload a file named `video Làm Sạch.mp4` (Vietnamese filename with spaces). Verify the slug in the download filename is normalized to ASCII (e.g. `input_logo_cleaned.mp4` or `flow_veo_logo_cleaned.mp4`) — no spaces, no unicode, no percent-encoding in the `Content-Disposition` filename. The backend uses `unicodedata.normalize("NFKD") + encode("ascii", "ignore")` on the base name.

12. **Admin auth enforcement on flow-cleanup endpoints**: Issue a `GET /api/v1/admin/flow-cleanup/jobs` request without the `X-Admin-Token` header (or with a wrong token). Verify the response is 401 (Unauthorized), not 200 or a redirect. The `verify_admin` dependency on FastAPI should block unauthenticated access.

---

## Appendix — Suitability Scoring Quick Reference

Use this to manually verify `suitability_level` shown in the admin detail panel.

```
score = 100

duration > 300s  → score −35, frontend flag: very_long
duration > 120s  → score −15, frontend flag: long          [mutually exclusive with very_long]
duration < 2s    → score −20, frontend flag: very_short

fps > 48         → score −25, frontend flag: high_fps
fps > 30         → score −8,  frontend flag: medium_fps    [mutually exclusive with high_fps]

width < 640 OR height < 360  → score −20, frontend flag: low_res

cropSuggested = (very_long OR long) AND NOT high_fps AND NOT low_res

if score >= 80:           level = good
elif score >= 60:         level = manual
elif cropSuggested:       level = crop      [takes priority over low when cropSuggested=true AND score<60]
else:                     level = low
```

**Backend warning_flags written to metadata.json** (separate from frontend scoring flags):

```
long_clip           duration > 300s
medium_length_clip  duration > 120s  (inclusive with long_clip — only one fires; long_clip takes priority)
high_fps            fps > 48
low_res             width < 640 OR height < 360
crop_preferred      method != crop AND duration > 120s AND fps <= 30
```

Note: The frontend scoring uses internal flag names (`very_long`, `long`, `very_short`, `high_fps`, `medium_fps`, `low_res`) that differ from the backend `warning_flags` names (`long_clip`, `medium_length_clip`). The frontend flags are used only for suitability computation and are not persisted. The backend flags are written to `metadata.json` and visible in the admin tab.

---

## Appendix — Key API Endpoints for Manual Testing

| Method | URL | Auth | Purpose |
|--------|-----|------|---------|
| POST | `/api/v1/flow-cleanup/upload` | None (rate-limited: 10/min) | Upload video, get temp_id + preview_url + video_info |
| GET | `/api/v1/flow-cleanup/frame/{temp_id}` | None | Serve first-frame JPEG |
| POST | `/api/v1/flow-cleanup/process` | None (rate-limited: 5/min) | FFmpeg cleanup; returns result metadata |
| GET | `/api/v1/admin/flow-cleanup/jobs` | `X-Admin-Token` required | List all live cleanup jobs (reads metadata.json files) |
| GET | `/api/v1/admin/flow-cleanup/jobs/{temp_id}` | `X-Admin-Token` required | Detail + file inventory for one job |
| GET | `/api/v1/download-local?filepath=...&filename=...` | None | Serve output file for download |

**Process request body (for direct API testing):**

```json
{
  "temp_id": "<hex from upload response>",
  "preset": "lower-right",
  "region": null,
  "method": "delogo",
  "suitability_level": "good"
}
```

`preset` options: `lower-right`, `lower-left`, `upper-right`, `upper-left`, `custom`
`method` options: `delogo`, `crop`, `blur`
For `preset=custom`, provide `region: {"x": N, "y": N, "w": N, "h": N}` in pixels.

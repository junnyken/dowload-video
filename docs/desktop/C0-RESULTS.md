# Phase 32C / C0 — Experiment results (TEMPLATE)

**Tóm tắt cho chủ dự án (VI).** Đây là mẫu điền kết quả cho thí nghiệm E1–E5 (kế hoạch ở `C0-EXPERIMENTS.md`). Mỗi thí nghiệm có một bảng; cuối file là bảng quyết định `handoff_ok` theo từng nền tảng — **mỗi giá trị phải trỏ tới một dòng bằng chứng** (tiêu chí nghiệm thu số 10). Ô trống = chưa đo. Không điền số ước đoán; nếu chưa đo thì ghi `not measured`.

Status: TEMPLATE — no experiment has been run when this file was created (2026-10-06).
Rule: an empty cell or `not measured` is treated as FALSE for any decision.

## 0. Run metadata (fill once per session)

| Field | Value |
|---|---|
| Operator | |
| Date range (with timezone) | |
| yt-dlp version / SHA256 | |
| ffmpeg version / source / SHA256 | |
| Deno version / SHA256 | |
| Resolver label (E1-W workspace / E1-P production) and IP class | |
| Home network label(s) and ISP (do not paste raw IPs outside this file) | |
| Link-picking procedure and date (see E1 "How links are picked") | |

---

## E1 — IP binding of CDN links

Evidence row ids: `E1-<platform>-<nn>`. One row per link.

| Row id | Platform | Page URL | Resolver (W/P) | resolved_at (UTC) | Fetch network | fetched_at (UTC) | Delay (s) | Control status/bytes | Home bare status/bytes | Home headers status/bytes | Content-type | `ip=`/`expire=` param present | Browser check (Chrome/Edge) | Full download (ok / late 403 / short) | Class (success / fail_403 / dead_link / needs_merge / resolve_failed) | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| E1-tiktok-01 | | | | | | | | | | | | | | | | |

Per-platform summary (computed from the rows above; do not type a rate without the counts):

| Platform | Links tried | resolve_failed | needs_merge | dead_link (control failed) | Denominator | Success | Rate | Mid-transfer failures | Browser-check failures | Row ids |
|---|---|---|---|---|---|---|---|---|---|---|
| tiktok | | | | | | | | | | |
| douyin | | | | | | | | | | |
| threads | | | | | | | | | | |
| bilibili | | | | | | | | | | |
| reddit | | | | | | | | | | |
| twitter | | | | | | | | | | |
| vk | | | | | | | | | | |
| dailymotion | | | | | | | | | | |
| rumble | | | | | | | | | | |
| odysee | | | | | | | | | | |
| soundcloud | | | | | | | | | | |
| pinterest | | | | | | | | | | |
| twitch | | | | | | | | | | |
| facebook | | | | | | | | | | |
| instagram | | | | | | | | | | |
| xiaohongshu | | | | | | | | | | |
| lemon8 | | | | | | | | | | |
| snapchat | | | | | | | | | | |
| youtube (informational; never handoff_ok) | | | | | | | | | | |

<!-- E1 RESULTS: filled by main session -->

### E1-P pilot — 2026-10-06 ~00:20 Vietnam time (2026-10-05 17:20 UTC)

**This is a pilot, not the decision run.** 10 real links across 5 platforms, not 10+ per platform. The fetching network was the Coder workspace (a datacenter network distinct from the production backend), not a home connection. Only the first 64 KiB were fetched (`Range: bytes=0-65535`), immediately (seconds) after resolving; full transfers and link lifetime were not tested.

Method: `POST https://dvid-api.vibe1.tinhgon.xyz/api/v1/fetch-link {"url":…,"quality":"video"}` as a guest (production resolves; for TikTok the URLs come from the TikWM provider), then from the workspace `GET <url>` with a desktop Chrome User-Agent and no cookies/Referer. Raw output: `e1_results.json` (kept in the session scratchpad, not in git).

| Row id | Link | Resolve | Candidate URL (format) | CDN host | `ip=` in URL | Fetch from other network |
|---|---|---|---|---|---|---|
| E1-tiktok-1a..d | tiktok @tiktok/6584647400055377158 | 200 | direct, "Thường không logo", "Có logo", MP3 | tiktokcdn-us.com / tiktokv.us | none | 206 ×4 (video/mp4, audio/mpeg) |
| E1-tiktok-2a..d | tiktok @rollwgrzxkh/7629952502376500487 | 200 | same four | tiktokcdn-us.com | none | 206 ×4 |
| E1-bilibili-1a..d | bilibili BV1ECeJ65EZS | 200 | direct, HD, SD, 360p | upos-hz-mirrorakam.akamaized.net | none | 206 ×4, **no Referer needed** |
| E1-bilibili-2 | bilibili BV1GJ411x7h7 | 422 (extract failed) | — | — | — | not tested |
| E1-dailymotion-1a..d | dailymotion x5e9eog | 200 | direct, HD, SD, 288p | vod3.cf.dmcdn.net | none | 206, but the body is an **HLS playlist** (`application/vnd.apple.mpegurl`), not a file — segments not tested |
| E1-dailymotion-2 | dailymotion x8ndjq1 | 500 (extract failed) | — | — | — | not tested |
| E1-threads-1 | threads @threads/DOJPyNPEVvT | 200 | direct | fbcdn.net | none | 206 but **`image/jpeg`** — the "video" URL is an image (photo post, or a resolver bug — not investigated) |
| E1-douyin-1..3 | 3 × v.douyin.com short links | 500 "Douyin requires valid cookie" | — | — | — | not tested |
| E1-youtube | (earlier VTV1 video) | 200 | **no URLs returned** (production YouTube path is HLS/proxy only) | — | — | not testable via API |

Observations (pilot only):
- TikTok: 8/8 URLs from 2/2 links fetched from a different network. No IP parameter.
- Bilibili: 4/4 URLs from 1 link fetched; URL carries `deadline`/`expire`-style param `1791228095` = 2026-10-05 19:21:35 UTC, about **2 h** after resolving (from the URL, not measured by E2). Whether "HD" is video-only (needs merge) was not checked.
- Dailymotion returns HLS playlists: T1 would need the browser to play/assemble HLS — treat as not T1-eligible.
- Threads pilot link returned an image: needs a real video post before any conclusion.
- Douyin: cannot be resolved on production without cookies (known, separate task).
- No platform reached the 20-link sample size; **no `handoff_ok` value may be set from this pilot.**

---

## E2 — Link lifetime

Evidence row ids: `E2-<platform>-<n>`.

| Row id | Platform | Resolver network | resolved_at (UTC) | Last 2xx age (min) | First failure age (min) / code | `expire=` param (converted: min after resolve) | STILL_ALIVE_24H | Notes |
|---|---|---|---|---|---|---|---|---|
| E2-tiktok-1 | | | | | | | | |

Per-platform: shortest observed lifetime (min), proposed `expiresAt` offset (min) = 50% of shortest (proposal; owner approves rule), row ids.

| Platform | Shortest lifetime (min) | Proposed expiresAt offset (min) | Row ids |
|---|---|---|---|
| | | | |

<!-- E2 RESULTS: filled by main session / owner -->

---

## E3 — Local yt-dlp from home IPs (YouTube)

Evidence row ids: `E3-<machine>-<cfg>-<nn>` (one per video run) — raw CSVs are attached as `e3-<machine>-<cfg>.csv`.

Machines:

| Machine id | Windows build | ISP / IP class | Country | Notes |
|---|---|---|---|---|
| M1 | | | | |
| M2 | | | | |
| M3 | | | | |

Config definitions (fill exact versions): a = yt-dlp alone; b = + Deno; c = + PO-token provider (name/version: ____ ).

| Machine | Config | Runs | Successes | Rate | Dominant error classes | Row ids / CSV |
|---|---|---|---|---|---|---|
| M1 | a | 30 | | | | |
| M1 | b | 30 | | | | |
| M1 | c | 30 | | | | |
| M2 | a | 30 | | | | |
| M2 | b | 30 | | | | |
| M2 | c | 30 | | | | |
| M3 | a | 30 | | | | |
| M3 | b | 30 | | | | |
| M3 | c | 30 | | | | |
| **Pooled** | a | 90 | | | | |
| **Pooled** | b | 90 | | | | |
| **Pooled** | c | 90 | | | | |

Decision (rule: below 90 percent → bundle a token provider or restrict YouTube support):

| Shipped config under test | Pooled rate | >= 90%? | Decision | Evidence rows |
|---|---|---|---|---|
| | | | | |

Shelf-life note: date of last run ______ (YouTube behaviour changes; re-run before C3).

<!-- E3 RESULTS: filled by owner -->

---

## E4 — Browser-side merge (ffmpeg.wasm) vs native

Evidence row ids: `E4-<tool>-<sizeMB>-<rep>`.

| Row id | Input video bytes | Input audio bytes | Tool (native / wasm-st / wasm-mt) + version | Browser + version / OS | Rep | Outcome (ok / fail) | Time (s) | Peak memory (MB) | Error text | Notes |
|---|---|---|---|---|---|---|---|---|---|---|
| E4-native-50-1 | | | | | | | | | | |

Summary:

| Tool / browser | Largest total input that passed 3/3 (bytes) | Smallest that failed (bytes) | Row ids |
|---|---|---|---|
| native ffmpeg | | | |
| wasm-st, headless Chromium | | | |
| wasm-mt, headless Chromium | | | |
| wasm, real desktop Chrome (Windows) | | | |

Decision: `max_ok_bytes` = ______ ; web-merge threshold (proposal 50% of it) = ______ ; merging above it → T2.

<!-- E4 RESULTS: filled by main session -->

### E4 run — 2026-10-06, Coder workspace (Linux, 12 CPUs, 10 GiB memory limit)

Samples: 1080p30 H.264 video-only (`testsrc2`, libx264 ultrafast, 8 Mbit/s, no audio) + AAC 128 kbit/s audio-only, generated with ffmpeg 7.0.2 static. Merge = `-i v.mp4 -i a.m4a -c copy -map 0:v -map 1:a out.mp4` (stream copy, the same operation yt-dlp does for separate streams).
- native: ffmpeg 7.0.2 static (johnvansickle build via `imageio-ffmpeg`), file on local disk, time includes writing the output.
- wasm-st: `@ffmpeg/ffmpeg` 0.12.15 + `@ffmpeg/core` 0.12.10 (single-thread), headless Chromium 148.0.7778.96 via Playwright, files fetched over localhost into MEMFS, output read back with `readFile`. Time = `exec` only; fetch/load listed separately. One rep per size.

| Row id | Video bytes | Audio bytes | Tool | Outcome | Merge time | Other | JS heap after (MB) |
|---|---|---|---|---|---|---|---|
| E4-native-50-1 | 50,292,884 | 811,378 | native | ok | 1.06 s | | |
| E4-native-200-1 | 200,009,620 | 3,244,995 | native | ok | 3.47 s | | |
| E4-native-500-1 | 500,021,284 | 8,114,425 | native | ok | 8.09 s | | |
| E4-native-1000-1 | 1,000,079,816 | ~16.2 M | native | ok | 18.5 s | | |
| E4-native-1800-1 | 1,800,150,562 | ~29.2 M | native | ok | 31.2 s | | |
| E4-wasm-st-50-1 | 50,292,884 | 811,378 | wasm-st | ok, out 51,131,106 B | 0.63 s | load 0.35 s, fetch 0.2 s | 50 |
| E4-wasm-st-200-1 | 200,009,620 | 3,244,995 | wasm-st | ok, out 203,363,319 B | 1.96 s | fetch 0.6 s | 196 |
| E4-wasm-st-500-1 | 500,021,284 | 8,114,425 | wasm-st | ok, out 508,407,417 B | 4.86 s | fetch 1.5 s | 487 |
| E4-wasm-st-1000-1 | 1,000,079,816 | ~16.2 M | wasm-st | ok, out 1,016,856,366 B | 11.5 s | fetch 21.1 s | 971 |
| E4-wasm-st-1800-1 | 1,800,150,562 | ~29.2 M | wasm-st | ok, out 1,830,350,850 B | 20.6 s | fetch 26.7 s | 1,747 |

(The 1000/1800 audio sizes were not recorded before the files were deleted; they follow the same 128 kbit/s rate.)

Findings:
- On this machine ffmpeg.wasm merged **every size up to 1.8 GB** (exit code 0, output size = video + audio + container overhead). The plan's expectation "fails above roughly 200 MB" did **not** hold here.
- JS heap grows ≈ 1× the file size and MEMFS holds input + output, so peak memory is roughly 2× the file. This machine has 10 GiB; a typical 8 GB Windows laptop with other tabs open is the real test.
- NOT tested: saving the merged output to disk from the browser (Blob + download = another copy), the 2 GiB+ input range, wasm-mt, mobile browsers, a real Windows Chrome. Output playability was checked only by exit code and size, not by playing the file.
- Decision: **not yet decidable.** Before setting a web-merge threshold, repeat E4 on the owner's Windows Chrome (8 GB machine) at 200 / 500 / 1000 MB including the save-to-disk step. Until then the plan's rule stands: merging goes to T2.

---

## E5 — `vidgrab://` handoff from extension

Evidence row ids: `E5-<os>-<browser>-<case>`.

Machines: Win10 build ______ (standard user? y/n) ; Win11 build ______ (standard user? y/n).

| Row id | OS | Browser (version) | Case (link click / extension / cold / warm / Win+R) | Browser prompt shown | App opened | URL intact | Admin/UAC prompt | Notes |
|---|---|---|---|---|---|---|---|---|
| E5-w11-chrome-ext-cold | | | | | | | | |

Other checks:

| Check | Result | Row / evidence |
|---|---|---|
| Part A (HKCU manual registration works as standard user) | | |
| Installer writes only HKCU (no HKLM) | | |
| Installer shows no UAC prompt | | |
| Max URL length that survives (chars) | | |
| App requires explicit confirmation before download | | |
| URL validated as untrusted input | | |

Decision (rule: keep only if it works on a clean Windows install without admin rights): ______

<!-- E5 RESULTS: filled by owner -->

---

## Final decision table — `handoff_ok` per platform

Every value in column "handoff_ok" must cite evidence in the "Evidence" column (E1 summary row, plus E2 for `expiresAt`). A value without a citation is invalid. Default for any platform with no row: `false` (not measured).

Required conditions for `true`: E1 rate >= 95 percent over a denominator of at least 10 (20 recommended), zero mid-transfer failures, zero browser-check failures, an E2 lifetime measured, and the format is single-stream (never offered when `requiresMerge`).

| Platform | handoff_ok | E1 rate (n/denominator) | E1 evidence (summary row + link-row ids) | E2 shortest lifetime (min) | expiresAt offset (min) | E2 evidence | Delivery decision (T0 / T1 / T2) | Reviewer + date |
|---|---|---|---|---|---|---|---|---|
| tiktok | | | | | | | | |
| douyin | | | | | | | | |
| threads | | | | | | | | |
| bilibili | | | | | | | | |
| reddit | | | | | | | | |
| twitter | | | | | | | | |
| vk | | | | | | | | |
| dailymotion | | | | | | | | |
| rumble | | | | | | | | |
| odysee | | | | | | | | |
| soundcloud | | | | | | | | |
| pinterest | | | | | | | | |
| twitch | | | | | | | | |
| facebook | | | | | | | | |
| instagram | | | | | | | | |
| xiaohongshu | | | | | | | | |
| lemon8 | | | | | | | | |
| snapchat | | | | | | | | |
| youtube | false (fixed by plan rule 4: server never downloads bytes; T2 only) | informational | | | | | T2 subject to E3 | |

Resulting flag values to propose (only after the table above is complete):
- `DELIVERY_HANDOFF_PLATFORMS=` ______ (comma list of platforms with handoff_ok = true)
- `DELIVERY_HANDOFF_ENABLED=` false until reviewed and rolled out (C3)

Other decisions fed by C0:

| Question | Answer | Evidence |
|---|---|---|
| E3: is a PO-token provider needed? | | |
| E3: restrict YouTube support? | | |
| E4: merge-above-size routed to T2 at threshold | | |
| E5: keep `vidgrab://` handoff? | | |

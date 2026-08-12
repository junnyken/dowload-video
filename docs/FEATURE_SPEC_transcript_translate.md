# Feature Spec — Transcript Translate + Transcript ASR

> Tài liệu đặc tả đầy đủ tính năng "Dịch phụ đề SRT/VTT" + "Tạo phụ đề tự động (ASR)"
> đã build trong VidGrab (2026-08-04/05). Dùng tài liệu này để hand-off cho một
> công cụ/AI khác build lại tính năng này từ đầu, độc lập với codebase gốc.
> Viết bằng tiếng Anh cho phần kỹ thuật (chính xác thuật ngữ), có tóm tắt tiếng Việt.

## 0. Tóm tắt (tiếng Việt)

Tính năng cho phép người dùng:
1. **Dịch phụ đề có sẵn**: upload file `.srt`/`.vtt`, chọn ngôn ngữ đích → hệ thống
   tự nhận diện ngôn ngữ gốc, dịch nội dung từng dòng phụ đề bằng LLM (Gemini/OpenAI)
   **giữ nguyên mốc thời gian gốc**, dịch theo nghĩa (không dịch máy word-by-word).
   Có thể xem/sửa tay bản dịch trước khi tải về, và ghép (burn) phụ đề đã dịch
   thẳng vào video đã tải trước đó.
2. **Tự tạo phụ đề (ASR)**: chọn 1 video đã tải sẵn trên hệ thống → tự tách audio,
   nhận diện giọng nói bằng Whisper → ra file `.srt` có timestamp. Có thể dịch
   luôn kết quả ASR sang ngôn ngữ khác (chain thẳng sang tính năng #1).

Cả hai chạy async qua Celery, có quota/ngày theo user, cache dịch thuật chống
dịch lại nội dung trùng lặp, và có cơ chế resume khi job bị gián đoạn giữa chừng.

---

## 1. Feature Overview (end-to-end)

### 1.1 Transcript Translate
- User uploads a batch (up to 10) of `.srt`/`.vtt` files (max 2MB each, max 4000
  cues per file) plus picks one target language from a fixed 10-language list.
- Backend validates + parses each file independently; bad files are rejected
  without failing the whole batch.
- Each valid file becomes an async job. A Celery task:
  1. Detects the source language via one bounded LLM call.
  2. Translates cue text in chunks of 35 cues per LLM call, preserving order,
     meaning (not literal), and exact original timing.
  3. Serializes the translated cues back into the same format (`.srt`/`.vtt`).
- Frontend polls job status every 5s while any job is non-terminal, shows a
  progress bar (`translated_cue_count / cue_count`).
- Once done, user can: download the translated file, view/edit individual cue
  translations in a modal editor, or burn the translated subtitle into a video
  they already downloaded via this app (FFmpeg hardsub).

### 1.2 Transcript ASR
- User picks one already-downloaded video from their history (must still be on
  disk server-side).
- Backend probes duration via `ffprobe`, rejects if > 45 minutes, reserves a
  daily-minutes quota.
- Celery task extracts a mono, low-bitrate audio track via FFmpeg, sends it to
  OpenAI Whisper (`whisper-1`, verbose_json, segment-level timestamps), builds
  SRT cues from the returned segments.
- Result: downloadable `.srt`. User can chain straight into Transcript Translate
  (creates a translation job from the ASR output without re-uploading).

---

## 2. Complete API Surface

Base prefix (per `app/api/routes.py` router mounting convention): `/api/v1/transcript-translate` and `/api/v1/transcript-asr`.

### 2.1 Transcript Translate — `/api/v1/transcript-translate`

| Method | Path | Purpose |
|---|---|---|
| POST | `/upload` | Upload a batch of .srt/.vtt files for translation |
| GET | `/jobs` | List jobs for the resolved identity, newest first (limit 50) |
| GET | `/jobs/{job_id}/download` | Stream the translated result file |
| GET | `/jobs/{job_id}/cues` | Return every translated cue (index/start/end/text) for the editor |
| PUT | `/jobs/{job_id}/cues` | Apply manual text-only edits to a subset of cues |
| POST | `/jobs/{job_id}/burn-into-video` | Burn this job's translated subtitle into an already-downloaded video |
| DELETE | `/jobs/{job_id}` | Delete job row + its files |

**POST /upload**
- Request: `multipart/form-data` — `files: File[]` (required, 1-10 files), `target_lang: str` (form field, one of the 10 allowed codes).
- Response `200`:
  ```json
  {
    "jobs": [
      {
        "id": "uuid", "filename": "movie.srt", "status": "queued",
        "source_lang": null, "target_lang": "vi",
        "cue_count": 120, "translated_cue_count": 0, "progress_pct": 0,
        "error": null, "skipped_block_count": 0, "reading_speed_warning_count": 0
      }
    ],
    "rejected": [ { "filename": "bad.txt", "reason": "Định dạng không hỗ trợ..." } ]
  }
  ```
- Per-file validation order (each failure short-circuits that file only, continues batch): extension in `{.srt, .vtt}` → size ≤ 2MB → UTF-8 decodable → parses to ≥1 cue → cue_count ≤ 4000 → no single cue's text > 2000 chars → daily quota check (atomic RPC reservation) → persist to disk → insert DB row → enqueue Celery task.
- Auth: requires a resolvable identity (see §5) — else `401`.

**GET /jobs** → `{"jobs": [ {...same shape as above...} ]}`, scoped to caller's identity (real user_id, or exact NULL match for anonymous — not partial).

**GET /jobs/{id}/download** → streams `FileResponse` with `media_type: text/plain; charset=utf-8`, filename `{base}_translated{ext}`. 404 if job not done/not owned/file missing.

**GET /jobs/{id}/cues** → `{"cues": [{"index": int, "start": "00:00:01,000", "end": "00:00:04,000", "text": "..."}]}`. 404 unless job done + owned.

**PUT /jobs/{id}/cues**
- Request: `{"cues": [{"index": int, "text": str}]}` (only changed cues need to be sent).
- Validates: total cues ≤ 4000, each `text` ≤ 2000 chars.
- Only `text` is mutable — `index`/`start`/`end` are never touched by this endpoint (can't add/remove/reorder/re-time cues).
- Unknown indices are silently ignored (not an error).
- Re-serializes and overwrites `result_path` on disk, recomputes reading-speed warning count, persists it.
- Response: `{"success": true, "reading_speed_warning_count": int}`.

**POST /jobs/{id}/burn-into-video**
- Request: `{"video_local_path": "<server-side path from download history>"}`.
- Requires job done + owned; subtitle file (`result_path`) must still exist.
- `video_local_path` is validated via a path-traversal guard (must resolve inside the server's downloads dir, must exist).
- Runs FFmpeg hardsub (see §4.7), writes to a new output file, schedules auto-cleanup (20 min countdown via Celery).
- Response: `{"success": true, "download_url": "/api/v1/download-local?filepath=...&filename=...", "output_path": "...", "file_size_mb": float, "expires_in_seconds": 1200}`.

**DELETE /jobs/{id}** → deletes DB row + input/result files + job work dir. Response `{"deleted": true}`.

### 2.2 Transcript ASR — `/api/v1/transcript-asr`

| Method | Path | Purpose |
|---|---|---|
| POST | `/jobs` | Create an ASR job from an already-downloaded video |
| GET | `/jobs` | List jobs for resolved identity |
| GET | `/jobs/{job_id}/download` | Stream the generated .srt |
| POST | `/jobs/{job_id}/translate` | Chain: create a Transcript Translate job from this ASR result |
| DELETE | `/jobs/{job_id}` | Delete job + result file (never the source video) |

**POST /jobs**
- Request: `{"video_local_path": str, "video_title": str | null}`.
- `video_local_path` validated via the same path-traversal guard used app-wide.
- Probes duration via `ffprobe`; rejects `duration_sec <= 0` (400) or `> 45min` (422).
- Reserves daily-minutes quota atomically (RPC) — 422 if exceeded.
- Inserts `transcript_asr_jobs` row, enqueues `transcribe_video_task`.
- Response: job object `{"id", "video_title", "status": "queued", "duration_sec", "detected_language": null, "progress_pct": 0, "error": null}`.

**GET /jobs** → `{"jobs": [{"id","video_title","status","duration_sec","detected_language","progress_pct","error"}]}`.

**GET /jobs/{id}/download** → `.srt` file, filename `{video_title or "transcript"}.srt`.

**POST /jobs/{id}/translate**
- Request: `{"target_lang": str}`.
- Loads the ASR job's result `.srt`, parses it, re-validates against Transcript Translate's own limits (cue count, cue length), reserves Transcript Translate's own daily cue quota (separate quota pool from ASR minutes), then creates+enqueues a normal `transcript_translation_jobs` row exactly as if the user had uploaded that file — this is pure convenience (skip download+re-upload), no new job type.
- Response: same shape as `/transcript-translate/upload`'s per-job object.

**DELETE /jobs/{id}** → removes DB row + `result_path` file only. The source video (`video_local_path`) is never touched — it belongs to a different feature's lifecycle.

---

## 3. Data Model (Postgres/Supabase)

### 3.1 `transcript_translation_jobs`

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | `gen_random_uuid()` |
| user_id | TEXT | real auth user id OR anonymous session id; no FK |
| source_filename | TEXT NOT NULL | |
| source_format | TEXT NOT NULL | CHECK IN ('srt','vtt') |
| source_lang | TEXT | filled after language-detection step |
| target_lang | TEXT NOT NULL | one of the 10 allowed codes |
| cue_count | INT | |
| translated_cue_count | INT NOT NULL DEFAULT 0 | |
| status | TEXT NOT NULL DEFAULT 'queued' | CHECK IN ('queued','detecting','translating','done','failed') |
| progress_pct | INT NOT NULL DEFAULT 0 | |
| error_message | TEXT | |
| input_path | TEXT NOT NULL | on-disk path to uploaded original |
| result_path | TEXT | on-disk path to translated output |
| celery_task_id | TEXT | |
| resume_chunk_index | INT NOT NULL DEFAULT 0 | chunk-level resume pointer (added later) |
| timeout_retry_count | INT NOT NULL DEFAULT 0 | bounded self-requeue counter (added later) |
| skipped_block_count | INT NOT NULL DEFAULT 0 | malformed cue blocks silently dropped at parse time (added later) |
| reading_speed_warning_count | INT NOT NULL DEFAULT 0 | QA warning count (added later) |
| created_at / updated_at | TIMESTAMPTZ | `updated_at` auto-bumped by trigger |
| expires_at | TIMESTAMPTZ NOT NULL DEFAULT NOW()+7 days | |

Index: `(user_id, created_at)`. RLS enabled, single `service_role_bypass` policy (backend uses the Supabase service-role client, not per-user RLS).

### 3.2 `transcript_translation_cache`
Cross-job, cross-user dedup cache so identical (source text, target language) pairs are never re-translated.

| Column | Type |
|---|---|
| content_hash | TEXT PK — `sha256(normalized_text + "\x1f" + target_lang)` |
| target_lang | TEXT NOT NULL |
| translated_text | TEXT NOT NULL |
| hit_count | INT NOT NULL DEFAULT 0 |
| created_at / last_hit_at | TIMESTAMPTZ |

Index on `target_lang`. RPC `bump_transcript_cache_hit(p_hash)` — best-effort hit counter.

### 3.3 `transcript_translation_usage` — daily per-identity cue quota
PK `(user_id, usage_date)`. Columns: `cues_translated INT`, `jobs_count INT`, `updated_at`.

RPC `reserve_transcript_translation_usage(p_user_id, p_cues, p_limit) RETURNS BOOLEAN` — atomic "insert row if missing → `SELECT ... FOR UPDATE` lock → check `current + p_cues > p_limit` → increment or return false". This row-level lock is what makes concurrent uploads from the same identity serialize correctly instead of racing past the cap.

### 3.4 `transcript_asr_jobs`

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | TEXT | |
| video_local_path | TEXT NOT NULL | points into the shared downloads dir — NOT per-job, never deleted by this feature's cleanup |
| video_title | TEXT | display label copied at creation time |
| duration_sec | NUMERIC | probed |
| detected_language | TEXT | Whisper's detected language |
| status | TEXT NOT NULL DEFAULT 'queued' | CHECK IN ('queued','extracting_audio','transcribing','done','failed') |
| progress_pct | INT NOT NULL DEFAULT 0 | |
| error_message | TEXT | |
| result_path | TEXT | generated .srt |
| celery_task_id | TEXT | |
| timeout_retry_count | INT NOT NULL DEFAULT 0 | |
| created_at / updated_at / expires_at | TIMESTAMPTZ | same 7-day expiry convention |

### 3.5 `transcript_asr_usage` — daily per-identity **minutes** quota
Same shape/pattern as §3.3 but `minutes_used NUMERIC` instead of cue count. RPC `reserve_transcript_asr_usage(p_user_id, p_minutes, p_limit)`.

### 3.6 Shared DB conventions
- All tables: RLS enabled + single `service_role_bypass` policy (`USING (auth.role() = 'service_role')`) — the backend always talks to Postgres via the Supabase service-role key, application-level identity scoping happens in the API layer (`_load_owned_job`), not via RLS per-row policies.
- `set_updated_at()` trigger function (shared, created once) auto-bumps `updated_at` on every UPDATE.
- Both job tables use a background "expiry sweep" Celery beat task that deletes rows (and their files) where `expires_at < NOW()`.

---

## 4. Core Algorithms (exact logic)

### 4.1 Identity resolution (shared by both features)
```python
def _get_session_id(request) -> str:
    return (request.headers.get("X-Session-ID") or "")[:64]

async def resolve_identity(request, user=Depends(get_optional_user)) -> str | None:
    if user:
        return user["id"]              # decoded Supabase JWT from Authorization header
    return _get_session_id(request) or None   # anonymous fallback
```
Frontend generates a stable per-browser UUID on first load (`localStorage['vg_session_id']`, `crypto.randomUUID()`), sends it as `X-Session-ID` on every request alongside `Authorization: Bearer <token>` when logged in — this way an expired Supabase token still resolves to a consistent (not null) identity instead of orphaning the request. Job listing scopes by exact match on `user_id` (real id) or `IS NULL`/anonymous-session-id depending which identity resolved — never a mix.

### 4.2 Subtitle parsing/serialization (`.srt` / `.vtt`)
Dependency-free, regex-based. Key design: `Cue{index, start, end, text}` where `start`/`end` are **raw timestamp strings preserved byte-for-byte** — never reformatted/reparsed except when explicitly converting for QA math.

**SRT block regex:** `^\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})(.*)$`
**VTT block regex:** `^\s*(\d{1,2}:\d{2}:\d{2}\.\d{1,3}|\d{2}:\d{2}\.\d{1,3})\s*-->\s*(same)(.*)$` (VTT allows `MM:SS.mmm` short form too)

Parse algorithm (both formats near-identical):
1. Strip UTF-8 BOM if present (`﻿` prefix) — common from Windows-exported files, otherwise silently corrupts the first cue's index line.
2. Normalize line endings (`\r\n`/`\r` → `\n`), split into blocks on blank lines (`\n\s*\n`).
3. Per block: strip trailing empty lines; if empty, skip.
4. SRT: first line may be a numeric index (if not, or if it's a timestamp line already, fall back to a sequential counter). Next line must match the time-arrow regex — if not, **silently drop the block** but increment a `skipped` counter (surfaced to the user as `skipped_block_count`, NOT raised as an error).
5. VTT: strip a `WEBVTT` header on the first block; skip `NOTE`/`STYLE`/`REGION` blocks entirely (not counted as "skipped" — intentional non-cue content); optional cue identifier line before the time-arrow line.
6. Remaining lines after the time-arrow = cue text (joined with `\n`, multi-line preserved).
7. `parse_srt_with_skip_count`/`parse_vtt_with_skip_count` return `(cues, skipped_block_count)`; plain `parse_srt`/`parse_vtt` discard the count.

**Serialize:** trivial join — `f"{index}\n{start} --> {end}\n{text}"` blocks separated by blank lines, VTT prepends a `WEBVTT` header line.

**Timestamp helpers** (only used for QA math and ASR-output generation, never for round-tripping existing cues):
```python
def timestamp_to_seconds(ts: str) -> float:   # handles both "," and "." decimal separators, HH:MM:SS or MM:SS
def seconds_to_srt_timestamp(seconds: float) -> str:  # -> "HH:MM:SS,mmm", clamps negative to 0
```

### 4.3 Translation chunking + LLM prompting + reassembly
Core file: `transcript_translation_service.py`.

**Chunk size:** 35 cues/LLM call (constant, not configurable per-request).

**Prompt template** (built per chunk):
```
Translate the following {N} numbered subtitle lines from {source_lang} to {target_lang}.
Rules:
- Preserve the meaning, context, and tone of the source — this is NOT a literal word-by-word translation. Make it read naturally in the target language.
- Keep the SAME NUMBER of lines as the input — one translation per input line, in the same order.
- Do NOT merge, split, skip, or reorder lines.
- Each translation MUST be a SINGLE line with no embedded line breaks, even if the source line looks like it could be split.
- Reply in EXACTLY this format, one line per translation, nothing else:
N: <translated text>

Input:
1: <flattened cue 1 text>
2: <flattened cue 2 text>
...
```
On a **strict retry** (2nd attempt after a failed parse), an extra paragraph is appended:
```
STRICT MODE: your previous reply did not match the required line count. You MUST output
exactly {N} lines, each starting with its number and a colon, and NOTHING else (no preamble,
no explanation, no extra blank lines).
```
Each cue's text is **flattened** before going into the prompt (`" ".join(text.split())` — collapses internal newlines to single spaces) specifically so every prompt line and every expected response line is unambiguous 1-cue-1-line — this is what makes strict response parsing reliable (see next).

**Response parsing** (`_parse_chunk_response`):
- Regex per line: `^\s*(\d+)\s*:\s*(.*)$`.
- Non-matching lines are **ignored, never merged into the previous entry** — this is a deliberate fix for a real bug: if a cue's own translated dialogue happens to start with something like "12: Attack now", treating it as a continuation of the prior line would silently misattribute text while still passing a naive line-count check.
- Any duplicate index within one response → parse fails outright (ambiguous, don't guess).
- Success requires `len(parsed) == expected_count` AND `set(parsed.keys()) == {1..expected_count}` exactly.
- On parse failure: retry once with strict mode. If that also fails → raise `TranslationAlignmentError` (never emit a partial/misaligned result silently).

**Reassembly:** translated strings are re-zipped 1:1 back onto the original `Cue.index/start/end` (never regenerated), producing new `Cue` objects only differing in `.text`.

**Source language detection** (`detect_source_language`): single bounded call over the first 15 cues' concatenated text (max 2000 chars sent), prompt: *"Identify the language of the following subtitle text. Reply with ONLY the language name in English (e.g. 'Vietnamese', 'English', 'Japanese'), nothing else."* — `max_output_tokens=16`. Falls back to `"Unknown"` on any failure (never crashes the job).

**Target language list** (fixed, both frontend + backend must match exactly):
```
vi: Vietnamese, en: English, ja: Japanese, ko: Korean, zh: Chinese,
fr: French, de: German, es: Spanish, th: Thai, id: Indonesian
```

### 4.4 LLM provider abstraction (`llm_client.py`)
Single function `call_llm(prompt: str, max_output_tokens: int = 2048) -> str | None`:
1. Try **Gemini** (`gemini-1.5-flash` via `google.generativeai`), if `GEMINI_API_KEY` set. `generation_config={"max_output_tokens": N, "thinking_budget": 0}` — **`thinking_budget: 0` is load-bearing**: without it, Gemini's thinking tokens eat into the output token budget and can silently truncate/empty the response (documented gotcha from this project's history).
2. On any exception, or if Gemini key absent, or empty response → fall back to **OpenAI** (`gpt-4o-mini` via `openai` SDK, `OPENAI_API_KEY`).
3. Returns `None` if both unavailable/fail — never raises. Callers must treat `None` as a soft-fail.

### 4.5 Chunk-level checkpoint + resume (Celery task idempotency)
Problem solved: a Celery worker can die mid-translation (OOM, deploy, crash); with `acks_late=True` the task gets redelivered, and without checkpointing it would restart from cue 0, re-billing every already-translated chunk.

Mechanism:
- Append-only on-disk file per job: `{job_dir}/progress.ndjson`, one JSON line per completed chunk: `{"chunk_index": int, "translations": [str, ...]}`. Append-only (not a growing DB JSONB blob) so each chunk write is O(1).
- DB column `resume_chunk_index` tracks how many chunks are confirmed done; updated **after** the checkpoint file write for that chunk, so a crash between the two still has the chunk recoverable from disk on next read.
- On task start: if `resume_chunk_index > 0`, reconstruct `translated_texts` for chunks `[0, resume_chunk_index)` by reading the checkpoint file. If the file is missing/unreadable/doesn't cover every expected chunk index → **never trust a partial reconstruction**, fall back to restarting from chunk 0 entirely.
- Loop then continues from `resume_chunk_index * chunk_size` instead of 0.
- Even without a usable checkpoint, the **cross-job translation cache** (§4.6) still avoids most re-billing on a full restart — the checkpoint just makes the common case free of even the cache DB round-trips.

### 4.6 Cross-job translation cache (dedup)
Within each chunk, before calling the LLM:
1. Compute `sha256(normalized_text + "\x1f" + target_lang)` per cue (`\x1f` unit separator chosen specifically because subtitle text can contain any printable character including `:`).
2. Batch-lookup all unique hashes in `transcript_translation_cache`.
3. For hashes NOT found, dedupe again **within the same chunk** (e.g. repeated `"[Music]"` filler lines) — only the first occurrence of each new hash actually goes to `translate_cues()`.
4. After translating, batch-upsert the fresh `(hash, target_lang, translated_text)` triples back into the cache table (`on_conflict="content_hash"`).
5. Final per-cue translation = merge of cache hits + freshly-translated results, re-expanded back to the original per-cue order (duplicates included).
Cache lookup/store failures are logged and swallowed — **never block or corrupt the actual translation**, purely a cost optimization.

### 4.7 Reading-speed QA (post-translation, informational only)
`subtitle_qa.check_reading_speed(cues) -> list[ReadingSpeedWarning]`. Thresholds (industry subtitle-style-guide norms, Netflix/BBC-ish):
- `_MAX_CPS = 20.0` characters/second
- `_MAX_CHARS = 42` characters per cue (cues are single-line post-flatten, so this is just total char count)

Per cue: skip if text is blank or duration is unparseable/≤0 (never flag a false positive from bad data). `cps = char_count / (end_sec - start_sec)`. Flag if `cps > 20` (`"too_fast"`) or `char_count > 42` (`"too_long"`) or both (`"both"`). Never blocks the job — just a count surfaced to the user (`reading_speed_warning_count`), recomputed live whenever the user manually edits cues via the editor.

### 4.8 Bounded self-requeue on transient failure (Celery)
Two exception types treated as **retryable**, not permanent failure:
- `SoftTimeLimitExceeded` — job legitimately needs more wall-clock than one run's soft limit (a max-size job = ~4000 cues / 35 per chunk ≈ 115 chunks × up to 2 LLM calls each ≈ up to 230 sequential calls; `soft_time_limit=1500s / time_limit=1680s` for the translate task, `300s/360s` for ASR).
- `TranslationAlignmentError` — one chunk's malformed LLM response; often succeeds fresh.

On these: bump `timeout_retry_count`, set `status='queued'`, re-`apply_async` the SAME job_id (so resume logic in §4.5 kicks in), bounded to `_MAX_TRANSIENT_RETRIES = 3` total. On any other exception, or once retries exhausted: `status='failed'`, `error_message` set (truncated to 2000 chars), on-disk job dir cleaned up immediately (don't wait for the hourly expiry sweep — a burst of failing jobs shouldn't fill disk).

### 4.9 ASR pipeline (Whisper)
1. `POST /transcript-asr/jobs` — `ffprobe` duration check (≤45min), quota reserve, insert row, enqueue.
2. Task `transcribe_video_task`:
   - `status='extracting_audio'` (progress 20%) → FFmpeg extracts a **mono** track at **48kbps** (`libmp3lame`), capped at 45 min duration by construction: `45*60*48000/8 ≈ 16.2MB`, comfortably under Whisper's **25MB hard upload limit** — but the actual output size is still double-checked post-extraction (VBR/encoding overhead could theoretically exceed the budget on unusual input) and fails clean with a clear message if so, rather than surfacing an opaque Whisper API error.
   - `status='transcribing'` (progress 50%) → `openai.audio.transcriptions.create(model="whisper-1", response_format="verbose_json", timestamp_granularities=["segment"])`.
   - Segments with blank/whitespace-only text are dropped; surviving segments are **renumbered sequentially from 1** (not preserving original Whisper segment indices — these are brand-new cues, no external reference depends on the original numbering, unlike the translate-existing-file feature where original `index` must be preserved).
   - `Cue.start/end` built via `seconds_to_srt_timestamp(seg.start/end)`.
   - Serialize to `.srt` (SRT only — ASR output is never VTT), `status='done'`.
   - The extracted scratch audio file is always deleted in a `finally` block (success or failure) — but the **source video is never touched**, it belongs to the download feature's own file lifecycle.
3. `POST /jobs/{id}/translate` chains an ASR result straight into a new Transcript Translate job (re-validates against that feature's own limits/quota — this is NOT a shared quota pool, ASR quota is minutes-based, translate quota is cues-based, fully separate).

### 4.10 Burn-into-video (FFmpeg hardsub)
Reuses generic helpers already in `app/api/processing.py` rather than reimplementing:
- `_guard_local_path(path)`: resolves + confirms path is inside the server's downloads dir (path-traversal guard), 404 if missing.
- `_burn_subtitle(video_path, subtitle_path, output_path) -> bool`:
  ```python
  ffmpeg -y -i {video_path} \
    -vf "subtitles={escaped_subtitle_path}:force_style='FontSize=20,PrimaryColour=&H00FFFFFF&,OutlineColour=&H00000000&,Outline=1'" \
    -c:a copy -c:v libx264 -crf 23 -preset fast \
    {output_path}
  ```
  (subtitle path has `\` → `/` and `:` → `\:` escaped for the ffmpeg filter-graph syntax). White text, black outline, 20pt, `crf 23`/`fast` preset. Timeout 300s. Returns `False` on any non-zero exit or missing output.
- `_schedule_cleanup(output_path)`: Celery countdown task deletes the burned output after 20 minutes.
- `_success_response(...)`: builds the download URL (`/api/v1/download-local?filepath=...&filename=...`), reports file size, `expires_in_seconds: 1200`.

### 4.11 Cue editor (manual post-translation fixes)
Purely a **text-only** patch mechanism — deliberately narrow scope to avoid corrupting timing/identity:
- `GET /jobs/{id}/cues` loads+parses the current `result_path` fresh (not from any DB cache) into `{index, start, end, text}` tuples.
- Frontend tracks a `Set` of dirty indices locally; `PUT` only sends the changed subset `{index, text}`.
- Backend merges edits onto the freshly-reparsed cue list by index (unknown indices ignored — the file may have changed between load and save), re-serializes the WHOLE file (all cues, edited + unedited) back to `result_path`, and recomputes the reading-speed warning count in the same request (so the UI reflects it without waiting for the next poll).

---

## 5. Celery / Queue Architecture
- No dedicated queue for this feature — both `translate_transcript_task` and `transcribe_video_task` are declared with `queue="analysis"`, deliberately reusing the existing `analysis` queue/worker rather than inventing a new queue name that no `docker-compose.yml` worker would actually consume.
- Both tasks: `acks_late=True` (safe redelivery on worker crash — combined with the resume/idempotency logic in §4.5/§4.8).
- Translate task: `soft_time_limit=1500, time_limit=1680`.
- ASR task: `soft_time_limit=300, time_limit=360`.
- Two Celery Beat tasks (run on a schedule, not user-triggered): `expire_transcript_translation_jobs_task` and `expire_transcript_asr_jobs_task` — delete rows + files where `expires_at < NOW()` (default 7-day TTL from job creation).

---

## 6. Frontend UX Flow

### 6.1 `/transcript-translate` (`TranscriptTranslatePage.jsx`)
1. Dropzone (drag-drop or click-to-browse) accepting multiple `.srt`/`.vtt` files; client-side extension filter before adding to the pending list.
2. A single `<select>` for target language (10 options, Vietnamese-labeled), shared across the whole batch.
3. "Bắt đầu dịch" button — disabled until ≥1 file + a target language chosen; a `useRef` double-submit guard prevents a fast double-click from firing two uploads (state alone can lag a render behind a click).
4. On submit: `multipart/form-data` POST, new jobs prepended to the local job list immediately, any `rejected` files shown in a dismissible banner with per-file reasons.
5. Job list polls `GET /jobs` every 5s **only while ≥1 job is in a non-terminal status** (`queued`/`detecting`/`translating`) — stops polling once everything is `done`/`failed`.
6. Each job card shows: filename, `{source_lang label} → {target_lang label}` (source shows "Đang phát hiện..." until detected), status badge (spinner for active states), progress bar + `"Đã dịch N/M dòng"`, any error message, a warning line if `skipped_block_count > 0` (malformed source blocks), a warning line if `reading_speed_warning_count > 0`.
7. Per-job actions (icon buttons, all except delete disabled until `status === 'done'`): **Edit** (pencil) opens the cue editor modal, **Download**, **Burn into video** (film icon) opens a picker modal listing the user's download history (only entries with `local_file_path` still set), **Delete**.
8. Cue editor modal: loads all cues via `GET /cues`, renders one row per cue (index + start timestamp shown as read-only label, a `<textarea>` for the text, auto-growing), tracks dirty indices via local state, "Lưu thay đổi" button disabled until something's dirty, sends only the changed subset on save.
9. Burn picker modal: on open, fetches `GET /api/v1/history?limit=10&status=success`, filters to entries with a `local_file_path`; clicking one POSTs to `burn-into-video`; on success shows a download link for the burned video.
10. Cross-link at the top: "Chưa có file phụ đề? Tự tạo bằng AI từ video đã tải →" → `/transcript-asr` (client-side `pushState`/`popstate` navigation, no full reload).

### 6.2 `/transcript-asr` (`TranscriptAsrPage.jsx`)
1. Single "Chọn video đã tải để tạo phụ đề" button opens a picker modal (same `GET /history` pattern as the burn picker).
2. Picking a video POSTs `/transcript-asr/jobs`, prepends the new job, closes the picker.
3. Job list, same 5s-conditional-poll pattern; per-job shows title, duration + detected language (or "Chưa xác định ngôn ngữ" while pending), status badge, progress bar.
4. Per-job actions: **Translate** (languages icon) opens a small target-language picker modal → POSTs `/jobs/{id}/translate` → on success, hard-redirects (`window.location.href`) to `/transcript-translate` where the new chained job now lives. **Download**, **Delete**.
5. Cross-link back to `/transcript-translate` for users who already have a subtitle file.

### 6.3 Shared frontend conventions
- Both pages: identical `getSessionId()`/`authHeaders()` helpers (stable `vg_session_id` in `localStorage`, sent as `X-Session-ID` header alongside `Authorization: Bearer <token>` when logged in).
- Both pages: routed in `App.jsx` at `/transcript-translate` and `/transcript-asr`; entry points live in the account menu (`AccountMenu.jsx`) only — **not** in the mobile bottom tab bar (`MobileTabBar.jsx`), matching this app's existing pattern of keeping secondary/power-user features out of primary mobile nav.
- Color/style system: dark theme (`#0d2320` card background), amber/orange gradient accent (`#FBBF24` → `#FB923C`) for primary actions, `lucide-react` icon set, Tailwind utility classes throughout.

---

## 7. Config / Environment Variables

| Var | Purpose | Required for |
|---|---|---|
| `GEMINI_API_KEY` | Primary LLM provider (translation + language detection) | Transcript Translate |
| `OPENAI_API_KEY` | Fallback LLM provider (translation) AND the only ASR provider (Whisper) | Both — hard-required for ASR (no fallback), fallback-only for Translate |
| `TRANSCRIPT_TRANSLATE_DAILY_CUE_LIMIT` | Daily per-identity cue quota, default `6000` | Transcript Translate |
| `TRANSCRIPT_ASR_DAILY_MINUTES_LIMIT` | Daily per-identity audio-minutes quota, default `120` | Transcript ASR |

External binaries required on the host/container: `ffmpeg`, `ffprobe` (audio extraction, duration probing, subtitle burning).

Python packages: `openai>=1.40.0` (must be an actual installed dependency, not just imported — this was a real bug found during original build: imported by `llm_client.py` but missing from `requirements.txt`, silently breaking the OpenAI fallback), `google-generativeai` (Gemini SDK).

---

## 8. Constants Reference (exact values, keep in sync frontend/backend)

```
ALLOWED_EXT              = {.srt, .vtt}
MAX_UPLOAD_MB             = 2
MAX_FILES_PER_BATCH       = 10
MAX_CUES_PER_JOB          = 4000
MAX_CUE_TEXT_LENGTH       = 2000
TARGET_LANGS              = vi,en,ja,ko,zh,fr,de,es,th,id  (10 fixed languages)
TRANSLATE_CHUNK_SIZE      = 35 cues/LLM call
TRANSLATE_MAX_RETRIES     = 2 attempts per chunk (1 normal + 1 strict)
DAILY_CUE_LIMIT           = 6000 (env-overridable; must stay > MAX_CUES_PER_JOB)
JOB_EXPIRY                = 7 days from creation
DETECT_LANG_SAMPLE_CUES   = first 15 cues, max 2000 chars sent to LLM

ASR_MAX_DURATION_MINUTES  = 45
ASR_DAILY_MINUTES_LIMIT   = 120 (env-overridable)
ASR_AUDIO_BITRATE_KBPS    = 48 (mono)
WHISPER_MAX_UPLOAD_BYTES  = 25 * 1024 * 1024

READING_SPEED_MAX_CPS     = 20.0 chars/sec
READING_SPEED_MAX_CHARS   = 42 chars/cue

BURN_SUBTITLE_STYLE       = FontSize=20, white text, black 1px outline
BURN_CLEANUP_COUNTDOWN    = 20 minutes
```

---

## 9. Known Bugs Found+Fixed During Original Build (context only, already resolved in this spec's described behavior — don't reintroduce)

1. **IDOR via broken auth import**: an earlier `resolve_identity` imported a nonexistent auth helper, silently bucketing every authenticated user under `user_id IS NULL` — a classic "looks fine, logs no error, just wrong" bug. Root cause: always resolve real auth via the actual working `get_optional_user` dependency, never assume an import succeeded without an explicit test hitting it with a real token.
2. **Response-line merging bug** (§4.3): an earlier parser merged non-numbered trailing lines into the previous cue's translation as a "continuation" — this silently broke when a cue's *translated* text itself happened to start with `"<number>: "` (plausible in dialogue). Fixed by flattening every cue to single-line before prompting, so every response line is unambiguous, and treating any non-matching line as noise to discard, never merge.
3. **Checkpoint deleted on retryable failure**: an earlier version's generic job-failure cleanup deleted the on-disk resume checkpoint even on transient errors, defeating the entire point of chunk-level resume. Fixed by only cleaning up the job directory once retries are genuinely exhausted (§4.8), not on every exception.
4. **ASR cue reindexing bug**: filtering blank Whisper segments via a list comprehension `if` clause while still using `enumerate()` on the *original* list preserved gappy original positions instead of a clean sequential 1,2,3,... Fixed by enumerating the already-filtered list.
5. **Missing dependency, silent fallback breakage**: `openai>=1.40.0` was imported by `llm_client.py` but never actually added to `requirements.txt` — the OpenAI fallback path was silently broken (ImportError caught and swallowed by the try/except) for an extended period without any visible error. Always verify a new import is a real declared dependency, not just "it worked in dev because it happened to be installed transitively."
6. **Gemini `thinking_budget` gotcha**: without explicitly setting `thinking_budget: 0` in `generation_config`, Gemini's internal "thinking" tokens can consume the entire `max_output_tokens` budget, returning an empty/truncated response with no error — always set this explicitly for short, deterministic-format responses like the numbered-line translation format used here.

---

## 10. Suggested Build Order (for a from-scratch rebuild)

1. `subtitle_format.py` (parser/serializer) — fully standalone, no external deps, write tests against real-world `.srt`/`.vtt` samples including BOM, VTT NOTE blocks, multi-line cues.
2. DB schema (§3) — all 4 tables + 3 RPC functions, in one migration.
3. `llm_client.py` (provider abstraction) — get Gemini+OpenAI fallback working standalone before touching subtitle logic.
4. `transcript_translation_service.py` (chunking/prompting/parsing) — unit-testable without Celery/DB, mock `call_llm`.
5. `transcript_translation_cache.py` + `subtitle_qa.py` — independent, straightforward.
6. Celery task (`transcript_translation_tasks.py`) wiring it all together — resume/retry logic (§4.5, §4.8) is the highest-risk part, write tests that simulate a mid-run crash and confirm resume produces the exact same output as an uninterrupted run.
7. API layer (`transcript_translate.py`) — upload validation chain, quota reservation, job CRUD.
8. Frontend page + polling + editor/burn modals.
9. Repeat 6-8 for ASR (`asr_service.py`, `transcript_asr_tasks.py`, `transcript_asr.py`, `TranscriptAsrPage.jsx`) — reuses most of translate's infrastructure (Cue type, identity resolution, expiry pattern), genuinely new surface is just the Whisper call + audio extraction.
10. Wire the ASR→Translate chain endpoint (`POST /transcript-asr/jobs/{id}/translate`) last, once both features independently work.

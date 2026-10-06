# 09 — Managed providers for Kuaishou and Xiaohongshu (Phase 32B-3)

Owner decisions (2026-10-06): single public video only (profiles/channels later); free route first, Apify allowed as
fallback on the **same Apify account** ($5/month in total), each platform with its own daily cap. Due 2026-10-09.

Everything below was read on **2026-10-06** from the public Apify API, no token needed:

- actor info, pricing, run stats: `GET https://api.apify.com/v2/acts/<user>~<actor>`
- README, input schema, dataset schema: `GET https://api.apify.com/v2/acts/<user>~<actor>/builds/default`
  (`data.actorDefinition.readme`, `.input`, `.storages.dataset`)
- store search: `GET https://api.apify.com/v2/store?search=<term>` (terms: kuaishou, kuaishou video, kuaishou downloader,
  kwai downloader, xiaohongshu, xiaohongshu downloader, rednote, rednote video, rednote downloader, xhs)

No actor was run. Every number in this file is the vendor's listing, not our measurement.

## How Apify bills these actors (matters for the estimates)

All candidates are pay-per-event (PPE). Two events matter:
- the actor's own per-result event (`apify-default-dataset-item` or a named one), priced flat or by plan tier
  (`FREE`, `BRONZE`, … — the owner's account is assumed to be on the Free plan, so the `FREE` tier price is used);
- `apify-actor-start`, charged **once per GB of run memory** (minimum one). `ApifyProvider` does not set memory, so the
  actor's default memory applies (`defaultRunOptions.memoryMbytes`).

The run's `usageTotalUsd` does not include the per-result charge (measured on Douyin, 2026-10-06), so budget_guard
settles a run at no less than the estimate (`CHINA_ACCESS_COST_FLOOR_AT_ESTIMATE=true`, commit f68a3e3). That makes
the **estimate the effective price** in our counters: it must be right.

## Kuaishou — chosen: `natanielsantos~kuaishou-scraper`

| | |
|---|---|
| Actor | `natanielsantos~kuaishou-scraper` (id `IEe8zzdPqUvDhZu6e`), same vendor as the Douyin actor in production |
| Pages | https://apify.com/natanielsantos/kuaishou-scraper · https://api.apify.com/v2/acts/natanielsantos~kuaishou-scraper |
| Pricing | PPE. `apify-default-dataset-item` ("video") **$0.004 flat** (not tiered); `comment` $0.0015; `apify-actor-start` $0.00005 per GB |
| Default memory | 256 MB → 1 start event |
| **Estimate per call** | **$0.004 + $0.00005 = $0.00405** (`CHINA_ACCESS_APIFY_KUAISHOU_EST_COST_USD`) |
| Last modified | 2026-09-12 (build 0.0.26); last public run 2026-10-06 |
| Runs, last 30 days (all users) | 1746: 1624 SUCCEEDED, **122 TIMED-OUT (7 %)**, 0 FAILED, 0 ABORTED; 4311 runs in total; 8 users / 30 days |
| Input | `startUrls: [string]` — README lists `kuaishou.com/short-video/<id>`, `kuaishou.com/f/<code>`, `v.m.chenzhongtech.com/fw/photo/<id>` (and profile/search/hashtag URLs, not used). We send `{"startUrls": [url], "maxCommentsPerVideo": 0, "maxRepliesPerComment": 0}`; the run-level `maxItems=1` caps the dataset |
| Output (README "Video Output Example") | `id`, `text` (caption → title), `duration` (`332` in the example, read as seconds), `thumb`, `cover`, `width`, `height`, `playUrl` (video URL), `allPlayUrls[{url, qualityType, videoCodec, avgBitrate}]`, `authorMeta.name` (author), `musicMeta.audioUrl` |

Why this one:
- Same vendor and output style as `natanielsantos~douyin-scraper`, which already runs in production through this layer.
- Lowest documented per-video price among actors that a Free-plan account can use, with a documented `playUrl`.
- Accepts the share forms users paste (`/f/`, chenzhongtech `/fw/photo/`).

What the README does not say (parser is defensive): no `error` field is documented (honoured if present); `duration`
unit is inferred from the example (a value above 6 h is treated as milliseconds); no statement on `playUrl` expiry,
Referer or IP binding. `musicMeta.audioUrl` is **not** used as the mp3 source: it is the music entry of the post, not
guaranteed to be the video's own sound. `v.kuaishou.com/<code>` is not a documented input: the adapter first reads its
302 (free, one request, not followed) — measured from the workspace: answered in 0.3 s and pointed to a
`*.m.chenzhongtech.com/fw/photo/<id>` URL, which is then sent in the README form `v.m.chenzhongtech.com/fw/photo/<id>`.
If the lookup fails, the share link itself is sent.

Considered and not chosen:

| Actor | Price (FREE tier) | 30-day runs | Why not |
|---|---|---|---|
| `zen-studio~kuaishou-scraper` | $0.00799/video, 1 GB default | 47/48 ok | ~2x the price |
| `stackrelay~kuaishou-scraper` | $0.006/video + $0.001 start/GB × 4 GB default | 31/31 ok | ~2.5x the price, low usage (2 users) |
| `hgservices~Kuaishou-Video-Scraper` | $0.002/result, but `isPPEPlatformUsagePaidByUser: true` (we would also pay compute, 4 GB default) | 24/24 ok | open-ended compute cost; 4 users |
| `socialdatax~socialdatax-kuaishou-data-api` | $0.002/item | 2761/2878 ok | README: "Ongoing use requires an Apify paid plan. Free-plan users get a 5-request … trial" |
| `sian.agency~kwai-kuaishou-scraper` | actor start **$0.12** on the FREE tier | 439/439 ok | start fee alone is 30x our estimate |

## Xiaohongshu — chosen: `blue_puppy~rednote-video-downloader`

| | |
|---|---|
| Actor | `blue_puppy~rednote-video-downloader` (id `ddOEoUiTdH9FbDDzq`) |
| Pages | https://apify.com/blue_puppy/rednote-video-downloader · https://api.apify.com/v2/acts/blue_puppy~rednote-video-downloader |
| Pricing | PPE. `apify-default-dataset-item` **$0.0025 on FREE** ($0.00217 Bronze, $0.00183 Silver, $0.0015 Gold+); `apify-actor-start` $0.00005 per GB |
| Default memory | 512 MB → 1 start event |
| **Estimate per call** | **$0.0025 + $0.00005 = $0.00255** (`CHINA_ACCESS_APIFY_XIAOHONGSHU_EST_COST_USD`) |
| Last modified | 2026-07-17 (created 2026-07-14); last public run 2026-10-05 |
| Runs, last 30 days | 266: 265 SUCCEEDED, 1 ABORTED; 414 runs in total; 3 users / 30 days |
| Input | `urls: [{url}]`, `maxUrls`. Documented URL forms: `xiaohongshu.com/explore/<id>?xsec_token=…`, `/discovery/item/<id>`, `xhslink.com` short links (and rednote.com). We send `{"urls": [{"url": url}], "maxUrls": 1}` |
| Output (README "Output" + dataset schema) | one row per URL: `status` (`ok`/`error`), `downloadUrl` (`sns-video-bd.xhscdn.com` video URL), `id`, `title`, `description`, `type` (always `video` when ok), `authorName`, `error {code, message}` with codes `invalid_url`, `fetch_failed`, `parse_failed`, `not_video` |

Why this one:
- A single-video downloader: one row per URL with the CDN URL, which is all this phase needs.
- Lowest price of the maintained candidates a Free-plan account can use; 99.6 % of last-30-day runs succeeded.

Risks / gaps (parser is defensive):
- **No duration and no cover/thumbnail** are documented. The result has `duration_sec = None` (allowed unless
  `CHINA_ACCESS_APIFY_REQUIRE_DURATION=true`) and no thumbnail.
- **Error rows are dataset items, so they are billed** ($0.0025). When the native extractor can see that a note is
  image-only (its cookie web-API path), it fails with `unsupported_url`, which is not fallback-eligible, so the actor
  is not called. When native cannot tell (no XHS cookie in the pool: yt-dlp finds no video and the API path needs the
  cookie), the note goes to the actor and costs one `not_video` row. Keeping a fresh `xiaohongshu` cookie in the pool
  avoids most of those.
- Young actor with few users: if it breaks, swap by env (below).
- README: "CDN links may expire". Expiry, Referer and IP binding are not documented.

Documented alternative (the parser already accepts its row shape, so switching is env-only:
`CHINA_ACCESS_APIFY_XIAOHONGSHU_ACTOR_ID=agentflow~xiaohongshu-video-downloader`,
`CHINA_ACCESS_APIFY_XIAOHONGSHU_EST_COST_USD=0.007`): https://apify.com/agentflow/xiaohongshu-video-downloader —
$0.007 per **successful** resolve only (failed/expired links free), 50/50 runs ok, last modified 2026-07-01; output
`success`, `type`, `title`, `desc`, `download_url`. Its input is `urls: [string]` (not `[{url}]`); the adapter picks
that form when the actor id starts with `agentflow~`. Default memory 4 GB, but it has no start event, so only the
per-success price applies.

Considered and not chosen:

| Actor | Price (FREE tier) | Why not |
|---|---|---|
| `zen-studio~rednote-note-detail-scraper` | $0.00999/note + **$0.05 actor start** (≈ $0.06/call) | 24x the price; README: Free plan = "15 lifetime preview runs" |
| `socialdatax~socialdatax-xhs-data-api` | $0.00499/item | README: paid Apify plan required (5-request trial on Free) |
| `zhorex~rednote-xiaohongshu-scraper` | $0.06/post + $0.15 `video-extracted` + $0.0125 start/GB | far over budget |
| `sian.agency~xiaohongshu-rednote-scraper` | $0.14 start + $0.15/note detail | far over budget |
| `atomus~xiaohongshu-scraper`, `vulnv~xiaohongshu-scraper` | $0.04 per note detail (vulnv: $0.10 minimum charge) | 16x the price |
| `easyapi~rednote-xiaohongshu-video-downloader` | $0.00299/result + **$0.09 start per GB × 2 GB**, compute paid by user | start fee |
| `actorzlab~rednote-scraper` | $0.003/record | output example shows `videoUrl: null`; last build 2026-05-24 |
| `dltik~rednote-xiaohongshu-scraper` | $0.005/result | README offers image URLs only, no video URL documented |

## Budget with the shared $5/month Apify account

| Route | Estimate/call | Default platform cap/day | Max/day at the cap |
|---|---|---|---|
| `apify_douyin` | $0.0071 | 50 calls, $1.00 | $1.00 |
| `apify_kuaishou` | $0.00405 | 20 calls, $0.10 | $0.081 |
| `apify_xiaohongshu` | $0.00255 | 20 calls, $0.10 | $0.051 |

All three also count against the shared provider ceilings (`CHINA_ACCESS_APIFY_DAILY_CALL_LIMIT` 50,
`..._DAILY_SPEND_CEILING_USD` 1.00, `..._MONTHLY_SPEND_CEILING_USD` 5.00). The Apify account's own spending limit is
the hard stop outside this code.

## What still needs a live check (owner)

1. 5–10 public Kuaishou video links (at least one `v.kuaishou.com` share link and one `kuaishou.com/f/` link) and
   5–10 public Xiaohongshu **video** notes (at least two `xhslink.com` share links copied from the app, one full
   `explore/<id>?xsec_token=…` URL), plus one XHS image note to confirm it costs nothing.
2. For each: does the media URL download from our server with the Referer we send (`https://www.kuaishou.com/`,
   `https://www.xiaohongshu.com/`), from the user's browser without it, and for how long does it stay valid?
3. The actual Apify charge per run in the Apify console vs the estimates above.

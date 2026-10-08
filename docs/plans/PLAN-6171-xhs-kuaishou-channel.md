# PLAN-6171 — Whole-channel download for Xiaohongshu and Kuaishou

Task #6171. Same flow as the Douyin channel scan (task #6055): one budgeted Apify run per profile, the listed
videos become ordinary per-video jobs, and each listed video that already carries a media URL is cached so its
job does not pay again.

Everything below was read on **2026-10-08** from the public Apify API (no token):
`GET https://api.apify.com/v2/acts/<actor>` (pricing, run stats, default memory) and
`GET https://api.apify.com/v2/acts/<actor>/builds/default` (README, input schema, dataset schema).
**No actor was run.** Every number below is the vendor's listing, not our measurement.

## Owner rules (2026-10-08) and where they are enforced

| Rule | Where |
|---|---|
| Signed-in users only | `POST /bulk-download`: 401 when every link is a XHS/Kuaishou channel, a failed row per link in a mixed batch (also after a share link resolves to a profile). Worker: `profile_listing.list_profile` refuses `login_required` (covers scheduled jobs and anything else that reaches the scan). |
| ≤ 20 videos per scan | `settings.china_channel_max()` — env may lower it, never raise it past 20; admins are capped at 20 too. A smaller `max_videos` is kept. |
| Each video counts 1 against the daily quota | Unchanged path: each listed video is a normal `process_video_task` job that counts on success. |
| Scan at most what is left today | `listing_cap()` = min(request, 20, `BatchAllowance.remaining(platform)`) — the same rule Douyin uses (`channel_listing.listing_cap`). 0 left → refused before any paid call. |
| Paid calls inside the existing ceilings | Same guards as Douyin: master/platform/channel flags, kill switches, managed mode, Apify token pool (`apify_pool.pick` with the run's estimate), `budget_guard.precheck` + `reserve` of the whole estimate, settle on the run's cost (floored at the estimate). |
| Free path first | Kuaishou: none. XHS: a dormant one exists, wired behind a flag (OFF) — see below. |

## Is there a free path? (evidence)

- **yt-dlp 2026.08.19** (the version in the backend venv): only `XiaoHongShuIE` with
  `_VALID_URL = …xiaohongshu.com/(?:explore|discovery/item)/<id>` — notes only, no profile/user extractor; no
  Kuaishou extractor at all.
- **Kuaishou**: `www.kuaishou.com` times out from the workspace and from production (docs/china-access/09);
  `app/services/kuaishou_extractor.py` has no profile code.
- **Xiaohongshu**: `app/services/xiaohongshu_extractor.scrape_xiaohongshu_profile` (cookie pool, `user_posted`
  web API) exists but was never wired into the channel flow. It sends no `x-s`/`x-t` signature and builds
  `explore/<id>` links without `xsec_token`. The memo23 actor README (read today) says that since **September 2026**
  Xiaohongshu renders guest profile grids with blank note IDs and that note IDs need a logged-in, *signed* catalog
  call. So it is very likely broken. It is wired as an opt-in first try:
  `CHINA_ACCESS_XIAOHONGSHU_CHANNEL_FREE_FIRST=true` (default off). Turn it on only after one live check with a fresh
  XHS cookie in the pool.

## Actors chosen

### Kuaishou — `natanielsantos~kuaishou-scraper` (already used for single videos)

| | |
|---|---|
| Page | https://apify.com/natanielsantos/kuaishou-scraper |
| Pricing | PPE: `apify-default-dataset-item` ("video") **$0.004 flat**; `apify-actor-start` $0.00005/GB (256 MB default → 1 event); `comment` $0.0015 (kept at 0) |
| Estimate per scan | N × $0.004 + $0.00005 → 20 videos = **$0.08005** |
| Runs, last 30 days | 1893: 1795 SUCCEEDED, 97 TIMED-OUT (5 %), 1 ABORTED; last modified 2026-09-12; last run 2026-10-08 |
| Input (README + input schema) | `startUrls` lists `https://www.kuaishou.com/profile/1745294435` as a "Profile URL"; `profileSortBy` `latest`/`popular`. We send `{"startUrls": [profile], "profileSortBy": "latest", "maxItems": N, "maxCommentsPerVideo": 0, "maxRepliesPerComment": 0}` and the run-level `maxItems=N` (the schema says `maxItems` is "per search term or hashtag", so the run-level cap is the one that bounds a profile) |
| Output | the single-video row: `id`, `url` (`kuaishou.com/short-video/<id>`), `text`, `duration`, `thumb`, `cover`, `playUrl`, `allPlayUrls`, `authorMeta.name` |
| Reuse | rows go through the existing `adapters/kuaishou.parse_actor_item`; each playable row is cached under the canonical `short-video/<id>` URL, so the per-video job is a cache hit |

### Xiaohongshu — `vulnv~xiaohongshu-scraper`, operation `user_notes`

| | |
|---|---|
| Page | https://apify.com/vulnv/xiaohongshu-scraper |
| Pricing | PPE: `note` **$0.004** ("Cost per Xiaohongshu note returned (from search or a creator's notes)"); no start event; `minimalMaxTotalChargeUsd` **$0.10** (a run's max-charge must be ≥ $0.10 — a vendor-side cap only, our reservation stays the estimate) |
| Estimate per scan | N × $0.004 → 20 notes = **$0.08** |
| Runs, last 30 days | 3036: 3025 SUCCEEDED (99.6 %), 6 FAILED, 4 TIMED-OUT, 1 ABORTED; 59 users; last modified 2026-07-26; last run 2026-10-08 |
| Input (input schema) | `{"operation": "user_notes", "userUrls": [profile], "maxItems": N}`; `userUrls` accepts profile URLs, share links or IDs |
| Output (dataset schema) | `record_type` `note`, `note_id`, `note_type` (`video`/`image`), `note_url`, `xsec_token`, `title`, `desc`, `cover_url`, `video_url` ("Playable MP4 URL for video notes (signed, time-limited)"), `video_duration` (s), `author_nickname` |
| Reuse | each **video** note becomes `explore/<id>?xsec_token=…&xsec_source=pc_user` (the form the single-video route and `blue_puppy~rednote-video-downloader` take). With `video_url` present it is cached (cache hit); without it the job resolves through the normal single-video route (native first, then the existing $0.00255 actor) |

Caveat: image notes are billed too ($0.004 each) and are skipped, so a scan of N notes can yield fewer than N videos.
The README does not say that `video_url` is filled for `user_notes` rows (it lists it among "common note fields") —
not verified.

### Considered for Xiaohongshu, not chosen

| Actor | Price (FREE tier) | 30-day runs | Why not |
|---|---|---|---|
| `toolzerhub~rednote-xiaohongshu-profile-posts-scraper` | $0.006/row + $0.00005 × 4 (4 GB) | 526/565 ok | no `xsec_token`, no video URL (only with `addonPostDetails` at +$0.028/row) |
| `maximedupre~rednote-user-posts-scraper` | $0.00445/post | 728/728 ok, 2 users | README example has `postId: null` — profile cards without note IDs |
| `memo23~rednote-user-posts-scraper` | $0.005/result + $0.005 start | 147/152 ok | needs our own `web_session` cookie for note IDs (guest = preview rows without IDs) |
| `pro100chok~rednote-xiaohongshu-scraper` (`userNotes`) | $0.005/note + $0.01 per creator profile | 2557/2567 ok | dearer; good fallback if vulnv breaks (needs a parser) |
| `funny_ground~xiaohongshu-creator-notes-scraper` | $0.014/item + $0.0005 start, compute paid by user | 24/71 ok | price and 34 % success |
| `easyapi~rednote-xiaohongshu-user-posts-scraper` | $0.00499/item + **$0.09 start** | 81/88 ok | start fee |

## Terms of Service

- Official user agreements: https://agree.xiaohongshu.com/h5/terms/ZXXY20220331001/-1 (page is JS-rendered; the fetch
  returned no clause text) and https://www.kuaishou.com/about/policy?tab=protocol (connection reset from here). **Not
  read first-hand.** Secondary sources (e.g. developer.aliyun.com/article/1686422) say Kuaishou's agreement forbids
  crawlers/automated collection; the same is widely reported for Xiaohongshu. Assume both platforms forbid automated
  collection.
- The actors' own disclaimers put compliance on the caller (blue_puppy README: "You are responsible for complying
  with 小红书's Terms of Service"; memo23: "You are responsible for complying with … Xiaohongshu's terms").
- This is the same exposure as the single-video route already live (32B-3). **Owner decision needed** before turning
  the channel flags on: bulk listing of a creator's whole profile is a larger step than single videos.

## Flags (all default OFF / safe)

| Env | Default | Meaning |
|---|---|---|
| `CHINA_ACCESS_XIAOHONGSHU_CHANNEL_ENABLED` / `CHINA_ACCESS_KUAISHOU_CHANNEL_ENABLED` | `false` | turn the channel scan on (needs `CHINA_ACCESS_ENABLED`, `CHINA_ACCESS_<P>_ENABLED`, `CHINA_ACCESS_<P>_MANAGED_MODE=on` and a token in the Apify pool, as for single videos) |
| `CHINA_ACCESS_<P>_CHANNEL_MAX` | 20 | videos per scan; clamped to 1..20 |
| `CHINA_ACCESS_<P>_CHANNEL_TIMEOUT_SEC` | 150 | one profile run (30..280) |
| `CHINA_ACCESS_<P>_CHANNEL_LISTING_CACHE_SEC` | 1500 | reuse a scanned list for the same profile |
| `CHINA_ACCESS_APIFY_KUAISHOU_CHANNEL_ACTOR_ID` / `…_EST_COST_USD` | `natanielsantos~kuaishou-scraper` / 0.004 | per-video estimate (+$0.00005 start) |
| `CHINA_ACCESS_APIFY_XIAOHONGSHU_CHANNEL_ACTOR_ID` / `…_EST_COST_USD` | `vulnv~xiaohongshu-scraper` / 0.004 | per-note estimate |
| `CHINA_ACCESS_APIFY_XIAOHONGSHU_CHANNEL_MIN_MAX_CHARGE_USD` | 0.10 | the actor's `minimalMaxTotalChargeUsd` |
| `CHINA_ACCESS_XIAOHONGSHU_CHANNEL_FREE_FIRST` | `false` | try the cookie-pool scraper first |

**Spend ceiling note:** `CHINA_ACCESS_KUAISHOU/XIAOHONGSHU_MANAGED_DAILY_SPEND_CEILING_USD` default to **$0.10/day**
(docs/china-access/09). One full 20-video scan reserves ~$0.08, so with the defaults about **one scan per platform per
day** fits, and it also eats that day's single-video budget. Raise the ceiling deliberately if more is wanted; the
vendor-wide `CHINA_ACCESS_APIFY_DAILY/MONTHLY_SPEND_CEILING_USD` and each pool token's own ceiling still apply.

## Rollout

1. Owner decision on ToS (above).
2. Set the platform flags as for single videos, then `CHINA_ACCESS_KUAISHOU_CHANNEL_ENABLED=true` (canary: set
   `CHINA_ACCESS_KUAISHOU_MANAGED_MODE=canary_admin` first — the scan then only opens for admin sessions).
3. One live scan per platform with a small `max_videos` (3): check the actor input is accepted, the Apify charge vs
   the estimate, whether XHS rows carry `video_url`, and that the per-video jobs succeed.
4. Kill switch per platform (existing) closes the scan at once.

## Not in scope / known gaps

- A **video** link of a creator does not scan its author (Douyin does that); paste the profile link.
- Kuaishou `v.m.chenzhongtech.com/fw/user/<id>` is assumed to carry the same id as `kuaishou.com/profile/<id>`.
- Chrome extension: XHS/Kuaishou profile pages are detected as channels, but the content script is not injected on
  kuaishou.com (manifest change = new host permission, store review) — Kuaishou channels work from the web Channel tab.
- The Windows app's `/client/douyin/channel` route is Douyin-only; no app route was added.

# Kuaishou (快手) — scaffold, OFF, UNVERIFIED

## Current state (2026-10-05)

- **OFF by default.** `KUAISHOU_ENABLED` is unset in every environment. While it
  is off, Kuaishou links go through the generic yt-dlp path exactly as before and
  fail with `unsupported_url`. The extractor is not registered, `/api/v1/platforms`
  and `/api/v1/platforms/capabilities` do not list it, the classifier returns
  `unknown`, and there is no probe target, SEO page or platform chip.
- **UNVERIFIED.** None of the code has run against the real site. From the
  workspace and the production server (both outside mainland China)
  `www.kuaishou.com` never answers. yt-dlp got a read timeout. The capture tool
  got a connect timeout after 8 s on 2026-10-05. Every parsing strategy is built
  from public write-ups and tested only against **SYNTHETIC** pages in
  `backend/tests/test_kuaishou_extractor.py`.
- yt-dlp 2026.08.19 has no Kuaishou extractor.

## What is there

| Piece | File |
|---|---|
| Extractor (URL forms, network layer, 3 strategies, media checks) | `backend/app/services/kuaishou_extractor.py` |
| Downloader hook (flag-gated, before Douyin) | `backend/app/services/downloader.py` |
| Error codes → HTTP status | `backend/app/core/extraction_errors.py` (`_EXTRACTOR_CODE_STATUS`) |
| Error catalogue (Vietnamese text) | `backend/app/core/error_codes.py` |
| Flag-gated classifier, capability and short-link entries | `source_classifier.py`, `container_registry.py`, `url_normalizer.py` |
| Cookie cooldown (20 s, optional pool `kuaishou`) | `backend/app/core/cookie_pool.py` |
| Capture-and-verify tool | `backend/scripts/kuaishou_capture.py` |

URL forms: `kuaishou.com|cn/short-video/<id>`, `/video/<id>` (requested by the owner;
not found in any public source), `/f/<code>`, `v.kuaishou.com|cn/<code>`,
`kuaishou.*/fw/photo/<id>`, `*.chenzhongtech.com/fw/photo/<id>`,
`m.gifshow.com/fw/photo/<id>`.

Strategies, tried in order (all UNVERIFIED):

1. `apollo_state`: `window.__APOLLO_STATE__` → `defaultClient["VisionVideoDetailPhoto:<id>"]`
   → `photoUrl`, `caption`, `coverUrl`, `duration`. Source: [KS-Downloader extractor.py](https://github.com/JoeanAmier/KS-Downloader/blob/master/source/extract/extractor.py), [sharextract #24](https://github.com/wuaishare/sharextract/issues/24).
2. `init_state`: share page `window.INIT_STATE` → `photo` object → `mainMvUrls[].url`
   (video), or `ext_params.atlas.cdn[0] + list[]` (image album). Source: KS-Downloader
   (above), [aliyun article](https://developer.aliyun.com/article/921451).
3. `photo_url_regex`: the page contains exactly one `"photoUrl":"…"` string.
   Source: [lux kuaishou.go](https://github.com/iawia002/lux/blob/master/extractors/kuaishou/kuaishou.go).

Not implemented on purpose:

- **Multiple qualities.** sharextract #24 warns that the adaptive `manifest` contains
  audio-only and video-only tracks. A video-only track would download without sound,
  so only the single muxed `photoUrl`/`mainMvUrls` stream is offered.
- **Web GraphQL (`/graphql`, `visionVideoDetail`).** Public write-ups show it needs a
  `did` that the site issues, plus private parameters. We neither generate a `did` nor
  invent signatures. The only cookie handling copies lux: if the first response sets
  cookies, the page is fetched once more with exactly those cookies.
- **Profiles and batch.**

## How to enable and verify (later, with a Chinese network path)

1. Get a proxy that exits in mainland China. The URL format is the one
   `XIAOHONGSHU_PROXY_CN` uses: `http://user:pass@host:port` or `https://…`. A
   `socks5://` proxy also works, but only after `socksio` is installed.
2. On any machine, from `backend/`, run the tool against 3–5 different **public**
   videos (one `/short-video/`, one `v.kuaishou.com` share link, and an image album
   if possible):

   ```bash
   KUAISHOU_PROXY_CN='http://user:pass@cn-host:port' \
     python scripts/kuaishou_capture.py 'https://www.kuaishou.com/short-video/<id>'
   ```

   Each run prints PASS/FAIL per step and saves `kuaishou_capture_*.html`.
3. Review each capture by hand. Redaction removes cookies, tokens, user ids and query
   values, but it is pattern-based. Copy good captures to
   `backend/tests/fixtures/kuaishou/`. Add tests that parse them, and replace or adjust
   the SYNTHETIC fixtures wherever the real structure differs.
4. Download one returned media URL through the proxy and play it. Check that it has
   sound and that the CDN works without the proxy (or note that it does not). The
   tool does not check this.
5. Only after that, set both variables on the backend **and** the Celery workers, then
   restart:

   ```
   KUAISHOU_ENABLED=true
   KUAISHOU_PROXY_CN=http://user:pass@cn-host:port
   ```

6. Then try `/api/v1/fetch-link` with a public Kuaishou link. Update this file and
   remove "UNVERIFIED" from the code comments that a capture has confirmed.

To turn it off, unset `KUAISHOU_ENABLED` and restart. Nothing else changes.

## Errors users can see (flag ON)

| Code | HTTP | When |
|---|---|---|
| `kuaishou_geo_blocked` | 503 | connect/read timeout, budget spent, or HTTP 403/451. The message says Kuaishou only answers from inside China and the server needs a Chinese proxy, and that the link is not at fault |
| `kuaishou_proxy_error` | 503 | the configured proxy fails |
| `kuaishou_challenge_page` | 503 | HTTP 429, or the final URL looks like a captcha/verify page |
| `kuaishou_upstream_unreachable` | 503 | other network errors or non-200 statuses |
| `kuaishou_parse_failed` | 502 | the page loaded but no strategy matched (likely a site change) |
| `kuaishou_media_url_rejected` / `kuaishou_unsafe_address` | 502 | the media or page address is not https or resolves to a non-public IP |
| `kuaishou_not_found` | 404 | HTTP 404/410 |
| `kuaishou_unsupported_post_type` | 422 | the `photoType` is neither video nor album |
| `kuaishou_invalid_url` / `kuaishou_redirect_rejected` | 400 | the link is not a Kuaishou video form, or the share link leads off Kuaishou |

The proxy URL never appears in logs or error text (tested).

## Risks

- **Geo-block:** without a working Chinese proxy, every request fails as `kuaishou_geo_blocked`. Proxy quality and cost are open questions.
- **Anti-bot churn:** the page structure, the device-id cookie and captcha behaviour can change at any time. Expect `kuaishou_parse_failed` or `kuaishou_challenge_page` in that case.
- **Timeouts:** one extraction gets a 27 s budget (connect 8 s, at most 25 s per request), so it ends inside the 30 s metadata timeout.
- **Media CDN:** it is unknown whether video URLs need a Referer, are IP-bound or expire quickly. The download step is unverified.
- **ToS / copyright:** public videos only. No login, no private content, no captcha solving.

## When it breaks

1. Run the capture tool against a public video through the proxy.
2. If **Fetch page** fails, the problem is the network or proxy. Check the proxy.
3. If fetch passes but every strategy fails, read each strategy's reason line and
   compare it with the saved capture. Update or add a strategy, then add the redacted
   capture as a fixture and a test for it.
4. If it cannot be fixed quickly, unset `KUAISHOU_ENABLED`.

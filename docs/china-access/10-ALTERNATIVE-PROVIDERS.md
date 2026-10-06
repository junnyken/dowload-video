# 10 - Alternative providers to Apify (Douyin / Kuaishou / Xiaohongshu)

Date read: 2026-10-06. Scope: managed APIs (not Apify) that resolve ONE public video URL to a media URL.

## Tóm tắt (Tiếng Việt)

Ứng viên đáng thử nhất là **TikHub.io**: có đủ cả 3 nền tảng, trả tiền theo lượt (pay-as-you-go), nạp tối thiểu 5 USD, không có gói thuê bao, xác thực bằng API key (Bearer), tặng 0,05 USD (~50 lượt) khi đăng ký để thử. Giá niêm yết trên trang vendor: Douyin 0,001 USD/lượt (Apify hiện 0,007), Kuaishou 0,001 USD (một trang trung gian ghi 0,002 USD - chưa thống nhất), Xiaohongshu 0,01 USD/lượt (đắt hơn Apify 0,0025, nên chỉ làm dự phòng). Trang TikHub tuyên bố Douyin trả link video không watermark; Kuaishou và Xiaohongshu CHƯA xác nhận link video trực tiếp - phải thử thật bằng 50 lượt miễn phí. Ứng viên thứ hai là **Just One API** (justoneapi.com): có cả 3 nền tảng nhưng KHÔNG đọc được bảng giá từng endpoint nên giá = chưa xác minh. Các nhà cung cấp khác (ScrapeCreators, EnsembleData, Bright Data) không thấy hỗ trợ 3 nền tảng này; RapidAPI chỉ có vài API Douyin do cá nhân đăng, độ tin cậy thấp. Rủi ro lớn nhất của TikHub: điều khoản "ALL SALES ARE FINAL - NO REFUNDS", cấm "resell/redistribute the Services", tự nhận không đảm bảo được các nền tảng cho phép truy cập, và bồi thường nếu nền tảng khiếu nại. Chủ sở hữu phải tự tạo tài khoản, nạp tiền, và nhập key vào trang admin của VidGrab - TUYỆT ĐỐI không dán key vào chat.

## English

### 1. Method and limits of this research

- Primary pages read: tikhub.io pricing/platform pages, user.tikhub.io terms (a JavaScript SPA; I decoded the text from the site's own bundle `https://user.tikhub.io/assets/TermsOfService-DERNRMGh.js`), TikHub SDK README on GitHub, api.justoneapi.com overview.
- Not readable (404, JS-only, or blocked): docs.tikhub.io (fetch blocked, only search snippets seen), tikhub.io/terms (404), justoneapi.com/pricing (404), docs.justoneapi.com (no pricing in excerpt), RapidAPI pricing pages (empty), scrapecreators.com/pricing (404).
- Nothing was called live. No API key was created. All "no watermark" claims are vendor claims and are **unverified** until tested with real URLs.
- Third-party mirrors (glasser.ai, treg.to, socq.ai, sandbase.ai) are used only as hints and marked as such; socq.ai and sandbase.ai are competitors of TikHub/each other and are vendor-biased.

### 2. Candidate A - TikHub.io (main candidate)

| Item | Finding | Source / status |
|---|---|---|
| Platforms | Douyin, Kuaishou, Xiaohongshu all present (SDK README: Douyin 319 endpoints; search result says Xiaohongshu 45, Kuaishou 38 - counts from search snippet, unverified) | https://github.com/TikHub/TikHub-API-Python-SDK ; https://tikhub.io/douyin-api |
| Douyin single-video endpoints | `/api/v1/douyin/app/v3/fetch_one_video`, `.../app/v3/fetch_one_video_by_share_url`, `.../app/v3/fetch_multi_video_high_quality_play_url`; web fallback `/api/v1/douyin/web/fetch_one_video` and `..._by_share_url`. Docs recommend App V3 for stability. | https://tikhub.io/douyin-api (read 2026-10-06) |
| Douyin output | Page table marks "video URL: yes, no watermark: yes". Vendor claim, **unverified** (no real response seen). Play count NOT in the fetch-video endpoint. | same |
| Kuaishou endpoints | App: `/api/v1/kuaishou/app/fetch_one_video`, `/api/v1/kuaishou/app/fetch_one_video_by_url`; Web: `/api/v1/kuaishou/web/fetch_one_video`, `.../fetch_one_video_v2`, `.../fetch_one_video_by_url` | https://tikhub.io/kuaishou-api |
| Kuaishou output | Page does NOT state whether a direct video URL is returned or watermark status. **Unverified.** A mirror says the endpoint "publishes no output schema" and answers "within 40s". | https://glasser.ai/endpoints/tikhub/kuaishou-single-video-data-v1-28b411b0 (third party) |
| Xiaohongshu endpoints | `/api/v1/xiaohongshu/app_v2/get_video_note_detail` (also `get_image_note_detail`) | https://tikhub.io/xiaohongshu-api |
| Xiaohongshu output | Page gives no statement on video URL or watermark. **Unverified.** | same |
| Price | Douyin $0.001/request (all single-video endpoints above). Kuaishou "from $0.001/request" on the vendor page; the mirror glasser.ai says $0.002 per call for `fetch_one_video` - **conflict, treat as $0.001-0.002, unverified**. Xiaohongshu $0.01/request. Billed per request even if no data is returned (search-result statement, unverified). | https://tikhub.io/douyin-api , /kuaishou-api , /xiaohongshu-api , https://tikhub.io/pricing |
| Volume discount | Automatic daily tiers, 0% at 0-1,000 req/day, up to 50% at 30,000+ req/day ($0.0005). Irrelevant at our volume. | https://tikhub.io/pricing ; https://docs.tikhub.io/186826052e0 |
| Free tier | About 50 free requests / $0.05 credit at sign-up, no card. "Some endpoints require paid credits" - so a free trial of the XHS endpoint is **unverified**. | https://tikhub.io/pricing |
| Minimum top-up / subscription | Min $5 top-up, no ceiling. No subscription, no monthly minimum. Higher RPS plans are billed monthly separately. | https://tikhub.io/pricing |
| Rate limit | 10 requests/s per account by default; paid upgrade to 100+ RPS. | https://tikhub.io/pricing |
| Auth | API key. Created in dashboard https://user.tikhub.io ; SDK reads `TIKHUB_API_KEY`. Header format (Bearer) is **unverified** - docs.tikhub.io not readable. | SDK README |
| Payment methods | Alipay, PayPal, Crypto (USDT), B2B bank transfer. Credit card is NOT listed. | https://tikhub.io/pricing |
| Caching | Responses cached 24 h at a URL for tracing, no extra cost; does not affect freshness. | https://docs.tikhub.io/186826052e0 |
| Maturity | Large catalog (1,048 endpoints, SDK v2.1.1, spec V5.3.2), changelog and docs site exist, support email team@tikhub.io. Status page: **none found.** Operator: TikHub, LLC, California (per terms). | SDK README ; terms |

#### TikHub Terms of Use (effective and last updated March 17, 2026)

Source: https://user.tikhub.io/terms (text decoded from the JS bundle; clause numbers not preserved).

- (a) Resale / public service:
  - Forbidden: "Sublicense, lease, rent, loan, sell, resell, or otherwise redistribute the Services to third parties without prior written authorization."
  - Licence is, per a search-result excerpt of the same page, "limited, non-exclusive, non-transferable, non-sublicensable ... solely for lawful internal business purposes" (excerpt only, **unverified in primary text**).
  - Nothing found that explicitly bans showing API results to end users of your own product. Because VidGrab is a public service using the data, **ask TikHub support in writing** before relying on it. Marked: unclear.
  - Customer is "solely responsible" for downstream use of data from Chinese platforms (PIPL, Cybersecurity Law, Data Security Law) and for not infringing copyright/trademark.
- (b) Multiple accounts: **no clause found** in the decoded text. Do not assume allowed; unverified. Also: API keys/credentials must not be shared with third parties.
- (c) Prohibited uses (found): circumventing access controls on Supported Platforms; infringing third-party IP/privacy rights; profiling individuals for harassment/doxxing; political surveillance/persecution; discriminating individuals; authenticating fake accounts or accessing accounts without owner consent; exceeding rate limits/quotas. Suspension without notice and without refund for violations.
- Platform risk: "TikHub makes no representation that its data access methods are authorized by any Supported Platform." Customer indemnifies TikHub for any claim by a platform or its parent related to the customer's use of the data. TikHub may change/remove platforms, endpoints, rate limits at any time with no refund. Price changes need 30 days' written notice.
- Payments: "all payments are non-refundable", including API credits; chargebacks waived (EU/UK/AU statutory consumer rights excepted). So top up only the minimum.
- Data retention / privacy: account data retained for the relationship and a reasonable period; deleted or anonymised within 90 days after termination. Terms state TikHub does not share "API query history, search parameters, or any information that reveals what data you specifically requested" (that line appears in the privacy/data-sharing section; the section also says TikHub may sell/license "Raw Platform Data" - public content - to third parties). Conflict rule: Privacy Policy prevails (privacy policy itself not read).

### 3. Candidate B - Just One API (justoneapi.com)

- Platforms: lists Xiaohongshu, Douyin, Kuaishou, 40 platforms / 369 endpoints; "video details" is in its capability list. Source: https://api.justoneapi.com/ (read 2026-10-06).
- Auth: token passed as a query parameter; base URL `https://api.justoneapi.com`; recommended client timeout 30-60 s. Same source. A token in the URL is more likely to leak into logs - the VidGrab client must not log full URLs.
- Pricing: per call, billed only on success (code 0). Balance does not expire. $1.00 free per new team, no card (from third-party treg.to, which resells it, https://treg.to/tools/justoneapi, and a search snippet). Treg.to quotes a range $0.00738 - $0.148 per successful call across 272 tools; the actual price of Douyin/Kuaishou/Xiaohongshu single-video endpoints: **unverified** (pricing page 404, docs excerpt had none). The only prices seen are for Xiaohongshu Pugongying (creator marketplace) endpoints, which are not what we need.
- Endpoint names, output (video URL / watermark), minimum top-up, rate limits, payment methods, ToS: **unverified / not read.**
- Maturity: Python SDK on PyPI/GitHub, MCP server, Telegram and email support, English and Chinese docs (docs.justoneapi.com, justoneapi.apifox.cn). Chinese-operated; reachable from Vietnam presumably - unverified.

### 4. Others checked and rejected for now

| Provider | Finding | Source |
|---|---|---|
| RapidAPI listings ("Douyin Media No Watermark", "Douyin API New", by individual publisher nguyenmanhict) | Douyin only. Plans from search snippet: Basic $0 (100 req/month), Pro $9 (200,000/month), Ultra $26.90, Mega $58 - from a search snippet, pricing page was empty when fetched = **unverified**. Single publisher, no Kuaishou/XHS, no SLA. Subscription not per-request. | https://rapidapi.com/nguyenmanhict-MuTUtGWD7K/api/douyin-media-no-watermark1/pricing |
| ScrapeCreators | TikTok, Instagram, YouTube, Facebook, X etc. No Douyin/Kuaishou/XHS endpoint found. | https://scrapecreators.com/ |
| EnsembleData | TikTok/Instagram/YouTube; subscriptions from $400/month; no evidence of the 3 platforms. | https://ensembledata.com/pricing |
| Bright Data, Data365 | Enterprise, no evidence for the 3 platforms (from a competitor's page, https://socq.ai/alternatives/tikhub-io-alternatives). | - |
| SandBase | Claims 262 Douyin endpoints and "free" in Oct 2026; promo from its own blog, Kuaishou/XHS not shown. Not recommended for production. | https://blog.sandbase.ai/best-douyin-data-api-services-2026/ |
| Self-hosted Evil0ctal/Douyin_TikTok_Download_API | Open source, not a managed provider; Douyin/TikTok only. Out of scope, noted as a no-cost experiment. | https://github.com/Evil0ctal/Douyin_TikTok_Download_API |
| Apify (for comparison only) | Douyin $0.007, Kuaishou $0.004, XHS $0.0025 per video (owner's current actors). | owner-supplied |

### 5. Comparison

| | Apify (current) | TikHub | Just One API |
|---|---|---|---|
| Douyin / video | $0.007 | $0.001 (claims no-WM, unverified) | unverified |
| Kuaishou / video | $0.004 | $0.001-0.002 (conflict), output unverified | unverified |
| Xiaohongshu / video | $0.0025 | $0.01, output unverified | unverified |
| Free trial | per Apify plan | $0.05 (~50 req) | $1.00 (third-party sources) |
| Minimum purchase | n/a | $5, non-refundable | unverified |
| Payment | n/a | Alipay, PayPal, USDT, bank transfer | unverified |
| Rate limit | n/a | 10 RPS default | unverified |
| Failure billing | n/a | billed per request (unverified) | free on error (per treg.to) |
| ToS read | - | yes (Mar 17, 2026) | no |

### 6. Recommendation

- **Douyin: TikHub** `fetch_one_video_by_share_url` (App V3), fall back to web variant. About 7x cheaper than Apify at the listed price. Primary risk: no-watermark and link lifetime unverified; Douyin media URLs are normally short-lived/signed (general knowledge, unverified for TikHub) - download immediately.
- **Kuaishou: TikHub** `app/fetch_one_video_by_url`, price $0.001-0.002. Risk: the page never states the response contains a playable URL. Test first; if it only returns metadata, drop TikHub for Kuaishou and try Just One API.
- **Xiaohongshu: keep Apify first** ($0.0025). TikHub `app_v2/get_video_note_detail` at $0.01 is 4x dearer; use it only as the fallback when Apify is down or out of credit. Alternative: test Just One API once its price is confirmed.
- Second provider as insurance: Just One API, only after the owner reads its pricing and terms in the dashboard.
- Risks: no refunds; TikHub disclaims platform authorisation and requires indemnity; ToS bans redistributing "the Services" (ask whether serving results to our users counts); platforms may block it at any time; dependence on a US LLC accepting Alipay/PayPal/USDT but not cards (card unverified).
- Cost of verification: the $0.05 free credit covers ~50 Douyin/Kuaishou calls but probably not many XHS calls (0.05 / 0.01 = 5 calls).

### 7. What the owner must do (nothing should be done in chat)

1. Register at https://user.tikhub.io (use the company email from the workspace environment, one account only since multi-account rules are unconfirmed). Read the Terms and the Privacy Policy once.
2. Use the free credit first: run 3-5 real public URLs per platform in the dashboard playground (or one curl), and record whether a direct mp4 URL is returned, whether it plays without watermark, how long it stays valid, and the latency.
3. Optional: email team@tikhub.io to ask (i) whether a public downloader site using results is allowed, (ii) multiple-account policy, (iii) failed-request billing.
4. Only if the test passes, top up the minimum $5 (non-refundable) via PayPal/Alipay/USDT.
5. Create the API key in the TikHub dashboard and paste it ONLY into the VidGrab admin settings page (provider key field; stored server-side). Never in chat, git, `.env.example` or logs. Rotate the key if it was ever pasted anywhere else.
6. Implementation hand-off (not done here): provider order per platform (Douyin: TikHub, Apify; Kuaishou: TikHub, Apify; XHS: Apify, TikHub), daily spend cap, alert when balance is below $1, and no logging of the key.

### 8. Sources (all read 2026-10-06)

- https://tikhub.io/pricing
- https://tikhub.io/douyin-api
- https://tikhub.io/kuaishou-api
- https://tikhub.io/xiaohongshu-api
- https://user.tikhub.io/terms (decoded from https://user.tikhub.io/assets/TermsOfService-DERNRMGh.js)
- https://docs.tikhub.io/186826052e0 (via search result)
- https://github.com/TikHub/TikHub-API-Python-SDK
- https://glasser.ai/endpoints/tikhub/kuaishou-single-video-data-v1-28b411b0 (third party)
- https://api.justoneapi.com/ ; https://treg.to/tools/justoneapi (third party)
- https://rapidapi.com/nguyenmanhict-MuTUtGWD7K/api/douyin-media-no-watermark1/pricing
- https://ensembledata.com/pricing ; https://scrapecreators.com/ ; https://socq.ai/alternatives/tikhub-io-alternatives ; https://blog.sandbase.ai/best-douyin-data-api-services-2026/

# Browser resolver (experiment, NOT deployed) — 15/10/2026

Goal (owner 15/10): a real Chromium with a persistent profile on the server, to
resolve Douyin / Xiaohongshu / Kuaishou for free before the paid Apify step.

## Measured in the workspace (douyin.com/video/7677628923164790070)

| Driver | Result |
|---|---|
| chrome-devtools MCP (puppeteer, Chrome for Testing 152) | 1/1 — `<video>` had douyinvod links, file downloaded with `Referer: https://www.douyin.com/` |
| Playwright 1.49 / 1.64, headless shell or channel chromium | 0/≈10 — `aweme/detail` 200 empty, or 403 `Blocked by ArgusSecurityPlugin Sign Invalid` |
| patchright + CfT 152 + persistent profile + images/media/fonts blocked | 3/6 (5–7 s each) |
| same, nothing blocked | 0/6 |
| same + reload on 403, up to 4 tries | 1/8 — fast retries made it worse |
| nodriver + CfT 152 | 0/6 |

Douyin's Argus signature check flags automated Chrome on 50–100 % of
navigations, worse the more we retry. Not reliable enough to replace Apify.
The free path that works is the user's own browser/PC (extension 5.7.0
direct download, app hidden webview).

Rumble: page loads from the workspace with a browser, but the server problem
is vibe1's IP reputation (403), which a browser on the same IP would share.

`app.py` / `resolvers.py` are kept for a later attempt (switch the import to
`patchright.async_api`, pin a current Chrome, measure from vibe1's IP first).

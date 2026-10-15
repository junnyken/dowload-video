# VidGrab douyin-mint

Gets the anonymous Douyin visitor cookies (`UIFID_TEMP`, `s_v_web_id`, `ttwid`)
that the backend's signed Douyin step needs. A real browser opens one public
video page per mint and closes; the backend asks for a new jar only when its
jar is older than the admin "max age" or Douyin refuses it 3 times in a row.

Env on this service: `MINT_KEY=<random 32+ chars>` (secret). Optional:
`MINT_VIDEO_IDS`, `MINT_WAIT_S`.

Backend side (admin, not env): `POST /api/v1/admin/china-platforms/douyin/visitor/settings`
`{"mint_url": "https://<this host>", "mint_key": "<same MINT_KEY>"}` then
`POST .../douyin/visitor/mint` to test. `GET .../douyin/visitor` shows the jar
age, ok/fail counts, pause state and calls this minute/hour.

Limits measured 15/10/2026 from one IP: 180 calls in ~4 min (4 in parallel)
all answered; after ~200 calls in ~10 min Douyin answered every call from that
IP with an empty 200, fresh jar or not. Hence the backend's per-minute /
per-hour caps (default 20/300) and the 30-minute pause.

Local test: `docker build -t vg-dymint . && docker run --rm -p 9056:3000 -e MINT_KEY=test vg-dymint`
then `curl -s -XPOST -H 'X-Mint-Key: test' localhost:9056/mint/douyin | jq '.cookies|keys'`.

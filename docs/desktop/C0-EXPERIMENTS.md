# Phase 32C / C0 — Experiment plan E1–E5

**Tóm tắt cho chủ dự án (VI).** Tài liệu này là kế hoạch thực nghiệm E1–E5 trước khi xây app desktop. Mỗi thí nghiệm có: câu hỏi, các bước chạy chính xác (lệnh PowerShell/curl/yt-dlp), mẫu thử, thứ cần ghi lại, luật quyết định (lấy từ plan32c.md), thời gian ước tính và ai chạy. E1 (một phần), E4 do phiên chính chạy trong workspace; E1 (phần mạng nhà), E2, E3, E5 cần máy Windows/mạng nhà của chủ dự án. Mọi con số "ước tính" là ước lượng, **chưa đo**. Phase 32B (Suno) đã bị huỷ — không có Suno trong bất kỳ danh sách nào.

Status: planning document, nothing in here has been executed. Results go in `docs/desktop/C0-RESULTS.md`.
Source plan: `plan32c.md` section 2b (decision rules are copied from there, not invented).

## 0. Ground rules

- Never record a number you did not observe. Missing = `not measured`.
- Every row of evidence needs: date/time (with timezone), operator, machine/network label, tool versions.
- Do not use accounts or cookies of real users. Test only public videos. Do not publish sample URLs of third-party content outside the internal results file.
- Terms-of-service risk is unchanged by C0; experiments fetch only a few KB to a few MB per link unless stated.
- Suno is out of scope (32B dropped by owner).

### Facts about the existing backend that the experiments rely on (verified by reading code on 2026-10-06)

| Fact | Where |
|---|---|
| `POST /api/v1/fetch-link` runs the download on the server (Celery task), after `_assert_safe_url` | `backend/app/api/routes.py` (`fetch_link`) |
| Cheap-platform quota bucket defaults to `tiktok,douyin,threads` (env `CHEAP_PLATFORMS`) | `backend/app/core/quotas.py` |
| YouTube is behind `YOUTUBE_ENABLED` (+ Redis admin override). The module docstring states that the datacenter IP is bot-blocked and the CDN URLs are IP-locked to the residential proxy exit. That is a code comment from earlier work, **not** a measurement made in C0 — E1 must re-measure it | `backend/app/core/youtube_gate.py` |
| SSRF guard resolves hostnames and re-validates each redirect hop (max 5); DNS rebinding is documented as a residual risk | `backend/app/core/ssrf_guard.py` |
| 21 platforms are probed (`PROBE_PLATFORMS`): youtube, tiktok, douyin, facebook, instagram, twitter, threads, reddit, pinterest, bilibili, xiaohongshu, lemon8, snapchat, vk, twitch, rumble, odysee, dailymotion, soundcloud, spotify, podcast | `backend/app/core/platform_probe.py` |
| `POST /api/v1/fetch-link` DOES return CDN URLs: `direct_mp4_url` and `available_formats[].url` (verified live 2026-10-06; for YouTube on production the list was empty). So the production resolver can be used from anywhere through the public API — no host access needed. Side effect: each call also downloads the file on the server (T0), so keep sample sets modest | live call, see C0-RESULTS.md E1 |

Two labelled variants:
- **E1-P** (production resolver, via `POST /api/v1/fetch-link`; fetch from another network). This is the real T1 case ("server resolves, user fetches"). A pilot was run from the workspace network on 2026-10-06 (see results); the decision run must fetch from a HOME connection.
- **E1-W** (resolve with `yt-dlp` on the operator's own machine, fetch elsewhere): optional, proves/disproves IP binding for platforms the server cannot resolve.

---

## E1 — Is the CDN link bound to the resolving IP?

**Question.** If link L was resolved from IP A, does fetching L from IP B (home network) still work?

**Decision rule (plan).** Set `handoff_ok=true` for a platform only if >= 95 percent of attempts succeed.
Arithmetic consequence (not in plan): with 10 links the rule requires 10/10 (9/10 = 90 percent < 95); 19/20 = 95 percent passes. So use **20 links** for any platform we actually want to enable; 10 is the plan minimum and only enough to *reject*.

### Sample set

| Group | Platforms | Links each | Notes |
|---|---|---|---|
| A (candidate T1) | tiktok, douyin, threads, bilibili, reddit, twitter, vk, dailymotion, rumble, odysee, soundcloud, pinterest, twitch (VODs/clips) | 20 (min 10) | public, anonymous-accessible |
| B (cookie/login-prone) | facebook, instagram, xiaohongshu, lemon8, snapchat | 10 | if resolve itself fails anonymously, record `resolve_failed`; excluded from denominator, reported separately |
| C (informational) | youtube | 10 | expected to need PO token/SABR; recorded to compare with the `youtube_gate` docstring claim; never `handoff_ok` (plan rule 4) |
| Excluded | spotify, podcast | — | not a CDN-video case; podcast RSS enclosures are plain static URLs — no experiment needed, mark "not tested" |

How links are picked (to avoid cherry-picking):
1. Take each platform's official account/feed or search results page; take the **first 20 distinct public videos** that are single-stream-capable, from **>= 5 different uploaders**, at least 5 posted > 30 days ago and 5 within the last 48 h.
2. Record the pick procedure, date and who picked in the results file. Do not replace a failing link with a "better" one; failures count.
3. Include 3 links per platform that are > 100 MB or > 5 minutes long (needed for the mid-transfer test below).

### Tools

- Resolver: `yt-dlp` (record `yt-dlp --version`), plus `jq` (Linux) for parsing.
- Fetcher on home Windows: `curl.exe` (ships with Windows 10 1803+) and PowerShell 5.1+.
- Record network labels: `RESOLVER_IP` (`curl -s https://api.ipify.org`) and `FETCHER_IP` (same command on the home machine). Do not publish the home IP outside the results file.

### Procedure

**Step 1 — resolve (resolver host, Linux shell).** `links.txt` holds one page URL per line, `platform<TAB>url`.

```bash
# resolve.sh — writes one JSON line per link to resolved.jsonl
RESOLVER_IP=$(curl -s https://api.ipify.org)
yt-dlp --version > tools.txt
while IFS=$'\t' read -r platform url; do
  # single-stream, progressive http(s) only: this is the only shape T1 may ever hand off (plan rule 2)
  out=$(yt-dlp -J --no-playlist --no-warnings \
        -f 'best[protocol^=http][vcodec!=none][acodec!=none]/best[protocol^=http]' "$url" 2>err.tmp)
  rc=$?
  if [ $rc -ne 0 ]; then
    jq -nc --arg p "$platform" --arg u "$url" --arg e "$(head -c 300 err.tmp)" --arg ip "$RESOLVER_IP" \
      '{platform:$p,page:$u,resolve:"failed",error:$e,resolver_ip:$ip}' >> resolved.jsonl
    continue
  fi
  echo "$out" | jq -c --arg p "$platform" --arg u "$url" --arg ip "$RESOLVER_IP" --arg t "$(date -u +%FT%TZ)" '
    {platform:$p,page:$u,resolve:"ok",resolved_at:$t,resolver_ip:$ip,
     media:.url,ext:.ext,filesize:(.filesize // .filesize_approx),
     headers:.http_headers,format_id:.format_id,protocol:.protocol}' >> resolved.jsonl
done < links.txt
```

If the extractor needs a `requiresMerge` pair (no single-stream format) the line shows `protocol` of `m3u8`/DASH or the resolve fails: record `needs_merge` — those are T2 by plan rule 2 and are not T1 candidates (not a failure of E1).

**Step 2 — inspect the URL for IP binding hints.**

```bash
jq -r '[.platform, (.media|capture("(?<q>[?&](ip|ipbits|ipb|sip|clientip)=[^&]*)")?.q // "no-ip-param"),
        (.media|capture("[?&](expire|exp|e|ei|expires)=(?<v>[0-9]+)")?.v // "no-expire-param")] | @tsv' resolved.jsonl
```

Record the parameter names found. Absence of an `ip=` parameter does not prove it is unbound; the CDN may bind server-side.

**Step 3 — control fetch from the resolver network (within 60 s of resolving).** Confirms the link is alive at all, so a later 403 can be attributed to the network change and not to a dead link.

```bash
jq -r '[.platform,.media] | @tsv' resolved.jsonl | while IFS=$'\t' read -r p m; do
  curl -sS -L -o /dev/null -w "$p control %{http_code} %{size_download} %{content_type}\n" \
       -H "Range: bytes=0-1048575" "$m"
done
```

**Step 4 — test fetch from the home network (Windows).** Copy `resolved.jsonl` to the home PC (within a few minutes of resolving; time between resolve and fetch must be recorded, and kept < 10 min for E1 so expiry is not the confound).

Two request profiles per link, because T1 means the user's *browser* makes the request: (a) bare = no special headers, browser-like UA; (b) with the headers yt-dlp reported (`Referer` etc.).

```powershell
# e1-fetch.ps1 -- run in Windows PowerShell 5.1 or PowerShell 7
$fetcherIp = (Invoke-RestMethod https://api.ipify.org)
$ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
$rows = @()
foreach ($line in Get-Content .\resolved.jsonl) {
  $r = $line | ConvertFrom-Json
  if ($r.resolve -ne 'ok') { continue }
  foreach ($prof in 'bare','headers') {
    $a = @('-sS','-L','-o','NUL','-w','%{http_code} %{size_download} %{content_type} %{remote_ip}',
           '-H','Range: bytes=0-1048575','-A',$ua)
    if ($prof -eq 'headers' -and $r.headers) {
      foreach ($p in $r.headers.PSObject.Properties) {
        if ($p.Name -ne 'User-Agent') { $a += @('-H', ($p.Name + ': ' + $p.Value)) }
      }
    }
    $a += $r.media
    $res = & curl.exe @a 2>&1
    $rows += [pscustomobject]@{ platform=$r.platform; page=$r.page; profile=$prof; fetcher_ip=$fetcherIp;
             fetched_at=(Get-Date).ToUniversalTime().ToString('o'); result=($res -join ' ') }
  }
}
$rows | Export-Csv -NoTypeInformation -Encoding UTF8 .\e1-home-results.csv
```

**Step 5 — browser-realistic check (3 links per platform, manual).** Paste the media URL into Chrome and Edge address bars on the home PC. Record: plays/downloads, 403, or redirect. Browsers send `Referer`-less, cookie-less navigations; this is the closest to what T1 would do from `<a href>`/`<a download>`. Also test whether `fetch()` from an https://dvid.vibe1.tinhgon.xyz page is blocked by CORS (open DevTools console on the app page):
```js
fetch(MEDIA_URL, {headers:{Range:'bytes=0-1023'}}).then(r=>console.log(r.status)).catch(e=>console.log('blocked',e.message))
```
CORS failure does not matter for plain navigation downloads but matters for a progress-aware in-page download; record it.

**Step 6 — mid-transfer test (3 long links per platform).** Download the whole file on the home PC:
```powershell
curl.exe -L -o NUL -w "%{http_code} %{size_download} %{time_total}`n" "<media url>"
```
Record whether it completes; a late 403 or short size (compare to `filesize`) is a mid-transfer failure.

### What to record (per link; columns match C0-RESULTS.md)

platform, page URL, resolver label + IP class (datacenter/residential), resolved_at, fetch network label, fetched_at, delay (s), status code and bytes for control / bare / headers, content-type, `ip=`/`expire=` param presence, browser check result, full-download result, notes.

### Success definition

A link counts as **success** if the home fetch (bare profile, because T1 is browser-made) returns 200 or 206, `size_download` > 0, and `Content-Type` is video/*, audio/* or application/octet-stream (not text/html or JSON). The `headers` profile is recorded to learn *why* a bare fetch failed, but does not count for the 95 percent test.
Denominator = links whose control fetch succeeded. Links failing the control are `dead_link` and reported, not counted.

### Decision

Per platform: success / denominator >= 95 percent **and** the mid-transfer test and browser check show no failures → `handoff_ok = true`; else false. Anything not run → `not measured` (treated as false).

### Time and owner

| Part | Estimate (not measured) | Who |
|---|---|---|
| E1-W resolve + control (Step 1–3) | ~1 h for ~200 links incl. picking | Main session in workspace |
| E1-P resolve on production host | ~30 min | Owner or host-access operator |
| Step 4–6 from home network | ~1–2 h | Owner on Windows at home |

---

## E2 — How long do links live?

**Question.** For each platform, after how many minutes does a resolved link return 403/410/expired?

**Decision rule (plan).** Set `expiresAt` conservatively below the measured value.
Proposal (not in plan, owner to approve): `expiresAt = resolved_at + 50% of the *shortest* observed lifetime for that platform`, and if the URL contains an `expire`/`exp` timestamp, use `min(that timestamp, the 50% rule)`.

### Sample set

Only platforms that passed E1 or are T1 candidates (Group A), **3 links per platform**, one of them freshly uploaded. YouTube is included with 2 links for information.

### Procedure

Resolve as in E1 Step 1, then poll the same media URL every 10 minutes from the **same network that resolved it** (this isolates time from IP binding), with a 1 KB range request:

```bash
# e2-poll.sh <resolved.jsonl>   — run in tmux/nohup; stops each link at first non-2xx or after 24 h
start=$(date +%s)
jq -c '. | select(.resolve=="ok")' "$1" | while read -r row; do
  p=$(echo "$row" | jq -r .platform); m=$(echo "$row" | jq -r .media)
  ( rt=$(date +%s)
    while :; do
      code=$(curl -s -o /dev/null -w '%{http_code}' -H 'Range: bytes=0-1023' "$m")
      now=$(date +%s); age=$(( (now-rt)/60 ))
      echo -e "$p\t$age\t$code\t$(date -u +%FT%TZ)" >> e2-log.tsv
      case "$code" in 200|206) ;; *) break;; esac
      [ "$age" -ge 1440 ] && { echo -e "$p\t$age\tSTILL_ALIVE_24H" >> e2-log.tsv; break; }
      sleep 600
    done ) &
done
wait
```
Lifetime = last age (minutes) with 2xx; the failure age is the next sample. Resolution is therefore 10 min. Compare with the `expire=` value in the URL where present (`date -d @<epoch>`).

### Record
platform, link id, resolved_at, last-ok age, first-fail age and code, `expire=` param value converted to minutes-after-resolve, `STILL_ALIVE_24H` flag.

### Time / owner
Wall-clock up to 24 h but unattended; effort ~1 h. Can run entirely in the workspace (E2-W) by the main session; no Windows needed. If run only from the workspace it says nothing about the production IP — label it.

---

## E3 — Does local yt-dlp work from a normal home IP?

**Question.** On ordinary Windows machines at home IPs, what percentage of YouTube downloads succeed with (a) yt-dlp alone, (b) + Deno JavaScript runtime, (c) + a PO-token provider?

**Decision rule (plan).** If success < 90 percent, bundle a token provider or restrict YouTube support.
Interpretation to confirm: evaluate the configuration we intend to ship (start with b); if b < 90 percent, test c; if c < 90 percent restrict YouTube support in the app.

### Sample set (30 videos, same list on every machine)

| Count | Kind |
|---|---|
| 8 | popular music videos, >= 1080p |
| 8 | ordinary public videos, 3–15 min, 1080p |
| 4 | Shorts |
| 4 | long (> 60 min) |
| 3 | 4K / VP9 / AV1 available |
| 3 | recently uploaded (< 24 h) |

Excluded: age-restricted, members-only, private, live streams in progress (these need cookies/login — a cookie flow is a separate decision and the app never sends cookies to the server). Picked by trending/search at test time; record the list and date. Same list for all machines and configs so results are comparable.

### Machines
3 Windows machines (mix of Windows 10 and 11), at least 2 different ISPs; ideally 1 with a "dirty" shared/mobile IP to see the lower bound. Record: Windows build (`winver`), ISP, IP class (`curl.exe https://api.ipify.org` — keep private), country.

### Setup (each machine; PowerShell)

```powershell
mkdir C:\c0\bin; cd C:\c0\bin
# Official release artifacts only; record versions + SHA256
curl.exe -L -o yt-dlp.exe https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe
curl.exe -L -o SHA2-256SUMS https://github.com/yt-dlp/yt-dlp/releases/latest/download/SHA2-256SUMS
Get-FileHash yt-dlp.exe -Algorithm SHA256      # compare with the line in SHA2-256SUMS
.\yt-dlp.exe --version
# ffmpeg/ffprobe: a build from a pinned official/known source; record URL + SHA256 (choice pending, see DESIGN-NOTES §2)
# Deno: https://github.com/denoland/deno/releases  (deno-x86_64-pc-windows-msvc.zip); record version + SHA256
.\deno.exe --version
.\yt-dlp.exe --help | findstr /i "js-runtimes"    # confirms the exact flag name in the installed version
```
The runtime flag (`--js-runtimes`) and plugin directory conventions differ between yt-dlp versions: **verify with `--help` and the yt-dlp README for the version you installed**; the commands below are the intended shape, not guaranteed syntax.

### Configurations

| Cfg | Description |
|---|---|
| a | yt-dlp only (`--no-js-runtimes` if available, or Deno not on PATH) |
| b | yt-dlp + Deno (`--js-runtimes deno:C:\c0\bin\deno.exe`, if that flag exists in the installed version) |
| c | b + PO-token provider plugin. Candidate to evaluate (not verified here): the `bgutil-ytdlp-pot-provider` yt-dlp plugin. Record exact name, version, how it is run, and whether it needs Node/Docker |

### Per-video run

```powershell
# e3-run.ps1 <cfg> <videos.txt>   -> appends to e3-<machine>-<cfg>.csv
param($cfg, $list)
$machine = $env:COMPUTERNAME
New-Item -ItemType Directory -Force C:\c0\out | Out-Null
Get-Content $list | ForEach-Object {
  $url = $_; $t0 = Get-Date
  Remove-Item C:\c0\out\* -Force -ErrorAction SilentlyContinue
  $extra = @()
  if ($cfg -eq 'b' -or $cfg -eq 'c') { $extra += @('--js-runtimes','deno:C:\c0\bin\deno.exe') }  # verify flag
  $log = & C:\c0\bin\yt-dlp.exe @extra --no-playlist --no-warnings `
        -f "bv*[height<=1080]+ba/b[height<=1080]" --merge-output-format mp4 `
        --ffmpeg-location C:\c0\bin -o "C:\c0\out\%(id)s.%(ext)s" $url 2>&1
  $rc = $LASTEXITCODE; $secs = [int]((Get-Date)-$t0).TotalSeconds
  $f = Get-ChildItem C:\c0\out\*.mp4 -ErrorAction SilentlyContinue | Select-Object -First 1
  $ok = $false; $dur=''; $h=''
  if ($f) {
    $p = & C:\c0\bin\ffprobe.exe -v error -show_entries format=duration:stream=codec_type,height -of json $f.FullName | ConvertFrom-Json
    $types = $p.streams.codec_type
    $ok = ($rc -eq 0) -and ($types -contains 'video') -and ($types -contains 'audio')
    $dur = $p.format.duration; $h = ($p.streams | Where-Object codec_type -eq 'video').height
  }
  [pscustomobject]@{machine=$machine;cfg=$cfg;url=$url;rc=$rc;ok=$ok;secs=$secs;duration=$dur;height=$h;
     size=($f.Length);err=(($log | Select-String 'ERROR|Sign in|403|PO Token|SABR' | Select-Object -First 1) -as [string]);
     when=(Get-Date).ToUniversalTime().ToString('o')} |
     Export-Csv -Append -NoTypeInformation -Encoding UTF8 "C:\c0\e3-$machine-$cfg.csv"
}
```
Success = exit code 0, mp4 exists, has both a video and an audio stream per ffprobe. Duration deviation vs `yt-dlp --print duration URL` > 2 s counts as failure (truncated). Keep the first matching error text for failures.

### Record
Per run: machine, config, URL, exit code, success, seconds, height achieved, error class. Per machine and config: successes/30. Pooled: successes/90 per config. Also versions of yt-dlp/Deno/plugin/ffmpeg and the date (YouTube behaviour changes weekly; the result has a shelf life — note it).

### Time / owner
Estimate (not measured): setup ~1 h per machine; 30 downloads ~1–2 h per config per machine; whole experiment ~1 working day for 3 machines x 2–3 configs. **Owner on Windows at home — cannot run in the workspace** (no home IP, no Windows).

---

## E4 — Is browser-side merging (ffmpeg.wasm) usable?

**Question.** Up to what input size does ffmpeg.wasm successfully mux video-only + audio-only streams in a browser, versus native ffmpeg?

**Decision rule (plan).** Expected to fail above ~200 MB in browsers (an expectation, not a result); route merging to T2. Use measured numbers to set the threshold.

### Inputs
Real streams, not synthetic. Use the same video at several sizes. Procedure to obtain them:
```bash
# 5 sources of ~50, 100, 200, 400, 800 MB video-only; matching audio-only. Pick public videos from a platform that works from the workspace;
yt-dlp -f 'bv*[ext=mp4]' -o 'v_%(id)s.%(ext)s' <url>   # video-only
yt-dlp -f 'ba[ext=m4a]'  -o 'a_%(id)s.%(ext)s' <url>   # audio-only
ls -l v_* a_*     # record exact byte sizes
```
If a size band cannot be found, concatenate-free alternative: record the actual sizes obtained; do not claim a size that was not tested.

### Procedure

1. **Native baseline** (workspace Linux; and Windows if available): 
   ```bash
   /usr/bin/time -v ffmpeg -y -i v.mp4 -i a.m4a -c copy -movflags +faststart out_native.mp4 2> native.log
   ```
   Record wall time, max RSS (`Maximum resident set size`), exit code, output size.
2. **ffmpeg.wasm in a real browser**: a minimal static page using `@ffmpeg/ffmpeg` + `@ffmpeg/util` (record package versions; test both the single-thread core and the multi-thread core — the multi-thread core needs `SharedArrayBuffer`, hence COOP/COEP headers). Steps in page: write inputs to the wasm FS (`writeFile`), run `['-i','v.mp4','-i','a.m4a','-c','copy','out.mp4']`, read `out.mp4`. Capture: success/fail, error message, time, and browser memory (Chrome Task Manager "GPU/Renderer memory", or `performance.memory` where present).
3. Browsers: headless Chromium in the workspace via Playwright (main session; note the workspace's known Chromium font/libnspr constraints) **and** one real desktop Chrome on the owner's Windows machine for the 200/400/800 MB cases. A mobile browser check (Android Chrome) is optional.
4. Repeat each size 3 times; record all outcomes.

### Record
Size (bytes) of each input, tool (native/wasm-st/wasm-mt), browser+version, outcome, time, peak memory, error text, repeat number.

### Decision
`max_ok_bytes` = largest total input size that succeeded 3/3 in the real desktop browser. T1/web merge is allowed only below `max_ok_bytes x 0.5` **(proposal, not from plan)**; everything above → T2. If the number is lower than a typical 1080p video, the product conclusion "merge only in T2" holds.

### Time / owner
Estimate (not measured): 2–3 h. Native + headless-wasm: main session in workspace. Real Chrome on Windows: owner (~30 min).

<!-- E4 RESULTS go in C0-RESULTS.md -->

---

## E5 — Can the extension hand a URL to the desktop app (`vidgrab://`)?

**Question.** From the existing extension (and from a plain web link), can a click open the installed desktop app with a URL, on a clean Windows install without admin rights?

**Decision rule (plan).** Keep the feature only if it works on a clean Windows install without admin rights.

### Prerequisites
A C0 desktop build with the protocol registered per-user. The scaffold is being written separately under `desktop/` (not by this document). If the scaffold does not register the scheme, use the manual registry spike below to answer the OS-level part of the question independently of Tauri.

### Procedure

**Part A — OS-level, no app needed (Windows, standard user, no elevation).** 
```powershell
# Register vidgrab:// for the current user only (HKCU => no admin)
$exe = "$env:TEMP\vg-echo.cmd"
'@echo %1 > "%TEMP%\vg-arg.txt"' | Set-Content $exe -Encoding ASCII
New-Item -Path 'HKCU:\Software\Classes\vidgrab' -Force | Out-Null
Set-ItemProperty 'HKCU:\Software\Classes\vidgrab' -Name '(default)' -Value 'URL:VidGrab'
Set-ItemProperty 'HKCU:\Software\Classes\vidgrab' -Name 'URL Protocol' -Value ''
New-Item -Path 'HKCU:\Software\Classes\vidgrab\shell\open\command' -Force | Out-Null
Set-ItemProperty 'HKCU:\Software\Classes\vidgrab\shell\open\command' -Name '(default)' -Value "`"$exe`" `"%1`""
Start-Process 'vidgrab://open?url=https%3A%2F%2Fexample.com%2Fv'
Get-Content "$env:TEMP\vg-arg.txt"
```
Verifies the registry mechanism works for a non-admin user. Remove after: `Remove-Item -Recurse HKCU:\Software\Classes\vidgrab`.

**Part B — real installer.** Install the C0 NSIS build per-user (no UAC prompt expected; record whether one appears). Check `reg query HKCU\Software\Classes\vidgrab` (and HKLM, to confirm nothing machine-wide was written).

**Part C — matrix.** For each cell record: prompt shown?, app opened?, URL received intact?, any admin prompt?

| | Chrome | Edge | Firefox |
|---|---|---|---|
| Link click `<a href="vidgrab://open?url=...">` on the web app | | | |
| Extension popup/background opens the URL (`chrome.tabs.update` / `window.open`) | | | |
| App not running (cold start) | | | |
| App already running (single instance should focus and receive URL) | | | |
| Win+R `vidgrab://...` | n/a | n/a | n/a |

Clean machines: Windows 10 and 11, a **standard (non-admin) account**, fresh profile. Test inputs: an ordinary URL, a URL with `&`, `%`, unicode, and a ~2000-char URL; record where truncation or failure occurs.

**Part D — safety checks (record yes/no).** The app must treat the incoming URL as untrusted: it must run the same validation as pasted URLs, never auto-start a download without a visible confirmation, and not pass the string to a shell. A web page can trigger `vidgrab://` too, so this is a security question, not only a UX one.

### Record
Matrix above, installer behaviour (UAC prompt yes/no), registry location, Windows build, browser versions, extension build/commit.

### Time / owner
Estimate (not measured): Part A ~15 min; Parts B–D ~2 h on two clean Windows machines (Win10 + Win11 VMs are fine). **Owner on Windows**; blocked on the scaffold build for B–D.

---

## Execution order

1. E1-W + E2-W + E4 (workspace) — main session, now.
2. E3 and E1 home-fetch part — owner, one day on Windows at home.
3. E1-P (production resolver) — owner/host-access.
4. E5 — owner, once the scaffold installer exists.

Everything feeds `C0-RESULTS.md`; no `handoff_ok` value may be set without a cited row there (acceptance criterion 10).

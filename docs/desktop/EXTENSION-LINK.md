# Extension ⇄ app link (native messaging) — app 0.20.0, extension 5.22.0

Before 0.20.0 the extension could only *ask* the app (`POST 127.0.0.1:17325/vidgrab/download`),
and a channel relay went through the server: the app opened `…#vg-relay=<token>` in Chrome (or the
default browser), the extension PUT the list to `/api/v1/channel-relay`, the app polled it. Two problems:

- the relay page landed in the **default browser**, which may not have the extension (Douyin in Edge
  waited forever);
- `Origin: chrome-extension://…` only keeps web pages out — any program on the machine can send it.

Now each browser with the extension keeps a native messaging host connected to the app:

```
extension (background.js)  ── connectNative("xyz.tinhgon.vidgrab") ──▶  host = vidgrab-host-<ver>.exe
        ▲  frames: u32 LE length + JSON                                       │ GET /vidgrab/native
        └──────────────────────────────────────────────────────────────────── │ X-VidGrab-Token
                                                                     app (ext_bridge.rs → ext_link.rs)
```

The browser starts the host **only for the extension ids in the host manifest**, so the app knows the
other end is the VidGrab extension. The app then knows which browser has it, and can ask it to do work:

| From → to | Message | What for |
|---|---|---|
| ext → app | `{"t":"hello","ext","brand"}` | which browser, which extension version |
| app → ext | `{"t":"req","id","op":"open_relay","url"}` → `res {signedIn}` | open + scroll a Douyin profile / Facebook Reels page in *that* browser |
| ext → app | `{"t":"evt","id","data":{items,channelTitle,done,skipped,loginWall}}` | relay progress, no server |
| ext → app | `{"t":"download","url","title","referer"}` | like `POST /vidgrab/download` |
| host → ext | `{"t":"app","up":bool,"version"}` | app running or not (the host retries every 3 s) |

Fallbacks kept: no link (app < 0.20.0, extension < 5.22.0, not signed in, share-text link) → the
server relay and the HTTP port exactly as before.

## Extension id

Users load the extension unpacked, so Chrome used to derive its id from the folder. 5.22.0 pins it with
the manifest `"key"` → id **`gkoepflfmpdmkhmccekmajlfdnfklmgi`** on every machine
(`native_reg.rs ALLOWED_IDS`; test `pinned_id_matches_the_manifest_key`). The private key is not in the
repo (`~/.vidgrab-secrets/extension-key.pem` on trieunt's workspace); it is only needed to pack a `.crx`.

- **One-time effect:** someone who updates by copying 5.22.0 over the old folder gets the new id → the
  extension's local data (sign-in, its download history) starts empty; signing in again on the
  website signs the extension in again. Someone who loads it from a new folder already got a new id
  every time.
- **Chrome Web Store:** `package-extension.py --store` removes `"key"` (the store refuses it). When the
  store item is published, add its id to `ALLOWED_IDS` and ship an app update.

## What the app writes (per user, no admin)

- `%LOCALAPPDATA%\xyz.tinhgon.vidgrab\native-host\`
  - `token` — new each app start; the host must send it back. Readable by anything running as this
    user (same trust boundary as the saved cookies).
  - `vidgrab-host-<version>.exe` — a copy of VidGrab.exe. The browser keeps its host running; if that
    were VidGrab.exe itself the updater could not replace it. Old copies are deleted when no browser
    runs them.
  - `xyz.tinhgon.vidgrab.json` — host manifest.
- `HKCU\Software\{Google\Chrome, Chromium, Microsoft\Edge, BraveSoftware\Brave-Browser}\NativeMessagingHosts\xyz.tinhgon.vidgrab`
  = path of that manifest.

Uninstall leaves these behind (harmless: the browser just finds no host).

## Tests

- `cargo test --lib -- ext_bridge ext_link native_host native_reg` — including
  `extension_host_app_round_trip` (fake extension ⇄ real host ⇄ real bridge ⇄ ext_link).
- `node chrome-extension/tests/test_native_link.js` — background.js against a mocked `chrome`.
- `python3 chrome-extension/tests/test_native_link_browser.py` — real Chromium: the browser starts the
  host by itself, relay over the link, download, app restart, host ends with the browser.
- `cargo xwin check --lib --target x86_64-pc-windows-msvc` — the Windows-only code (registry, parent
  process walk) compiles; it has **not run on Windows yet**.

## To check on Windows (not testable here)

1. Install app 0.20.0, open it once; `reg query HKCU\Software\Google\Chrome\NativeMessagingHosts\xyz.tinhgon.vidgrab`
   shows the manifest path; the folder above has the three files.
2. Load extension 5.22.0 in Chrome **and** Edge; Task Manager shows `vidgrab-host-0.20.0.exe` once per
   browser, no console window appears.
3. Channels → a Douyin profile → the button reads "Lấy đủ kênh bằng Microsoft Edge" (or Chrome); the tab
   opens in that browser even when it is not the default; the list comes back.
4. With a browser open, run an app update: the installer must not complain that a file is in use.

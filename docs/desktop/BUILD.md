# VidGrab Desktop: building the Windows installer (stage C0)

Scope: the C0 spike in `desktop/`. It is a Tauri 2 app that runs a bundled,
checksum-verified yt-dlp on one URL and kills the whole process tree on
cancel. It is not the MVP.

All commands are for **Windows 10/11 x64, PowerShell**, run from the repository
root unless stated otherwise. The installer must be built on Windows. Building
on Linux needs NSIS, LLVM (`llvm-rc`, `lld`) and `cargo-xwin`, and is not
covered here.

## 1. One-time machine setup

1. **Visual Studio 2022 Build Tools, "Desktop development with C++" workload**
   (MSVC compiler, Windows SDK). Install it from the GUI, or with:
   ```powershell
   winget install --id Microsoft.VisualStudio.2022.BuildTools --override "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
   ```
2. **Rust** using the MSVC toolchain:
   ```powershell
   winget install --id Rustlang.Rustup
   # open a new terminal, then:
   rustup default stable-msvc
   rustc -vV        # "host: x86_64-pc-windows-msvc"
   ```
3. **WebView2 runtime**: already present on Windows 10 1803+ and Windows 11.
   Only for an old machine, install the "Evergreen Bootstrapper" from
   https://developer.microsoft.com/en-us/microsoft-edge/webview2/ .
   End users do not need this step, because the installer downloads the
   bootstrapper itself (`webviewInstallMode: downloadBootstrapper`).
4. **Node.js 22**:
   ```powershell
   winget install --id OpenJS.NodeJS.LTS
   node --version   # v22.x
   ```

## 2. Install JavaScript dependencies

```powershell
cd desktop
npm ci
```

## 3. Fetch the sidecar binaries (yt-dlp, ffmpeg, ffprobe, deno)

The pins are in `desktop/binaries.lock.json`. The script is plain ESM
JavaScript (`scripts/fetch-binaries.mjs`, no build step, no npm dependencies).

| Binary | Version pinned | Source | Checksum source |
|---|---|---|---|
| yt-dlp.exe | 2026.08.19 | github.com/yt-dlp/yt-dlp release asset | the release's `SHA2-256SUMS` |
| ffmpeg.exe, ffprobe.exe | 9.0.2 essentials (GPL) | github.com/GyanD/codexffmpeg release 9.0.2 (gyan.dev builds) | gyan.dev `ffmpeg-release-essentials.zip.sha256` (matches the GitHub asset digest) |
| deno.exe | 2.9.7 | github.com/denoland/deno release asset | the release's `.zip.sha256sum` and `.sha256sum` (binary) |

gyan.dev publishes the hash of the zip only, not of `ffmpeg.exe` and
`ffprobe.exe` inside it. Their `extractedSha256` is therefore `REPLACE_ME`
until it is recorded from a verified archive. **Run this once**, review the
lock file diff (only the two `extractedSha256` values should change), and
commit it:

```powershell
node scripts/fetch-binaries.mjs --target x86_64-pc-windows-msvc --record-extracted
git diff -- binaries.lock.json
```

After that, every build machine runs the strict form, which refuses on any
mismatch or placeholder:

```powershell
node scripts/fetch-binaries.mjs        # same as: npm run fetch-binaries
```

Result: `src-tauri/binaries/{yt-dlp,ffmpeg,ffprobe,deno}-x86_64-pc-windows-msvc.exe`.
That folder is git-ignored. Downloads are cached in `desktop/.cache/binaries/`,
keyed by hash. Total download is about 175 MB: the 115 MB ffmpeg zip, the
43 MB Deno zip and the 18 MB yt-dlp.

Runtime check: `src-tauri/build.rs` compiles each `extractedSha256` for the
build target into the app. Before every download the app hashes all four
sidecars and refuses to start if any differs. A pin that is still
`REPLACE_ME` compiles as "no pin" (cargo prints a warning), and the app
refuses to run that binary. **Rebuild after changing the lock file.**

## 4. Updater (OFF in C0)

C0 ships an unsigned installer with no updater: `tauri-plugin-updater` is an
optional cargo feature (`updater`, default off) and
`bundle.createUpdaterArtifacts` is `false`, so no key is needed to build.

To turn it on (stage C2):
1. `npx tauri signer generate -w "$env:USERPROFILE\.tauri\vidgrab-updater.key"`
   (separate from any code-signing certificate; keep the private key and its
   password in the team secret store — if lost, installed clients can never be
   updated again).
2. In `tauri.conf.json` set `bundle.createUpdaterArtifacts: true` and add
   ```json
   "plugins": { "updater": {
     "pubkey": "<content of vidgrab-updater.key.pub>",
     "endpoints": ["https://dvid-api.vibe1.tinhgon.xyz/api/v1/client/update/{{target}}/{{arch}}/{{current_version}}"],
     "windows": { "installMode": "passive" } } }
   ```
   The endpoint does not exist on the server yet; it must return Tauri's update
   JSON (`version`, `url`, `signature`, optional `notes`, `pub_date`) or 204.
3. Build with `--features updater` and the key in the environment:
   `$env:TAURI_SIGNING_PRIVATE_KEY = Get-Content -Raw "$env:USERPROFILE\.tauri\vidgrab-updater.key"`.

## 5. Build the NSIS installer

```powershell
cd desktop
npm run tauri build -- --bundles nsis
```

`beforeBuildCommand` runs `npm run build` first (tsc + vite → `desktop/dist`).

Artifacts (native build without `--target`):

```
desktop\src-tauri\target\release\bundle\nsis\VidGrab_0.1.0_x64-setup.exe
desktop\src-tauri\target\release\bundle\nsis\VidGrab_0.1.0_x64-setup.exe.sig   (updater signature)
desktop\src-tauri\target\release\vidgrab-desktop.exe                           (unbundled app; Cargo binary name)
```

If you build with `--target x86_64-pc-windows-msvc`, the path becomes
`target\x86_64-pc-windows-msvc\release\bundle\nsis\`.

Publish the SHA256 alongside the installer:

```powershell
Get-FileHash -Algorithm SHA256 src-tauri\target\release\bundle\nsis\VidGrab_0.1.0_x64-setup.exe
```

The installer is per-user (`installMode: currentUser`). It installs under
`%LOCALAPPDATA%` (expected `%LOCALAPPDATA%\VidGrab`) without an admin prompt. It is **unsigned** in C0,
so SmartScreen will warn; code signing starts in C2 (see `SIGNING-COSTS.md`).

### Development run

```powershell
cd desktop
npm run tauri dev
```

Tauri copies the sidecars next to the dev executable without the triple suffix
(`target\debug\yt-dlp.exe` and so on). The app resolves them from there, as in
the installed app.

### Cross-build from Linux (how the first C0 installer was made, 2026-10-06)

Tauri marks this experimental; a Windows host remains the reference build.
Ubuntu 24.04, Rust 1.99, cargo-xwin 0.23.1, makensis 3.09:
```bash
sudo apt-get install -y nsis lld llvm clang
rustup target add x86_64-pc-windows-msvc
cargo install --locked cargo-xwin      # downloads the MSVC CRT + Windows SDK on first use
cd desktop
npm ci
node scripts/fetch-binaries.mjs --target x86_64-pc-windows-msvc
npm run tauri -- build --runner cargo-xwin --target x86_64-pc-windows-msvc --bundles nsis
```
Output: `src-tauri/target/x86_64-pc-windows-msvc/release/bundle/nsis/VidGrab_0.1.0_x64-setup.exe`
(101 MiB; installer is unsigned — SmartScreen shows "Windows protected your PC",
choose More info → Run anyway). Linker warnings `LNK4099` (missing Microsoft
PDBs) are harmless. Checked after the build: the four bundled tools inside the
installer have the SHA256 values pinned in `binaries.lock.json`, and those
values are compiled into `vidgrab-desktop.exe`. The installer was NOT run.

## 6. C0 manual test checklist (clean Windows 10 and Windows 11)

Record the results in `C0-RESULTS.md`. Use a machine or VM that has never had
VidGrab, Python or ffmpeg installed.

**Install**
- [ ] Run `VidGrab_0.1.0_x64-setup.exe` as a standard (non-admin) user. Expect no UAC prompt. Expect the app in the default per-user folder (expected `%LOCALAPPDATA%\VidGrab\`; note the actual path) with `yt-dlp.exe`, `ffmpeg.exe`, `ffprobe.exe` and `deno.exe` next to the main exe.
- [ ] The app launches and shows the version (`v0.1.0`) next to the title.

**Download one TikTok URL**
- [ ] Click **Folder…** and choose an empty folder.
- [ ] Paste a public TikTok video URL, then click **Start**. The log shows `-- started job-1`, then yt-dlp `[download]` lines, then `-- finished (exit code 0)`.
- [ ] The video file is in the folder and plays.
- [ ] Enter `file:///C:/Windows/win.ini` and click Start. Expect `-- error: only http(s) URLs are allowed`.

**Cancel during merge → no stray ffmpeg, no locked file**
- [ ] Use a URL whose default format needs merging (separate video and audio streams), for example a long YouTube video. Click Start.
- [ ] Open Task Manager (Details tab). While the log shows `[Merger]` (or, failing that, while `[download]` is running), click **Cancel**. The log shows `-- cancelled`.
- [ ] Within about 2 seconds, there is no `ffmpeg.exe`, `yt-dlp.exe` or `deno.exe` in Task Manager. In PowerShell: `Get-Process yt-dlp,ffmpeg,deno -ErrorAction SilentlyContinue` returns nothing.
- [ ] In Explorer, rename and then delete the partial output files (`*.part`, `*.f*.mp4`, `*.temp.mp4`). Both must succeed without a "file is open in another program" error.
- [ ] Repeat, but close the app window mid-download instead of clicking Cancel. Again no `yt-dlp.exe` or `ffmpeg.exe` remains (the Job Object kills the tree when the app's handle closes).

**Tampered binary is refused**
- [ ] Close the app. Append one byte to the installed yt-dlp:
      `[IO.File]::AppendAllText("<install folder>\yt-dlp.exe", "x")`
- [ ] Start the app and click Start. Expect `-- error: yt-dlp failed the integrity check (expected …, found …); refusing to run it…`. In Task Manager, no `yt-dlp.exe` started.
- [ ] Repeat with `ffmpeg.exe`. The download is refused before yt-dlp starts.
- [ ] Reinstall to restore the binaries.

**Webview cannot run arbitrary commands**
- [ ] In a dev build, open devtools (`npm run tauri dev`, right-click → Inspect) and run `window.__TAURI_INTERNALS__.invoke('plugin:dialog|open', {})`. It must be rejected as not allowed. Also check that `plugin:shell|execute` does not exist.

## 7. Verification done while writing the scaffold (Linux workspace, 2026-10-06)

| Check | Command | Result |
|---|---|---|
| tauri.conf.json against the official schema (`https://schema.tauri.app/config/2`, `$id` 2.12.1) | ajv 8 in a scratch folder | valid. A deliberately wrong `installMode` was rejected. `plugins.*` is free-form in that schema, so the updater block was compared by hand with `tauri-plugin-updater` 2.13.1 `src/config.rs` instead. |
| Frontend type-check and build | `npm run build` (tsc 5.9.3 + vite) | passed |
| Rust, Windows target | `cargo check --target x86_64-pc-windows-msvc --all-targets` on a copy **without the updater plugin**, with a stub `llvm-rc` that only creates an empty resource file | passed, 0 warnings. This checked the Job Object code against windows-sys 0.61, the pins generated by build.rs and the generated `allow-*` command permissions. Not linked, not run. With the updater left in, the check stops at `ring`, which needs the MSVC `lib.exe`. |
| Rust, Linux host | `cargo check` | **failed**: `gobject-2.0` (GTK/WebKitGTK dev packages) is not installed in the workspace |
| Unix process-group kill, checksum check, input validators | scratch crate that `#[path]`-includes `proc.rs`, `checksum.rs` and `validate.rs`; `cargo test` | 4/4 passed. The negative control (killing only the direct child) left the grandchild alive, so the test does catch the bug. |
| fetch-binaries | `node scripts/fetch-binaries.mjs` (Linux target), `--target x86_64-pc-windows-msvc` | refused (exit 2) and listed every `REPLACE_ME` |
| fetch-binaries, real yt-dlp | `--target x86_64-pc-windows-msvc --only yt-dlp` | downloaded yt-dlp.exe; hash matched `SHA2-256SUMS` |
| fetch-binaries, zip path | local zip served on 127.0.0.1 | correct hash → extracted. Wrong archive hash → refused. Wrong binary hash → refused. `--record-extracted` → filled the lock file |

**Not verified:** an actual Windows build, NSIS packaging, running the app,
Job Object behaviour at runtime, WebView2 bootstrapper, and updater
behaviour. The section 6 checklist covers these on real machines.

## 8. Documentation used (fetched 2026-10-06)

- Config reference: https://v2.tauri.app/reference/config/ and the JSON schema https://schema.tauri.app/config/2
- Sidecars / externalBin: https://v2.tauri.app/develop/sidecar/
- Updater plugin: https://v2.tauri.app/plugin/updater/ (plus crate source `tauri-plugin-updater` 2.13.1)
- Windows installer / NSIS / WebView2: https://v2.tauri.app/distribute/windows-installer/
- Prerequisites: https://v2.tauri.app/start/prerequisites/
- yt-dlp options (`--js-runtimes`, `--ignore-config`, `--no-plugin-dirs`, `--ffmpeg-location`): README at tag 2026.08.19

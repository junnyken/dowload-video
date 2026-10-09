# In-app update ("Cập nhật ngay") — key, build, release, rotation (task #6205)

**Tóm tắt (VI).** Từ 0.11.0 app Windows tự cập nhật: Cài đặt → Thông tin phiên
bản → **Cập nhật ngay** → app tải bộ cài của GitHub Release mới nhất, **kiểm chữ
ký** bằng khoá công khai gắn trong app, tạm dừng lượt tải đang chạy, chạy bộ cài
ở chế độ không hỏi (passive) rồi tự mở lại. Mỗi bản phát hành **phải** đính kèm
cả `VidGrab_<v>_x64-setup.exe` **và** `VidGrab_<v>_x64-setup.exe.sig`; thiếu
`.sig` thì app chỉ hiện link tải tay như cũ. **Mất khoá riêng = mọi máy đang cài
phải tự tải bản mới bằng tay một lần** (không có cách nào khác) — giữ 2 bản sao.

## 1. Pieces

| Piece | Where |
|---|---|
| Private key (minisign, password-protected) | `~/.config/vidgrab-updater.key` (chmod 600) on the build workspace — **secret** |
| Its password | `~/.config/vidgrab-updater.key.password` (chmod 600) — **secret** |
| Public key | `~/.config/vidgrab-updater.key.pub`, and committed in `desktop/src-tauri/tauri.conf.json` → `plugins.updater.pubkey` (key id `F84D51CB22023463`) |
| Endpoint the app asks | `https://dvid-api.vibe1.tinhgon.xyz/api/v1/client/update/{{target}}/{{arch}}/{{current_version}}` (`plugins.updater.endpoints`, same host as `src/lib/config.ts` `API_BASE`) |
| Server side | `backend/app/core/desktop_release.py` `update_offer()` + route in `backend/app/api/client_api.py` |
| App side | `desktop/src-tauri/src/app_update.rs` (commands `update_check`, `update_install`), Settings screen |

This key is **not** the Windows code-signing certificate (SIGNING-COSTS.md) and
costs nothing. It only proves to an installed app that an update was built by
us. SmartScreen still warns on the unsigned installer itself.

**Back up both secret files now** (owner's password manager + one offline
copy). They are not in git and nowhere else.

## 2. What happens when the user clicks "Cập nhật ngay"

1. On opening Settings (and on "Kiểm tra bản mới") Rust calls the endpoint.
   The server answers **204** (nothing) or **200**:
   `{"version","notes","pub_date","url","signature"}` where `url` is the latest
   GitHub Release asset `VidGrab_<v>_x64-setup.exe` and `signature` is the
   **content** of `VidGrab_<v>_x64-setup.exe.sig` from the same release.
2. The server answers 204 when: target/arch is not `windows`/`x86_64`; the
   caller is already on that version or newer; the release has no `.sig` or
   the `.sig` is not a minisign signature; the asset is not under
   `https://github.com/<DESKTOP_RELEASE_REPO>/releases/download/` with the exact
   name; `DESKTOP_UPDATER_ENABLED=0`; `DESKTOP_RELEASE_SOURCE=env`. The env
   floor `DESKTOP_LATEST_VERSION` is **not** used (no signed installer for it).
   The `.sig` is fetched once per release cache period (10 min; 2 min after a
   failure).
3. Click → Rust downloads the installer (progress %), verifies the signature
   with the built-in public key (a wrong / missing signature → "Bản cập nhật
   tải về không đúng chữ ký…", nothing is installed), pauses + kills running
   downloads (as "Thoát"), starts the NSIS installer with `/P /UPDATE /R`
   (passive: progress bar only, no wizard; same per-user folder; app data in
   `%APPDATA%\xyz.tinhgon.vidgrab` is untouched) and exits; the installer
   restarts VidGrab.
4. Any failure → Vietnamese message + the manual link. The webview never gets
   `updater:*` or `process:*` permissions; it can only call the two commands.

The repo comes from `DESKTOP_RELEASE_REPO` (default `junnyken/dowload-video`).
When releases move to a public `junnyken/vidgrab-releases`, set that env on the
backend and redeploy; nothing in the app changes (the app talks only to our API;
the download itself follows GitHub's redirect).

## 3. Build (release)

Feature `updater` is **on by default** (`Cargo.toml` `default = ["updater"]`),
so the normal build command includes it. `bundle.createUpdaterArtifacts: true`
makes the NSIS build also write `…_x64-setup.exe.sig`, and **the build fails
without the private key** — you cannot ship an update by accident unsigned.

```bash
cd desktop
export VIDGRAB_CLAIM_PUBKEYS='k1:…'                       # P3-KEY-ROTATION.md
export TAURI_SIGNING_PRIVATE_KEY="$(cat ~/.config/vidgrab-updater.key)"
export TAURI_SIGNING_PRIVATE_KEY_PASSWORD="$(cat ~/.config/vidgrab-updater.key.password)"
npm run tauri -- build --runner cargo-xwin --target x86_64-pc-windows-msvc --bundles nsis
# → src-tauri/target/x86_64-pc-windows-msvc/release/bundle/nsis/
#     VidGrab_<v>_x64-setup.exe  +  VidGrab_<v>_x64-setup.exe.sig
```

(On Windows use `$env:TAURI_SIGNING_PRIVATE_KEY = Get-Content … -Raw`.) A build
without the updater: `--no-default-features` (and `createUpdaterArtifacts:
false`); that app shows only the manual link.

Check a `.sig` before publishing (prints "verified" or fails):
`node scripts/verify-update-sig.mjs <installer.exe> <installer.exe.sig>` — it
uses the pubkey from tauri.conf.json.

## 4. Release steps (owner)

1. Bump the version in `desktop/package.json`, `src-tauri/tauri.conf.json`,
   `src-tauri/Cargo.toml` (+ `Cargo.lock`), build as above.
2. Create the GitHub Release `v<version>` (not draft, not pre-release, "latest")
   in `DESKTOP_RELEASE_REPO` and upload **both**
   `VidGrab_<v>_x64-setup.exe` **and** `VidGrab_<v>_x64-setup.exe.sig`
   (exact names; do not rename). Bullet lines of the release text become the
   update notes.
3. Within ~10 minutes `/client/version` shows the new version and installed
   apps ≥ 0.11.0 offer "Cập nhật ngay". Check:
   `curl -i https://dvid-api.vibe1.tinhgon.xyz/api/v1/client/update/windows/x86_64/0.11.0`
   → 200 with a `signature`.

Apps **older than 0.11.0 cannot update themselves**: they keep showing the
manual link (they have no updater). Everyone on 0.10.x must install 0.11.0 by
hand once.

**Pull a bad release:** set `DESKTOP_UPDATER_ENABLED=0` on the backend and
redeploy (apps stop being offered the update; the manual link stays), or delete
the `.sig` asset from the GitHub Release (→ 204 after the cache period).

## 5. Losing or leaking the key; rotation

* **Lost** private key or password: no further signed update can reach
  installed apps. Generate a new pair, put the new pubkey in tauri.conf.json,
  release, and tell users to **reinstall manually once** (download page). There
  is no recovery.
* **Leaked** private key: someone who also controls the endpoint answer or the
  GitHub Release could push code to every install. Rotate at once (below) and
  remove the old key from all copies.
* **Planned rotation** (no user action): release N is still signed with the OLD
  key but already carries the NEW pubkey in tauri.conf.json. Once installs are
  on N, release N+1 is signed with the NEW key. (An app trusts exactly one
  pubkey — the one it was built with — so the switch must happen in two
  releases.)

Generate a pair (prints only the public key; file names as in §1):

```bash
cd desktop
openssl rand -base64 32 | tr -d '\n' > ~/.config/vidgrab-updater.key.password
chmod 600 ~/.config/vidgrab-updater.key.password
./node_modules/.bin/tauri signer generate --ci -w ~/.config/vidgrab-updater.key \
  -p "$(cat ~/.config/vidgrab-updater.key.password)" > /dev/null
chmod 600 ~/.config/vidgrab-updater.key ~/.config/vidgrab-updater.key.pub
cat ~/.config/vidgrab-updater.key.pub
```

## 6. Not verified yet

* A real update on Windows end to end (0.11.0 → 0.11.1): it needs a second
  signed release. Test it with the first 0.11.1: install 0.11.0, publish 0.11.1
  with its `.sig`, Settings → "Cập nhật ngay" → the app must reopen as 0.11.1
  with history, settings, saved platform accounts and the paused queue intact.
* Running downloads are paused before the installer starts; if the installer
  then cannot start they stay paused (resume by hand).
* An app started by "start with Windows" (`--minimized`) is restarted with the
  same argument, so it comes back in the tray.
* `plugins.updater.requireSignedVersion` is left OFF. With it on, the app also
  refuses a `.sig` whose signed `version:` differs from the announced version
  (stops an endpoint from pairing a new version number with an older signed
  installer). `tauri signer sign --app-version` writes that field; turn the flag
  on once a real build's `.sig` is confirmed to carry `version:<v>`
  (`node scripts/verify-update-sig.mjs <exe> <exe.sig> <v>` checks it) —
  otherwise every update would be refused.

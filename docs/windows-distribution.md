# Flowboard Windows Distribution

## Release Shape

Build output lives in `dist/`:

- `Flowboard.exe`: starts the local FastAPI agent, serves the built React UI, opens it in a desktop WebView window, and requires a license in packaged mode.
- `update.exe`: downloads the latest `flowboard-windows.zip` from GitHub Releases and replaces the local files.
- `extension/`: Chrome MV3 extension folder loaded automatically when Flowboard opens a managed Chrome profile.
- `update.json`: updater config with `repo` and `asset`.
- `flowboard-windows.zip`: release asset to upload to GitHub Releases.

The Chrome extension is intentionally shipped as a visible folder. Chrome cannot load an unpacked extension from inside a one-file exe bundle, so `Flowboard.exe` keeps `extension/` next to the executable and launches Chrome with `--load-extension`.

## License Sheet

The default license source is the published TSV Google Sheet configured in `FLOWBOARD_LICENSE_SHEET_URL`.

Expected columns:

| Key | HWID | Status | Expiry |
| --- | --- | --- | --- |
| user key | machine HWID | `active` | blank or date |

Rules:

- `Status` must be `active`.
- `HWID` must match the machine code shown in the app.
- `Expiry` may be blank, `YYYY-MM-DD`, `DD/MM/YYYY`, or an Excel serial date.
- The app caches a successful activation in `%USERPROFILE%\.flowboard\license.json` and allows a short offline grace window.

Admin flow:

1. User opens `Flowboard.exe`.
2. User copies the displayed HWID and sends it to admin.
3. Admin adds or updates the row in the license sheet: `Key`, `HWID`, `active`, optional `Expiry`.
4. User enters the key in Flowboard.

## Build

From repo root:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 -Console
```

For a smoke build without PyArmor:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 -Console -SkipPyArmor
```

To point `update.exe` at your own GitHub repo:

```powershell
$env:FLOWBOARD_UPDATE_REPO = "owner/repo"
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
```

Encrypted builds require a real PyArmor license. The free trial can fail on large modules with `out of license`; when that happens, activate PyArmor first, then rerun the build without `-SkipPyArmor`.

## GitHub Release

1. Push source to GitHub.
2. Create a tag, for example `v1.2.13`.
3. Create a GitHub Release for that tag.
4. Upload `dist/flowboard-windows.zip` as a release asset.
5. Users run `update.exe`; it reads `update.json`, checks the latest release, downloads `flowboard-windows.zip`, and applies it.

## User Install

1. Extract `flowboard-windows.zip` to a folder such as `%LOCALAPPDATA%\FlowboardApp`.
2. Run `Flowboard.exe`.
3. Copy HWID, receive key from admin, activate.
4. In Account Manager, click **Open Flow profile**. Flowboard launches a dedicated Chrome profile with the bundled extension already loaded.
5. Sign in to Google Flow once in that profile. Later launches reuse the saved profile cookies.
6. Later updates: close Flowboard, run `update.exe`, then open `Flowboard.exe` again.

## Current External Requirements

The exe packages Python dependencies and the static frontend. End users do not need Python, Node, npm, uv, or pip for Flowboard itself.

For OpenAI Codex, Flowboard can bootstrap a private runtime from Settings: it checks for `npm`, downloads portable Node if needed, then installs `@openai/codex` under Flowboard's storage/tools directory. Users still need to sign in to Codex once if they choose the Codex CLI path; the OpenAI API-key fallback remains available.

These are still external:

- Microsoft Edge WebView2 runtime for the desktop shell. Most Windows 10/11 machines already have it; Flowboard falls back to the default browser if WebView is unavailable.
- Chrome/Chromium/Edge installed on the machine.
- An authenticated Google Flow session in the Flowboard-managed Chrome profile.
- LLM CLIs configured by the user if they use auto-prompt/vision/planner.
- `ffmpeg` and Demucs-related runtime if using scenario audio separation/export paths.

## Security Notes

This license gate deters casual sharing but is not a hardened licensing server. A public Google Sheet can be copied or patched around by a motivated reverse engineer. For stronger control later, replace the TSV sheet with a small signed license API and verify a server signature in the app.

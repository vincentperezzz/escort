# Escort overlay (Electron)

The in-meeting overlay as a small Electron app. It opens a truly transparent,
frameless, always-on-top window that loads `<base>/#/overlay` from the local
Escort service and mirrors `GET /api/overlay` onto the native window:

| field             | window effect                                                         |
| ----------------- | --------------------------------------------------------------------- |
| `visible`         | `showInactive()` / `hide()`; never steals focus                        |
| `collapsed`       | 440x88 (pill only) or 440x560, keeping the top-left corner             |
| `opacity`         | `win.setOpacity()` (0.3 to 1, rounded to 0.01)                         |
| `hide_from_share` | `win.setContentProtection()` (WDA_EXCLUDEFROMCAPTURE), re-applied on show |
| `input_focus`     | focusable and focused while typing; otherwise not focusable           |

Transparent pixels pass clicks through to the desktop: the page marks its glass
pieces with `data-hit`, and `window.escortOverlay.setIgnoreMouse()` (from
`preload.js`) flips `setIgnoreMouseEvents(…, { forward: true })` as the pointer
moves on and off them. `Ctrl+Shift+E` toggles visibility by POSTing to
`/api/overlay`. The app quits when the backend is unreachable for about 10s or
when `--parent-pid` exits. Logs go to `%APPDATA%\Escort\logs\overlay.log`.

## Run in development

Requires Node 20+. The Escort backend must be running and serving the built UI
(`npm run build` in `desktop/ui`).

```powershell
cd overlay
npm install                      # also downloads the Electron binary
python -m escort.desktop --web --port 8750   # in another terminal
npm start                        # = electron . --url=http://127.0.0.1:8750
```

Arguments: `--url=<base>` (default `http://127.0.0.1:8750`) and optional
`--parent-pid=<pid>`. Use the `=` form: Electron 45 on Windows exits at
startup (code -1, before `main.js` runs) when a URL value and a numeric value
are both passed as separate tokens, e.g. `--url http://… --parent-pid 123`. Show it with `POST /api/overlay {"visible": true}` or
`Ctrl+Shift+E`. Only one instance runs at a time.

## Package

```powershell
cd overlay
npm run package
```

Runs `package.js` (@electron/packager) and produces
`out/EscortOverlay-win32-x64/EscortOverlay.exe`. The asar holds only
`main.js`, `preload.js` and `package.json`; Chromium locales other than en-US
are removed. Launch it as
`EscortOverlay.exe --url=http://127.0.0.1:8750 --parent-pid=<escort pid>`.

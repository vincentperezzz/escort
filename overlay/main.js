"use strict";

// Escort overlay: a transparent, frameless, always-on-top window that renders
// <base>/#/overlay from the local Escort service and mirrors GET /api/overlay
// (visible, collapsed, opacity, hide_from_share, input_focus) onto the window.

const { app, BrowserWindow, globalShortcut, ipcMain, screen } = require("electron");
const fs = require("node:fs");
const path = require("node:path");

const WIDTH = 440;
const OPEN_HEIGHT = 560;
const COLLAPSED_HEIGHT = 88;
const POLL_MS = 300;
const MAX_BACKOFF_MS = 2000;
const OFFLINE_QUIT_MS = 10000;
const PARENT_CHECK_MS = 2000;
const HOTKEY = "CommandOrControl+Shift+E";
// After the page applies a value itself (slider drag, collapse click), the
// backend may still report the old one for a poll or two; trust the page meanwhile.
const LOCAL_OPACITY_HOLD_MS = 900;
const LOCAL_COLLAPSE_HOLD_MS = 1500;

// --- logging ----------------------------------------------------------------

const LOG_DIR = path.join(process.env.APPDATA || app.getPath("appData"), "Escort", "logs");
const LOG_FILE = path.join(LOG_DIR, "overlay.log");

function log(...parts) {
  const line = `${new Date().toISOString()} ${parts
    .map((p) => (p instanceof Error ? p.stack || p.message : typeof p === "string" ? p : JSON.stringify(p)))
    .join(" ")}\n`;
  try {
    fs.mkdirSync(LOG_DIR, { recursive: true });
    try {
      if (fs.statSync(LOG_FILE).size > 1024 * 1024) fs.renameSync(LOG_FILE, `${LOG_FILE}.1`);
    } catch {
      /* no log yet */
    }
    fs.appendFileSync(LOG_FILE, line);
  } catch {
    /* logging must never take the overlay down */
  }
}

process.on("uncaughtException", (err) => log("uncaughtException", err));
process.on("unhandledRejection", (err) => log("unhandledRejection", err instanceof Error ? err : String(err)));

// --- arguments ----------------------------------------------------------------

function argValue(name) {
  const argv = process.argv;
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === name && i + 1 < argv.length) return argv[i + 1];
    if (argv[i].startsWith(`${name}=`)) return argv[i].slice(name.length + 1);
  }
  return null;
}

const BASE = (argValue("--url") || "http://127.0.0.1:8750").replace(/\/+$/, "");
const PARENT_PID = Number.parseInt(argValue("--parent-pid") || "", 10) || null;
const OVERLAY_URL = `${BASE}/#/overlay`;
const API = `${BASE}/api/overlay`;

// --- single instance ----------------------------------------------------------

if (!app.requestSingleInstanceLock()) {
  log("Another overlay is already running; exiting.");
  app.quit();
} else {
  app.on("second-instance", () => log("Second instance ignored."));
  app.whenReady().then(start).catch((err) => {
    log("Startup failed", err);
    app.quit();
  });
}

app.on("window-all-closed", () => app.quit());
app.on("will-quit", () => {
  try {
    globalShortcut.unregisterAll();
  } catch {
    /* app is going away */
  }
});

// --- window -------------------------------------------------------------------

let win = null;
let quitting = false;
let savedPos = null; // where the user dragged it, for this session
let lastState = null; // last good GET /api/overlay
const applied = { visible: undefined, collapsed: undefined, opacity: undefined, share: undefined, focus: undefined };
let ignoringMouse = null;
let localOpacityUntil = 0;
let localCollapseUntil = 0;

function quit(reason) {
  if (quitting) return;
  quitting = true;
  log(`Quitting: ${reason}`);
  app.quit();
}

function defaultPosition() {
  const area = screen.getPrimaryDisplay().workArea;
  return { x: area.x + Math.round((area.width - WIDTH) / 2), y: area.y + 8 };
}

/** Keep the window on some display; a remembered spot can vanish when a monitor unplugs. */
function onScreen(pos) {
  const area = screen.getDisplayMatching({ x: pos.x, y: pos.y, width: WIDTH, height: COLLAPSED_HEIGHT }).workArea;
  const x = Math.min(Math.max(pos.x, area.x - WIDTH + 80), area.x + area.width - 80);
  const y = Math.min(Math.max(pos.y, area.y), area.y + area.height - 40);
  return { x, y };
}

function createWindow() {
  const pos = defaultPosition();
  win = new BrowserWindow({
    x: pos.x,
    y: pos.y,
    width: WIDTH,
    height: OPEN_HEIGHT,
    show: false,
    frame: false,
    transparent: true,
    backgroundColor: "#00000000",
    hasShadow: false,
    thickFrame: false,
    resizable: false,
    maximizable: false,
    minimizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    focusable: false,
    title: "Escort overlay",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      backgroundThrottling: false,
      spellcheck: false,
    },
  });
  win.setAlwaysOnTop(true, "screen-saver");
  win.setMenu(null);
  setIgnoreMouse(true);

  win.on("moved", () => {
    if (!win || win.isDestroyed()) return;
    const [x, y] = win.getPosition();
    savedPos = { x, y };
  });
  win.on("show", reapplyShare);
  win.on("closed", () => {
    win = null;
    quit("window closed");
  });

  const wc = win.webContents;
  wc.setWindowOpenHandler(() => ({ action: "deny" }));
  wc.on("will-navigate", (event, url) => {
    if (!url.startsWith(BASE)) event.preventDefault();
  });
  wc.on("did-fail-load", (_e, code, desc, url, isMainFrame) => {
    if (!isMainFrame || code === -3) return; // -3: aborted by a newer navigation
    log(`Load failed (${code} ${desc}) for ${url}; retrying.`);
    setTimeout(() => win && !win.isDestroyed() && win.loadURL(OVERLAY_URL).catch(() => {}), 1000);
  });
  wc.on("render-process-gone", (_e, details) => {
    log("Renderer gone", details);
    setTimeout(() => win && !win.isDestroyed() && win.loadURL(OVERLAY_URL).catch(() => {}), 500);
  });
  wc.on("console-message", (event) => {
    if (event.level === "error") log(`page: ${event.message}`);
  });
  win.loadURL(OVERLAY_URL).catch((err) => log("loadURL", err));
}

function alive() {
  return win && !win.isDestroyed();
}

function setIgnoreMouse(ignore) {
  if (!alive() || ignoringMouse === ignore) return;
  ignoringMouse = ignore;
  win.setIgnoreMouseEvents(ignore, { forward: true });
}

function reapplyMouse() {
  if (alive() && ignoringMouse !== null) win.setIgnoreMouseEvents(ignoringMouse, { forward: true });
}

/** Content protection (WDA_EXCLUDEFROMCAPTURE) is re-asserted on every show. */
function reapplyShare() {
  if (alive() && applied.share !== undefined) win.setContentProtection(applied.share);
}

function setShare(on) {
  if (applied.share === on) return;
  applied.share = on;
  if (alive()) win.setContentProtection(on);
  log(`hide_from_share=${on}`);
}

function setCollapsed(on) {
  if (applied.collapsed === on || !alive()) return;
  applied.collapsed = on;
  const [x, y] = win.getPosition();
  win.setBounds({ x, y, width: WIDTH, height: on ? COLLAPSED_HEIGHT : OPEN_HEIGHT }, false);
}

function setOpacity(value) {
  if (!Number.isFinite(value) || !alive()) return;
  const rounded = Math.round(Math.min(1, Math.max(0.3, value)) * 100) / 100;
  if (applied.opacity === rounded) return;
  applied.opacity = rounded;
  win.setOpacity(rounded);
}

/** Show without activating; window styles are re-asserted because a show can reset them. */
function showInactive() {
  reapplyShare();
  win.showInactive();
  win.setAlwaysOnTop(true, "screen-saver");
  reapplyMouse();
}

function setVisible(on) {
  if (applied.visible === on || !alive()) return;
  applied.visible = on;
  if (on) {
    const pos = onScreen(savedPos || defaultPosition());
    const [x, y] = win.getPosition();
    if (x !== pos.x || y !== pos.y) win.setPosition(pos.x, pos.y, false);
    showInactive();
  } else {
    releaseFocus();
    win.hide();
  }
}

function takeFocus() {
  if (!alive()) return;
  applied.focus = true;
  win.setFocusable(true);
  reapplyMouse();
  win.focus();
  win.webContents.focus();
  log(`Focus taken (focused: ${win.isFocused()}).`);
}

/** Give the keyboard back to whatever app the user was in before typing here. */
function releaseFocus() {
  if (!alive()) return;
  applied.focus = false;
  const had = win.isFocused();
  if (had) win.blur();
  win.setFocusable(false);
  if (had) log(`Focus released (still focused after blur: ${win.isFocused()}).`);
  if (had && win.isFocused() && win.isVisible()) {
    // blur() found nothing to activate: hiding hands activation to the next
    // window in z-order, then the overlay comes back without taking it.
    win.hide();
    showInactive();
  }
  reapplyMouse();
}

function setInputFocus(on) {
  if (applied.focus === on) return;
  if (on) takeFocus();
  else releaseFocus();
}

function applyState(state) {
  if (!state || typeof state !== "object") return;
  const now = Date.now();
  if (typeof state.hide_from_share === "boolean") setShare(state.hide_from_share);
  if (typeof state.collapsed === "boolean" && now >= localCollapseUntil) setCollapsed(state.collapsed);
  if (typeof state.opacity === "number" && now >= localOpacityUntil) setOpacity(state.opacity);
  if (typeof state.visible === "boolean") setVisible(state.visible);
  if (typeof state.input_focus === "boolean" && applied.visible) setInputFocus(state.input_focus);
}

// --- backend polling ------------------------------------------------------------

async function request(method, body) {
  const res = await fetch(API, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(2500),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

function startPolling() {
  let lastOk = Date.now();
  let delay = POLL_MS;
  let wasDown = false;
  const tick = async () => {
    if (quitting) return;
    try {
      const state = await request("GET");
      lastState = state;
      lastOk = Date.now();
      delay = POLL_MS;
      if (wasDown) log("Backend reachable again.");
      wasDown = false;
      applyState(state);
    } catch (err) {
      if (!wasDown) log(`Backend unreachable: ${err.message}`);
      wasDown = true;
      delay = Math.min(delay * 2, MAX_BACKOFF_MS);
      if (Date.now() - lastOk > OFFLINE_QUIT_MS) return quit(`backend ${BASE} unreachable for ${OFFLINE_QUIT_MS / 1000}s`);
    }
    setTimeout(tick, delay);
  };
  tick();
}

function watchParent() {
  if (!PARENT_PID) return;
  setInterval(() => {
    try {
      process.kill(PARENT_PID, 0);
    } catch (err) {
      if (err.code !== "EPERM") quit(`parent process ${PARENT_PID} is gone`);
    }
  }, PARENT_CHECK_MS);
}

function registerHotkey() {
  const ok = globalShortcut.register(HOTKEY, async () => {
    try {
      const current = lastState || (await request("GET"));
      const next = await request("POST", { visible: !current.visible });
      lastState = next;
      applyState(next);
    } catch (err) {
      log(`Hotkey toggle failed: ${err.message}`);
    }
  });
  log(ok ? `Hotkey ${HOTKEY} registered.` : `Hotkey ${HOTKEY} is taken by another app.`);
}

// --- page bridge -------------------------------------------------------------------

function fromOverlay(event) {
  return alive() && event.sender === win.webContents;
}

function registerIpc() {
  ipcMain.on("overlay:ignore-mouse", (event, ignore) => {
    if (fromOverlay(event)) setIgnoreMouse(Boolean(ignore));
  });
  ipcMain.on("overlay:focus-input", (event) => {
    if (fromOverlay(event) && !(applied.focus && win.isFocused())) takeFocus();
  });
  ipcMain.on("overlay:release-focus", (event) => {
    if (fromOverlay(event)) releaseFocus();
  });
  ipcMain.on("overlay:opacity", (event, value) => {
    if (!fromOverlay(event)) return;
    localOpacityUntil = Date.now() + LOCAL_OPACITY_HOLD_MS;
    setOpacity(Number(value));
  });
  let dragFrom = null;
  ipcMain.on("overlay:drag", (event, phase, dx, dy) => {
    if (!fromOverlay(event)) return;
    if (phase === "start") {
      const [x, y] = win.getPosition();
      dragFrom = { x, y };
    } else if (phase === "move" && dragFrom && Number.isFinite(dx) && Number.isFinite(dy)) {
      win.setPosition(Math.round(dragFrom.x + dx), Math.round(dragFrom.y + dy), false);
    } else if (phase === "end" && dragFrom) {
      dragFrom = null;
      const [x, y] = win.getPosition();
      savedPos = { x, y };
    }
  });
  ipcMain.on("overlay:collapsed", (event, on) => {
    if (!fromOverlay(event)) return;
    localCollapseUntil = Date.now() + LOCAL_COLLAPSE_HOLD_MS;
    setCollapsed(Boolean(on));
  });
}

function start() {
  log(`Overlay starting: url=${BASE} parent=${PARENT_PID || "-"} electron=${process.versions.electron}`);
  registerIpc();
  createWindow();
  registerHotkey();
  watchParent();
  startPolling();
  screen.on("display-removed", () => {
    if (!alive() || !win.isVisible()) return;
    const [x, y] = win.getPosition();
    const pos = onScreen({ x, y });
    if (pos.x !== x || pos.y !== y) win.setPosition(pos.x, pos.y, false);
  });
}

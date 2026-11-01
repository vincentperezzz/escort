"use strict";

// The only bridge the overlay page gets: window.escortOverlay. The page checks
// for it, so the same #/overlay route still works in a plain browser.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("escortOverlay", {
  /** true: clicks pass through to the desktop beneath; false: the page takes them. */
  setIgnoreMouse: (ignore) => ipcRenderer.send("overlay:ignore-mouse", Boolean(ignore)),
  /** Make the window focusable and focus it, so the ask box can take keystrokes. */
  focusInput: () => ipcRenderer.send("overlay:focus-input"),
  /** Hand the keyboard back to the previous app and stop taking focus. */
  releaseFocus: () => ipcRenderer.send("overlay:release-focus"),
  /** Apply opacity natively at once (the backend is still told separately). */
  setOpacity: (value) => ipcRenderer.send("overlay:opacity", Number(value)),
  /** Window drag driven by the page: start, move by a screen-space offset, end. */
  dragStart: () => ipcRenderer.send("overlay:drag", "start"),
  dragMove: (dx, dy) => ipcRenderer.send("overlay:drag", "move", Number(dx), Number(dy)),
  dragEnd: () => ipcRenderer.send("overlay:drag", "end"),
  /** Resize to the pill-only or full height at once. */
  setCollapsed: (on) => ipcRenderer.send("overlay:collapsed", Boolean(on)),
});

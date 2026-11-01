from __future__ import annotations

import argparse
import logging
import sys
import threading
from pathlib import Path

import uvicorn

from escort.desktop import win_overlay
from escort.desktop.api import create_desktop_app
from escort.desktop.hotkey import GlobalHotkey
from escort.desktop.overlay import create_overlay
from escort.desktop.runtime import DesktopRuntime
from escort.paths import repo_root
from escort.server import AppState, create_server
from escort.store import MeetingStore

log = logging.getLogger("escort.desktop")


def ui_dist() -> Path | None:
    frozen = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False) else None
    candidates = [
        frozen / "ui" if frozen else None,
        repo_root() / "desktop" / "ui" / "dist",
        Path(__file__).resolve().parent / "ui",
    ]
    for candidate in candidates:
        if candidate and (candidate / "index.html").exists():
            return candidate
    return None


def start_mcp(store: MeetingStore) -> None:
    state = AppState(store)
    state.pipeline = None
    app = create_server(state).streamable_http_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=8747, log_level="warning", log_config=None)
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, name="escort-mcp", daemon=True).start()


def start_whatsapp() -> None:
    from escort.whatsapp.app import create_app
    from escort.whatsapp.server import build_from_env

    app = create_app(build_from_env(), poll=True)
    config = uvicorn.Config(app, host="127.0.0.1", port=8748, log_level="warning", log_config=None)
    threading.Thread(target=uvicorn.Server(config).run, name="escort-whatsapp", daemon=True).start()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="escort desktop")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8750)
    parser.add_argument("--web", action="store_true")
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args(argv)
    store = MeetingStore(repo_root() / "var" / "desktop")
    runtime = DesktopRuntime(store)
    if args.demo:
        runtime.load_demo()
    try:
        start_mcp(store)
    except Exception as exc:
        store.warn(f"MCP did not start: {exc}")
    try:
        start_whatsapp()
    except Exception as exc:
        store.warn(f"WhatsApp webhook did not start: {exc}")
    app = create_desktop_app(runtime, ui_dist())
    url = f"http://{args.host}:{args.port}"
    if args.web:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning", log_config=None)
        return
    config = uvicorn.Config(app, host=args.host, port=args.port, log_level="warning", log_config=None)
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, name="escort-ui", daemon=True).start()
    _open_window(url, runtime)


class MainWindow:
    """Show/hide the main pywebview window; show also restores and raises it."""

    def __init__(self, window) -> None:
        self.window = window

    def hide(self) -> None:
        self.window.hide()

    def show(self) -> None:
        self.window.show()
        try:
            self.window.restore()
        except Exception:
            pass
        native = getattr(self.window, "native", None)
        if native is not None:
            try:
                win_overlay.focus_window(int(native.Handle.ToInt64()))
            except Exception as exc:
                log.debug("Could not raise the main window: %s", exc)


def _open_window(url: str, runtime: DesktopRuntime) -> None:
    try:
        import webview
    except Exception:
        print(url, flush=True)
        threading.Event().wait()
        return
    window = webview.create_window("Escort", url, width=1120, height=780, background_color="#efe6d6")
    overlay = create_overlay(webview, url)
    runtime.attach_overlay(overlay)
    runtime.attach_main_window(MainWindow(window))
    if overlay is not None:
        # pywebview only exits once every window is gone; the overlay must not keep it alive.
        window.events.closed += overlay.destroy
    icon = _tray(runtime, window)
    hotkey = _hotkey(runtime)
    try:
        webview.start()
    finally:
        runtime.shutdown()
        if hotkey is not None:
            hotkey.stop()
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass


def _hotkey(runtime: DesktopRuntime) -> GlobalHotkey | None:
    """Ctrl+Shift+E toggles the overlay. Never raises; None if unavailable."""
    try:
        hotkey = GlobalHotkey(runtime.toggle_overlay)
        return hotkey if hotkey.start() else None
    except Exception as exc:
        log.warning("Overlay hotkey unavailable: %s", exc)
        return None


def _tray(runtime: DesktopRuntime, window):
    """Start the tray icon. Never raises; returns the pystray icon or None."""
    try:
        import pystray

        from escort.desktop.logo import render_logo

        images = {live: render_logo(64, live=live) for live in (False, True)}

        def toggle(_icon, _item) -> None:
            runtime.set_listening(not runtime.listening)

        def show(_icon, _item) -> None:
            if runtime.main_window is not None:
                runtime.show_main_window()
            else:
                window.show()

        def toggle_overlay(_icon, _item) -> None:
            runtime.toggle_overlay()

        def toggle_share(_icon, _item) -> None:
            runtime.set_overlay(hide_from_share=not runtime.hide_from_share)

        def quit_app(icon, _item) -> None:
            icon.stop()
            window.destroy()

        menu = pystray.Menu(
            pystray.MenuItem("Show Escort", show, default=True),
            pystray.MenuItem("Start or stop listening", toggle),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Show/Hide overlay", toggle_overlay, checked=lambda _item: runtime.overlay_visible),
            pystray.MenuItem(
                "Hide from screen share",
                toggle_share,
                checked=lambda _item: runtime.hide_from_share,
                enabled=lambda _item: runtime.overlay_supported(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", quit_app),
        )
        icon = pystray.Icon("escort", images[runtime.listening], "Escort", menu)

        def refresh() -> None:
            icon.icon = images[runtime.listening]
            icon.title = "Escort - listening" if runtime.listening else "Escort"
            icon.update_menu()

        runtime.subscribe(refresh)
        threading.Thread(target=icon.run, name="escort-tray", daemon=True).start()
        return icon
    except Exception as exc:
        log.warning("Tray icon unavailable: %s", exc)
        return None

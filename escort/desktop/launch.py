from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from pathlib import Path

import uvicorn

from escort.desktop import crashlog, win_overlay
from escort.desktop.api import create_desktop_app
from escort.desktop.hotkey import GlobalHotkey
from escort.desktop.overlay import create_overlay
from escort.desktop.overlay_process import ElectronOverlay, start_electron_overlay
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


def _handle_protocol_url(runtime: DesktopRuntime, url: str) -> None:
    from escort.context.sources.notion_mcp import notion_oauth_callback_params

    params = notion_oauth_callback_params(url)
    if params is None:
        return
    runtime.connectors.notion_mcp.handle_callback(params)
    runtime.show_main_window()


def main(argv: list[str] | None = None) -> None:
    from escort.desktop.single_instance import SingleInstance, escort_protocol_urls, register_escort_protocol

    raw_argv = list(sys.argv[1:] if argv is None else argv)
    protocol_urls = escort_protocol_urls(raw_argv)
    register_escort_protocol()
    instance = SingleInstance()
    if not instance.try_acquire():
        for url in protocol_urls:
            instance.forward(url)
        return
    parser = argparse.ArgumentParser(prog="escort desktop")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8750)
    parser.add_argument("--web", action="store_true")
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args(raw_argv)
    crashlog.install()  # no-op when the frozen entry already did it
    store = MeetingStore(repo_root() / "var" / "desktop")
    runtime = DesktopRuntime(store)
    instance.start_listener(lambda url: _handle_protocol_url(runtime, url))
    for url in protocol_urls:
        _handle_protocol_url(runtime, url)
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
        try:
            uvicorn.run(app, host=args.host, port=args.port, log_level="warning", log_config=None)
        finally:
            instance.release()
        return
    config = uvicorn.Config(app, host=args.host, port=args.port, log_level="warning", log_config=None)
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, name="escort-ui", daemon=True).start()
    try:
        _open_window(url, runtime)
    finally:
        instance.release()


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


def _primary_work_area(webview_module=None):
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            rect = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
        except Exception:
            pass
    if webview_module is not None:
        try:
            screens = webview_module.screens
            if screens:
                screen = screens[0]
                if isinstance(screen, dict):
                    return screen.get("x", 0), screen.get("y", 0), screen.get("width", 1920), screen.get("height", 1080)
                return (
                    getattr(screen, "x", 0),
                    getattr(screen, "y", 0),
                    getattr(screen, "width", 1920),
                    getattr(screen, "height", 1080),
                )
        except Exception:
            pass
    return 0, 0, 1920, 1080


def _main_window_create_kwargs(webview_module):
    margin = 40
    want_w, want_h = 1280, 860
    work_left, work_top, work_w, work_h = _primary_work_area(webview_module)
    max_w = max(margin * 2, work_w - 2 * margin)
    max_h = max(margin * 2, work_h - 2 * margin)
    width = min(want_w, max_w)
    height = min(want_h, max_h)
    x = work_left + max(0, (work_w - width) // 2)
    y = work_top + max(0, (work_h - height) // 2)
    return {"width": width, "height": height, "x": x, "y": y, "min_size": (980, 660), "resizable": True}


def _open_window(url: str, runtime: DesktopRuntime) -> None:
    try:
        import webview
    except Exception:
        print(url, flush=True)
        threading.Event().wait()
        return
    window = webview.create_window("Escort", url, background_color="#efe6d6", **_main_window_create_kwargs(webview))
    runtime.attach_main_window(MainWindow(window))
    # Minimizing the main window mid-meeting brings the overlay back, so the call is never unwatched.
    minimized = getattr(getattr(window, "events", None), "minimized", None)
    if minimized is not None:
        window.events.minimized += lambda *_args: runtime.main_window_minimized()
    electron = _overlay(webview, window, url, runtime)
    icon = _tray(runtime, window)
    # EscortOverlay.exe registers Ctrl+Shift+E itself; a second registrant would lose it anyway.
    hotkey = None if electron is not None else _hotkey(runtime)
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


def overlay_url(url: str) -> str:
    """The URL the overlay process loads: loopback when the server binds every interface."""
    for wildcard in ("://0.0.0.0:", "://[::]:"):
        url = url.replace(wildcard, "://127.0.0.1:")
    return url


def _overlay(webview, window, url: str, runtime: DesktopRuntime) -> ElectronOverlay | None:
    """Prefer the Electron overlay; fall back to the in-process pywebview overlay.

    Returns the running ElectronOverlay, or None when the pywebview overlay (or no
    overlay at all) is in use.
    """
    electron = start_electron_overlay(overlay_url(url), os.getpid(), on_gone=runtime.overlay_process_lost)
    if electron is not None:
        runtime.attach_overlay_process(electron)
        return electron
    overlay = create_overlay(webview, url)
    runtime.attach_overlay(overlay)
    if overlay is not None:
        # pywebview only exits once every window is gone; the overlay must not keep it alive.
        window.events.closed += overlay.destroy
    return None


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

        refresh_lock = threading.Lock()
        shown: dict[str, bool] = {}

        def refresh() -> None:
            # Observers fire from many API worker threads at once (e.g. every opacity
            # step). pystray's setters destroy and replace native icon handles and are
            # not thread-safe ("Invalid cursor handle" under load), so serialize them
            # and only touch the icon when listening actually changed.
            with refresh_lock:
                listening = bool(runtime.listening)
                if shown.get("listening") != listening:
                    icon.icon = images[listening]
                    icon.title = "Escort - listening" if listening else "Escort"
                    shown["listening"] = listening
                icon.update_menu()

        runtime.subscribe(refresh)
        threading.Thread(target=icon.run, name="escort-tray", daemon=True).start()
        return icon
    except Exception as exc:
        log.warning("Tray icon unavailable: %s", exc)
        return None

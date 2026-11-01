from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

import uvicorn

from escort.desktop.api import create_desktop_app
from escort.desktop.runtime import DesktopRuntime
from escort.paths import repo_root
from escort.server import AppState, create_server
from escort.store import MeetingStore


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
    config = uvicorn.Config(app, host="127.0.0.1", port=8747, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, name="escort-mcp", daemon=True).start()


def start_whatsapp() -> None:
    from escort.whatsapp.app import create_app
    from escort.whatsapp.server import build_from_env

    app = create_app(build_from_env(), poll=True)
    config = uvicorn.Config(app, host="127.0.0.1", port=8748, log_level="warning")
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
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
        return
    config = uvicorn.Config(app, host=args.host, port=args.port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, name="escort-ui", daemon=True).start()
    _open_window(url, runtime)


def _open_window(url: str, runtime: DesktopRuntime) -> None:
    try:
        import webview
    except Exception:
        print(url, flush=True)
        threading.Event().wait()
        return
    window = webview.create_window("Escort", url, width=1120, height=780, background_color="#efe6d6")
    _tray(runtime, window)
    webview.start()


def _tray(runtime: DesktopRuntime, window) -> None:
    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception:
        return
    image = Image.new("RGB", (64, 64), "#1a140f")
    draw = ImageDraw.Draw(image)
    draw.ellipse((8, 8, 56, 56), fill="#c47b12")

    def toggle(_icon, _item) -> None:
        runtime.set_listening(not runtime.listening)

    def show(_icon, _item) -> None:
        window.show()

    def quit_app(icon, _item) -> None:
        icon.stop()
        window.destroy()

    menu = pystray.Menu(
        pystray.MenuItem("Show Escort", show),
        pystray.MenuItem("Start or stop listening", toggle),
        pystray.MenuItem("Quit", quit_app),
    )
    icon = pystray.Icon("escort", image, "Escort", menu)
    threading.Thread(target=icon.run, daemon=True).start()

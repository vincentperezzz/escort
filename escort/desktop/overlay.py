"""The in-meeting overlay window: a second, frameless, always-on-top pywebview window.

DesktopRuntime owns the overlay *state* (visible, hide_from_share) and pushes it
to a controller. OverlayWindow is the real controller; tests use a fake with the
same three members (supported, set_visible, set_hide_from_share).
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Protocol

from escort.desktop import win_overlay

log = logging.getLogger("escort.desktop.overlay")

WIDTH = 440
HEIGHT = 560
TOP_MARGIN = 24
TITLE = "Escort Overlay"


class OverlayController(Protocol):
    supported: bool

    def set_visible(self, visible: bool) -> None: ...

    def set_hide_from_share(self, hide: bool) -> None: ...


class OverlayWindow:
    """Drives a pywebview window, using Win32 directly where pywebview would steal focus."""

    def __init__(self, window: Any, home: tuple[int, int] | None = None) -> None:
        self.window = window
        self.home = home
        self.supported = win_overlay.capture_exclusion_supported()
        self.hwnd: int | None = None
        self._visible = False
        self._hide_from_share = True
        self._placed = False
        self._lock = threading.RLock()

    # pywebview lifecycle hooks (run on pywebview worker threads)
    def on_created(self) -> None:
        self.attach()

    def on_loaded(self) -> None:
        # Transparent WebView2 windows force themselves visible on every
        # navigation, so re-assert our state once the page has loaded.
        self.attach()

    def attach(self) -> bool:
        with self._lock:
            if win_overlay.is_windows():
                # Re-resolve every time: WinForms may recreate the handle.
                self.hwnd = self._resolve_hwnd() or self.hwnd
            self._apply_affinity()
            self._apply_visibility()
            return self.hwnd is not None

    def set_visible(self, visible: bool) -> None:
        with self._lock:
            self._visible = visible
            self._apply_visibility()

    def set_hide_from_share(self, hide: bool) -> None:
        with self._lock:
            self._hide_from_share = hide
            self._apply_affinity()

    def destroy(self) -> None:
        try:
            self.window.destroy()
        except Exception as exc:
            log.warning("Overlay window did not close cleanly: %s", exc)

    def _resolve_hwnd(self) -> int | None:
        try:
            native = getattr(self.window, "native", None)
            if native is None:
                return None
            return int(native.Handle.ToInt64())
        except Exception as exc:
            log.warning("Overlay window handle unavailable: %s", exc)
            return None

    def _apply_affinity(self) -> None:
        if not self.hwnd or not self.supported:
            return
        hwnd, hide = self.hwnd, self._hide_from_share
        self._on_gui(lambda: win_overlay.set_capture_excluded(hwnd, hide))

    def _apply_visibility(self) -> None:
        if self.hwnd:
            hwnd = self.hwnd
            if self._visible:
                place = not self._placed
                self._placed = True

                def show() -> None:
                    # WinForms resets extended styles from CreateParams whenever
                    # pywebview touches Opacity etc., so re-assert them each time.
                    win_overlay.make_tool_window(hwnd)
                    win_overlay.show_no_activate(hwnd, place_top_center=place, margin=TOP_MARGIN)

                self._on_gui(show)
            else:
                self._on_gui(lambda: (win_overlay.hide(hwnd), win_overlay.make_tool_window(hwnd)))
            return
        # Non-Windows or no handle yet: fall back to pywebview's own calls.
        try:
            if self._visible:
                if not self._placed and self.home:
                    self.window.move(*self.home)
                self._placed = True
                self.window.show()
            else:
                self.window.hide()
        except Exception as exc:
            log.warning("Overlay show/hide failed: %s", exc)

    def _on_gui(self, fn: Callable[[], Any]) -> None:
        """Run fn on the window's UI thread (some Win32 calls require the owner thread)."""
        native = getattr(self.window, "native", None)
        try:
            if native is not None and getattr(native, "InvokeRequired", False):
                from System import Func, Type  # type: ignore[import-not-found]

                def call():
                    fn()

                native.Invoke(Func[Type](call))
                return
        except Exception as exc:
            log.warning("Overlay UI-thread dispatch failed, calling directly: %s", exc)
        try:
            fn()
        except Exception as exc:
            log.warning("Overlay call failed: %s", exc)


def create_overlay(webview: Any, url: str) -> OverlayWindow | None:
    """Create the hidden overlay window. Returns None (and logs) on any failure."""
    try:
        if win_overlay.is_windows():
            # Park it off-screen: transparent WebView2 windows show themselves on
            # first navigation. We move it to top-center the first time it is shown.
            x, y, home = -32000, -32000, None
        else:
            x, y = _top_center(webview)
            home = (x, y) if x is not None else None
        window = webview.create_window(
            TITLE,
            f"{url}#/overlay",
            width=WIDTH,
            height=HEIGHT,
            x=x,
            y=y,
            frameless=True,
            easy_drag=True,
            resizable=False,
            on_top=True,
            transparent=True,
            shadow=False,
            hidden=True,
            focus=False,
            background_color="#000000",
        )
        controller = OverlayWindow(window, home=home)
        window.events.before_show += controller.on_created
        window.events.loaded += controller.on_loaded
        return controller
    except Exception as exc:
        log.warning("Overlay window was not created: %s", exc)
        return None


def _top_center(webview: Any) -> tuple[int | None, int | None]:
    try:
        screen = webview.screens[0]
        return int(screen.x + (screen.width - WIDTH) / 2), int(screen.y + TOP_MARGIN)
    except Exception:
        return None, None

"""The in-meeting overlay window: a second, frameless, always-on-top pywebview window.

DesktopRuntime owns the overlay *state* (visible, hide_from_share, collapsed,
opacity, input focus) and pushes it to a controller. OverlayWindow is the real
controller; tests use fakes. Only `supported`, `set_visible` and
`set_hide_from_share` are required; the runtime skips the optional setters
(set_collapsed, set_opacity, set_input_focus) when a controller lacks them.

Why the window is resized instead of relying on transparency: pywebview's
`transparent=True` is not real per-pixel transparency on Windows. WebView2 is a
GPU-composited child of a WinForms Form, and wherever the page is transparent the
Form's BackColor shows through (SystemColors.Control, light grey). A WinForms
TransparencyKey or a SetWindowRgn region does not clip the WebView2 surface (both
measured on Windows 11 26300), so an empty transparent area can never be made
see-through. Instead the window is always sized to its content (a pill when
collapsed), painted a dark colour that matches the overlay, and given DWM
rounded corners, which do clip the WebView2 surface.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Protocol

from escort.desktop import win_overlay

log = logging.getLogger("escort.desktop.overlay")

WIDTH = 440
HEIGHT = 560
COLLAPSED_HEIGHT = 88
TOP_MARGIN = 24
TITLE = "Escort Overlay"
BACKGROUND = "#202020"
DEFAULT_OPACITY = 0.92
MIN_OPACITY = 0.3
MAX_OPACITY = 1.0
# A focus=False arriving this soon after focus=True is the page's own blur/focus
# churn while Windows hands it activation; acting on it made the ask box flicker.
FOCUS_RELEASE_DELAY_S = 0.25


def clamp_opacity(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return DEFAULT_OPACITY
    if number != number:  # NaN
        return DEFAULT_OPACITY
    return round(min(MAX_OPACITY, max(MIN_OPACITY, number)), 3)


def overlay_size(collapsed: bool) -> tuple[int, int]:
    return WIDTH, COLLAPSED_HEIGHT if collapsed else HEIGHT


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
        self._collapsed = False
        self._opacity = DEFAULT_OPACITY
        self._input_focus = False
        self._previous_foreground: int | None = None
        self._placed = False
        self._applied_opacity: float | None = None
        self._release_timer: threading.Timer | None = None
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
            self._apply_background()
            self._apply_opacity()
            self._apply_size()
            self._apply_affinity()
            self._apply_visibility()
            return self.hwnd is not None

    def set_visible(self, visible: bool) -> None:
        with self._lock:
            self._visible = visible
            if not visible:
                self._cancel_release()
                if self._input_focus:
                    self._release_focus()
            self._apply_visibility()

    def set_hide_from_share(self, hide: bool) -> None:
        with self._lock:
            self._hide_from_share = hide
            self._apply_affinity()

    def set_collapsed(self, collapsed: bool) -> None:
        with self._lock:
            self._collapsed = collapsed
            self._apply_size()

    def set_opacity(self, opacity: float) -> None:
        with self._lock:
            self._opacity = clamp_opacity(opacity)
            if self._opacity == self._applied_opacity:
                return  # slider sweeps repeat values; each apply rebuilds the window styles
            self._apply_opacity()

    def set_input_focus(self, focus: bool) -> None:
        """True lets the overlay take keystrokes (clears NOACTIVATE); False hands focus back.

        Idempotent, and a release is delayed by FOCUS_RELEASE_DELAY_S so a
        blur/focus pair from the page while it is being activated is a no-op.
        """
        with self._lock:
            self._cancel_release()
            if focus:
                if not self._input_focus:
                    self._take_focus()
            elif self._input_focus:
                timer = threading.Timer(FOCUS_RELEASE_DELAY_S, self._delayed_release)
                timer.daemon = True
                self._release_timer = timer
                timer.start()

    def _cancel_release(self) -> None:
        if self._release_timer is not None:
            self._release_timer.cancel()
            self._release_timer = None

    def _delayed_release(self) -> None:
        with self._lock:
            if self._release_timer is None or threading.current_thread() is not self._release_timer:
                return  # cancelled or superseded meanwhile
            self._release_timer = None
            if self._input_focus:
                self._release_focus()

    def _set_pywebview_focus_flag(self, focus: bool) -> None:
        # pywebview re-adds WS_EX_NOACTIVATE on every activation of a focus=False window.
        try:
            self.window.focus = focus
        except Exception:
            pass

    def _take_focus(self) -> None:
        self._input_focus = True
        self._set_pywebview_focus_flag(True)
        hwnd = self.hwnd
        if not hwnd:
            return
        current = win_overlay.foreground_window()
        if current and current != hwnd:
            self._previous_foreground = current
        native = getattr(self.window, "native", None)

        def focus() -> None:
            win_overlay.make_tool_window(hwnd, no_activate=False)
            win_overlay.focus_window(hwnd)
            if native is not None:
                try:
                    native.Activate()
                    browser = getattr(getattr(native, "browser", None), "webview", None)
                    if browser is not None:
                        # WinForms focus alone is not enough: after the Form has been
                        # layered (Opacity) or already active, keystrokes never reach the
                        # page (measured). MoveFocus on the WebView2 controller is.
                        browser.Focus()
                        _move_webview_focus(browser)
                except Exception as exc:
                    log.debug("Overlay WinForms focus failed: %s", exc)

        self._on_gui(focus)

    def _release_focus(self) -> None:
        self._input_focus = False
        self._set_pywebview_focus_flag(False)
        hwnd, previous = self.hwnd, self._previous_foreground
        self._previous_foreground = None
        if not hwnd:
            return

        def release() -> None:
            win_overlay.make_tool_window(hwnd, no_activate=True)
            if previous and win_overlay.is_window(previous):
                win_overlay.focus_window(previous)

        self._on_gui(release)

    def _apply_background(self) -> None:
        native = getattr(self.window, "native", None)
        if native is None or not self.hwnd:
            return
        hwnd = self.hwnd

        def paint() -> None:
            try:
                from System.Drawing import ColorTranslator  # type: ignore[import-not-found]

                native.BackColor = ColorTranslator.FromHtml(BACKGROUND)
            except Exception as exc:
                log.debug("Overlay back colour not set: %s", exc)
            win_overlay.round_corners(hwnd)

        self._on_gui(paint)

    def _apply_size(self) -> None:
        width, height = overlay_size(self._collapsed)
        if self.hwnd:
            hwnd = self.hwnd
            self._on_gui(lambda: win_overlay.resize(hwnd, width, height))
            return
        if win_overlay.is_windows():
            return  # no handle yet; attach() applies the size once the window exists
        try:
            self.window.resize(width, height)
        except Exception as exc:
            log.warning("Overlay resize failed: %s", exc)

    def _apply_opacity(self) -> None:
        native = getattr(self.window, "native", None)
        if native is None or not self.hwnd:
            return
        hwnd = self.hwnd
        opacity = self._opacity
        self._applied_opacity = opacity

        def apply() -> None:
            native.Opacity = opacity
            # Setting Opacity makes WinForms rebuild the extended styles, and the
            # layered style change can drop the display affinity: re-assert both.
            win_overlay.make_tool_window(hwnd, no_activate=not self._input_focus)
            if self.supported:
                win_overlay.set_capture_excluded(hwnd, self._hide_from_share)

        self._on_gui(apply)

    def window_rect(self) -> tuple[int, int, int, int] | None:
        """Where the overlay sits on screen (None before it has been shown)."""
        if not self._placed:
            return None
        return win_overlay.window_rect(self.hwnd)

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

                no_activate = not self._input_focus

                def show() -> None:
                    # WinForms resets extended styles from CreateParams whenever
                    # pywebview touches Opacity etc., so re-assert them each time.
                    win_overlay.make_tool_window(hwnd, no_activate=no_activate)
                    win_overlay.show_no_activate(hwnd, place_top_center=place, margin=TOP_MARGIN)

                self._on_gui(show)
            else:
                self._on_gui(lambda: (win_overlay.hide(hwnd), win_overlay.make_tool_window(hwnd)))
            return
        if win_overlay.is_windows():
            return  # no handle yet (before webview.start); attach() applies it later
        # Non-Windows: fall back to pywebview's own calls.
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
        """Run fn on the window's UI thread (WinForms and some Win32 calls require the owner thread).

        Skipped when the Form is gone or its HWND was destroyed: touching a
        disposed Form or a dead handle from another thread is undefined.
        """
        native = getattr(self.window, "native", None)
        hwnd = self.hwnd

        def call() -> None:
            if hwnd and win_overlay.is_windows() and not win_overlay.is_window(hwnd):
                log.debug("Overlay window handle is gone; skipping")
                return
            try:
                fn()
            except Exception as exc:
                log.warning("Overlay call failed: %s", exc)

        if native is None:
            call()
            return
        try:
            if bool(getattr(native, "IsDisposed", False)) or not bool(getattr(native, "IsHandleCreated", True)):
                log.debug("Overlay Form not ready or disposed; skipping")
                return
            if getattr(native, "InvokeRequired", False):
                from System import Func, Type  # type: ignore[import-not-found]

                native.Invoke(Func[Type](call))
                return
        except Exception as exc:
            log.warning("Overlay UI-thread dispatch failed: %s", exc)
            return
        call()


def _move_webview_focus(browser: Any) -> None:
    """Tell the WebView2 controller to take keyboard focus (the WinForms wrapper keeps it private)."""
    try:
        from Microsoft.Web.WebView2.Core import CoreWebView2MoveFocusReason  # type: ignore[import-not-found]
        from System.Reflection import BindingFlags  # type: ignore[import-not-found]

        field = browser.GetType().GetField("_coreWebView2Controller", BindingFlags.NonPublic | BindingFlags.Instance)
        controller = field.GetValue(browser) if field is not None else None
        if controller is not None:
            controller.MoveFocus(CoreWebView2MoveFocusReason.Programmatic)
    except Exception as exc:
        log.debug("WebView2 MoveFocus unavailable: %s", exc)


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
            # pywebview's default min_size (200x100) would clamp the collapsed pill.
            min_size=(WIDTH, COLLAPSED_HEIGHT),
            x=x,
            y=y,
            frameless=True,
            # easy_drag=True drags the window from a mousedown *anywhere* (pywebview's
            # customize.js listens on window), so dragging the opacity slider or
            # selecting text in the ask box moved the whole overlay instead. Only the
            # .pywebview-drag-region elements (pill, panel header) drag it now.
            easy_drag=False,
            resizable=False,
            on_top=True,
            # transparent=True is kept for pywebview's WinForms show path: without it the
            # Win32-shown form is never WinForms-Visible and WebView2 renders nothing.
            # It does not give real transparency (see module docstring); attach()
            # paints the Form BackColor dark so no grey ever shows.
            transparent=True,
            shadow=False,
            hidden=True,
            focus=False,
            background_color=BACKGROUND,
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

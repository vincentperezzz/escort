from __future__ import annotations

import dataclasses
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Protocol

import httpx

from escort.capture import monitors as monitor_mod
from escort.context.library import ContextLibrary
from escort.context.mock_data import mock_import_item
from escort.context.sources.registry import Connectors
from escort.context.prep_chat import prep_chat
from escort.context.settings_intent import describe_settings, handle_settings_message
from escort.desktop import live_capture
from escort.desktop.devices import list_audio_devices
from escort.desktop.overlay import DEFAULT_OPACITY, clamp_opacity
from escort.desktop.secret_store import MemorySecretStore, SecretStore, open_secret_store
from escort.replay import build_pipeline, load_scenario, run_replay
from escort.settings import Settings, load_settings
from escort.store import MeetingStore, utc_now
from escort.summary import empty_summary, generate_summary, rules_summary

if TYPE_CHECKING:
    from escort.desktop.overlay import OverlayController


class MainWindowController(Protocol):
    def show(self) -> None: ...

    def hide(self) -> None: ...


class OverlayProcess(Protocol):
    """An overlay living in another process (Electron) that follows GET /api/overlay itself."""

    def is_running(self) -> bool: ...

    def stop(self) -> None: ...


IDLE_SCREEN = {"monitoring": False, "last_frame_at": None, "frames_seen": 0, "changed_frames": 0}
MONITOR_CACHE_S = 3.0

log = logging.getLogger("escort.desktop")

KEY_NAMES = (
    "meta_api_key",
    "whatsapp_access_token",
    "whatsapp_app_secret",
    "whatsapp_verify_token",
    "notion_oauth_token",
    "google_oauth_token",
    "google_client_secret",
)


def config_dir() -> Path:
    override = os.environ.get("ESCORT_CONFIG_DIR")
    if override:
        path = Path(override)
    elif os.environ.get("APPDATA"):
        path = Path(os.environ["APPDATA"]) / "Escort"
    else:
        path = Path.home() / ".config" / "Escort"
    path.mkdir(parents=True, exist_ok=True)
    return path


class DesktopRuntime:
    def __init__(
        self,
        store: MeetingStore,
        secrets: SecretStore | None = None,
        config_path: Path | None = None,
        connectors: Connectors | None = None,
        summary_client: httpx.Client | None = None,
        monitor_lister: Callable[[], list[dict[str, int]]] | None = None,
    ) -> None:
        self.store = store
        self._monitor_lister = monitor_lister
        self._monitor_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._chat_pending: dict[str, Any] | None = None
        self.secrets = secrets or open_secret_store()
        self.config_path = config_path or (config_dir() / "config.json")
        self.listening = False
        self.listening_started_at: str | None = None
        self._lock = threading.RLock()
        self.prefs = self._read_prefs()
        self.overlay: OverlayController | None = None
        self.overlay_process: OverlayProcess | None = None
        self.overlay_visible = False
        self.overlay_collapsed = False
        self.overlay_input_focus = False
        self.main_window: MainWindowController | None = None
        self._main_hidden = False
        self.live: live_capture.LiveSession | None = None
        self.summary: dict[str, Any] = empty_summary()
        self._summary_gen = 0
        self._summary_thread: threading.Thread | None = None
        self._summary_client = summary_client
        self._observers: list[Callable[[], None]] = []
        key = self.secrets.get("meta_api_key")
        if key and not os.environ.get("MODEL_API_KEY"):
            # Keychain survives restarts; Muse clients read the key from the environment.
            os.environ["MODEL_API_KEY"] = key
        self.connectors = connectors or Connectors(self.secrets, client_id_getter=lambda: self.prefs.get("google_client_id"))
        self.context = ContextLibrary(store.media_dir.parent, self.secrets.get, connectors=self.connectors)

    def _context_root(self) -> Path:
        return self.store.media_dir.parent

    def _read_prefs(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {"mic_id": "", "loopback_id": "", "whatsapp_phone_number_id": "", "whatsapp_allowed_numbers": "", "whatsapp_graph_version": "v22.0"}
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        for secret in KEY_NAMES:
            data.pop(secret, None)
        return data

    def _write_prefs(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        safe = {key: value for key, value in self.prefs.items() if key not in KEY_NAMES and "key" not in key and "token" not in key and "secret" not in key}
        self.config_path.write_text(json.dumps(safe, indent=2), encoding="utf-8")

    def _settings(self) -> Settings:
        settings = load_settings()
        key = self.secrets.get("meta_api_key") or settings.api_key
        return dataclasses.replace(settings, api_key=key or None)

    def screen_status(self) -> dict[str, Any]:
        live = self.live
        screen = live.screen() if live is not None else dict(IDLE_SCREEN)
        # The loop reports what it is grabbing; before its first tick (or when idle) say what it will grab.
        return {**screen, "monitor": screen.get("monitor") or self._watch_target()["label"]}

    # --- watched display -------------------------------------------------------

    def list_monitors(self, fresh: bool = False) -> list[dict[str, Any]]:
        cached = self._monitor_cache
        if not fresh and cached is not None and time.monotonic() - cached[0] < MONITOR_CACHE_S:
            return cached[1]
        found = monitor_mod.list_monitors(self._monitor_lister or monitor_mod.enumerate_raw)
        self._monitor_cache = (time.monotonic(), found)
        return found

    @property
    def watch_choice(self) -> Any:
        try:
            return monitor_mod.normalize_choice(self.prefs.get("watch_monitor", monitor_mod.PRIMARY))
        except ValueError:
            return monitor_mod.PRIMARY

    def _watch_target(self) -> dict[str, Any]:
        """Polled by the screen loop every tick, so a new choice applies live."""
        return monitor_mod.resolve(self.watch_choice, self.list_monitors())

    def monitors_status(self, fresh: bool = False) -> dict[str, Any]:
        monitors = self.list_monitors(fresh=fresh)
        effective = monitor_mod.resolve(self.watch_choice, monitors)
        return {
            "monitors": monitors,
            "selected": self.watch_choice,
            "effective": effective,
            "notice": effective["notice"],
            "overlay_on_watched": self._overlay_on_watched(effective, monitors),
        }

    def set_watch_monitor(self, selected: Any) -> dict[str, Any]:
        """Persist the watched display; a running screen loop picks it up on its next tick.

        Raises ValueError for anything that is not 'primary', 'all' or a connected display.
        """
        choice = monitor_mod.normalize_choice(selected)
        monitors = self.list_monitors(fresh=True)
        if isinstance(choice, int) and monitors and not any(item["index"] == choice for item in monitors):
            count = len(monitors)
            raise ValueError(f"There is no Display {choice}; this machine has {count} display{'s' if count != 1 else ''}.")
        with self._lock:
            if self.prefs.get("watch_monitor") != choice:
                self.prefs["watch_monitor"] = choice
                self._write_prefs()
        self._notify()
        return self.monitors_status()

    def _overlay_on_watched(self, effective: dict[str, Any], monitors: list[dict[str, Any]]) -> bool | None:
        """True when the overlay window overlaps the watched display; None when unknown."""
        getter = getattr(self.overlay, "window_rect", None)
        if getter is None or not self.overlay_visible:
            return None
        try:
            rect = getter()
        except Exception:
            return None
        if rect is None:
            return None
        if effective["index"] == monitor_mod.ALL_INDEX:
            return True
        watched = next((item for item in monitors if item["index"] == effective["index"]), None)
        return monitor_mod.rects_overlap(rect, watched) if watched else None

    def capture_error(self) -> str | None:
        live = self.live
        return live.error() if live is not None else None

    def public_status(self) -> dict[str, Any]:
        snapshot = self.store.snapshot()
        return {
            "listening": self.listening,
            "listening_started_at": self.listening_started_at,
            "screen": self.screen_status(),
            "capture_error": self.capture_error(),
            "summary": self.summary_status(),
            "has_meta_key": bool(self.secrets.get("meta_api_key")),
            "has_whatsapp_token": bool(self.secrets.get("whatsapp_access_token")),
            "prefs": {
                "mic_id": self.prefs.get("mic_id", ""),
                "loopback_id": self.prefs.get("loopback_id", ""),
                "whatsapp_phone_number_id": self.prefs.get("whatsapp_phone_number_id", ""),
                "whatsapp_allowed_numbers": self.prefs.get("whatsapp_allowed_numbers", ""),
                "whatsapp_graph_version": self.prefs.get("whatsapp_graph_version", "v22.0"),
                "google_client_id": self.prefs.get("google_client_id", ""),
                "watch_monitor": self.watch_choice,
            },
            "monitors": self.monitors_status(),
            "devices": list_audio_devices(),
            "meeting": snapshot,
            "context": self.context.public_view(),
            "connectors": self.connectors.status(),
            "overlay": self.overlay_status(),
            "mcp":"http://127.0.0.1:8747/mcp",
            "whatsapp": "http://127.0.0.1:8748/webhook",
        }

    def save_setup(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if body.get("meta_api_key"):
                self.secrets.set("meta_api_key", str(body["meta_api_key"]))
                os.environ["MODEL_API_KEY"] = str(body["meta_api_key"])
            for name in ("whatsapp_access_token", "whatsapp_app_secret", "whatsapp_verify_token", "notion_oauth_token", "google_oauth_token", "google_client_secret"):
                if body.get(name):
                    self.secrets.set(name, str(body[name]))
            for key in ("mic_id", "loopback_id", "whatsapp_phone_number_id", "whatsapp_allowed_numbers", "whatsapp_graph_version", "google_client_id"):
                if key in body:
                    self.prefs[key] = body[key]
            self._write_prefs()
        return self.public_status()

    def set_prd(self, text: str, name: str = "uploaded.md") -> dict[str, Any]:
        self.context.add_paste(text, title=name)
        self.context.apply_to_store(self.store)
        return self.public_status()

    def context_upload(self, filename: str, data: bytes) -> dict[str, Any]:
        with self._lock:
            self.context.add_upload(filename, data)
            self.context.apply_to_store(self.store)
        return self.public_status()

    def context_remove(self, item_id: str) -> dict[str, Any]:
        with self._lock:
            self.context.remove_item(item_id)
            self.context.apply_to_store(self.store)
        return self.public_status()

    def context_import(self, source: str, external_id: str) -> dict[str, Any]:
        with self._lock:
            self.context.import_external(source, external_id)
            self.context.apply_to_store(self.store)
        return self.public_status()

    def context_search(self, source: str, query: str) -> dict[str, Any]:
        return {"hits": self.context.search_connector(source, query)}

    def context_match(self, event_id: str) -> dict[str, Any]:
        event, proposals = self.context.propose_matches(event_id)
        return {
            "event": event.to_dict() if event else None,
            "proposals": [item.to_dict() for item in proposals],
        }

    def context_prep(self, event_id: str, item_ids: list[str]) -> dict[str, Any]:
        with self._lock:
            self.context.confirm_prep(event_id, item_ids)
            self.context.apply_to_store(self.store)
        return self.public_status()

    def context_chat(self, message: str) -> dict[str, Any]:
        """Prep chat and overlay ask box. Settings requests are handled here, before Muse."""
        with self._lock:
            pending, self._chat_pending = self._chat_pending, None
            self.context.append_chat("user", message)
            settings_reply = handle_settings_message(message, self, pending)
            changed: list[dict[str, Any]] = []
            if settings_reply is not None:
                answer = settings_reply.reply
                changed = settings_reply.changed
                self._chat_pending = settings_reply.pending
            else:
                answer = prep_chat(
                    message,
                    self.context.combined_markdown(),
                    self.context.chat_history(),
                    settings_note=describe_settings(self),
                )
            self.context.append_chat("assistant", answer, changed=changed or None)
        return {"reply": answer, "changed": changed, "context": self.context.public_view()}

    def load_demo(self) -> dict[str, Any]:
        scenario = load_scenario()
        settings = load_settings()
        self.context.clear()
        self.context.add_paste(scenario.prd_text, title="BayaniPay scope (demo)")
        # The demo is a fixed scenario: always use the bundled sample docs, never live accounts.
        self.context.add_item(mock_import_item("notion", "notion_phase_scope"))
        self.context.add_item(mock_import_item("drive", "drive_sow"))
        self.context.confirm_prep("evt_bayani_scope", [item.id for item in self.context.items()[:2]])
        self.context.apply_to_store(self.store)
        pipeline = build_pipeline(
            self.store,
            scenario,
            settings,
            transcriber_mode="scripted",
            spark_mode="rules",
            segmenter_mode="fallback",
        )
        self.context.apply_to_store(self.store)
        run_replay(pipeline, scenario, pace_ms=0)
        return self.public_status()

    def connectors_status(self) -> dict[str, Any]:
        return self.connectors.status()

    def connect_google(self) -> dict[str, str]:
        return self.connectors.connect_google()

    def connect_notion(self, token: str) -> dict[str, Any]:
        return self.connectors.connect_notion(token)

    def disconnect_connector(self, provider: str) -> dict[str, Any]:
        return self.connectors.disconnect(provider)

    def set_listening(self, on: bool) -> dict[str, Any]:
        with self._lock:
            was = self.listening
            self.listening = on
            if on and self.store.mode in {"idle", "deleted"}:
                self.store.mode = "live"
            if not on and self.store.mode == "live":
                self.store.mode = "idle"
            if on and not was:
                self.listening_started_at = utc_now()
                self._summary_gen += 1
                self.summary = empty_summary()
                self._start_capture()
            elif not on and was:
                self.listening_started_at = None
                self._begin_summary(stop_session=self.live)
        self._set_overlay_visible(on, bring_main=not on)
        self._notify()
        return self.public_status()

    def _start_capture(self) -> None:
        """Start screen + audio capture over the store. Never raises; errors land in capture_error."""
        settings = self._settings()
        if not self.store.title:
            self.store.title = "Live meeting"
        try:
            self.live = live_capture.start_session(
                self.store,
                settings,
                self.context.combined_markdown(),
                mic_id=self.prefs.get("mic_id"),
                loopback_id=self.prefs.get("loopback_id"),
                on_pipeline=lambda: self.context.apply_to_store(self.store),
                monitor_source=self._watch_target,
            )
        except Exception as exc:
            log.warning("Live capture failed to start: %s", exc)
            self.store.warn(f"Live capture did not start: {exc}")
            self.live = None

    def _stop_capture(self, timeout: float = 6.0) -> None:
        live = self.live
        if live is not None:
            try:
                live.stop(timeout=timeout)
            except Exception as exc:
                log.warning("Live capture did not stop cleanly: %s", exc)

    def shutdown(self) -> None:
        """App exit: stop capture threads (they are daemons, but release devices first)."""
        self.listening = False
        process = self.overlay_process
        if process is not None:
            # First: it is the one window still on screen.
            try:
                process.stop()
            except Exception as exc:
                log.warning("Overlay process did not stop cleanly: %s", exc)
        self._stop_capture(timeout=2.0)

    def delete_meeting(self) -> dict[str, Any]:
        with self._lock:
            self.listening = False
            self.listening_started_at = None
            self._summary_gen += 1
            self.summary = empty_summary()
            self._stop_capture(timeout=2.0)
            self.live = None
            self.store.delete_meeting()
            self.context.clear()
        self._set_overlay_visible(False, bring_main=True)
        self._notify()
        return self.public_status()

    # --- summary -------------------------------------------------------------

    def summary_status(self) -> dict[str, Any]:
        return dict(self.summary)

    def regenerate_summary(self) -> dict[str, Any]:
        with self._lock:
            if not self.listening:
                self._begin_summary(stop_session=None)
        return self.summary_status()

    def transcript(self) -> dict[str, Any]:
        with self.store.lock:
            return {
                "turns": [item.to_dict() for item in self.store.utterances],
                "partials": dict(self.store.partials),
                "listening": self.listening,
                "listening_started_at": self.listening_started_at,
            }

    def _begin_summary(self, stop_session: live_capture.LiveSession | None) -> None:
        self._summary_gen += 1
        generation = self._summary_gen
        self.summary = {**empty_summary("generating"), "title": self.store.title}
        thread = threading.Thread(
            target=self._run_summary, args=(generation, stop_session), name="escort-summary", daemon=True
        )
        self._summary_thread = thread
        thread.start()

    def _run_summary(self, generation: int, stop_session: live_capture.LiveSession | None) -> None:
        if stop_session is not None:
            # Join the capture threads first so the last finished turns are in the snapshot.
            try:
                stop_session.stop()
            except Exception as exc:
                log.warning("Live capture did not stop cleanly: %s", exc)
        snapshot: dict[str, Any] = {}
        try:
            snapshot = self.store.snapshot()
            result = generate_summary(self._settings(), snapshot, self.context.combined_markdown(), self._summary_client)
        except Exception as exc:
            log.warning("Summary failed: %s", exc)
            try:
                result = rules_summary(snapshot, error=str(exc))
            except Exception as inner:
                result = {**empty_summary("error"), "error": str(inner), "generated_at": utc_now()}
        with self._lock:
            if generation != self._summary_gen:
                return  # deleted or restarted meanwhile
            self.summary = result
        self._notify()

    # --- overlay -------------------------------------------------------------

    @property
    def hide_from_share(self) -> bool:
        return bool(self.prefs.get("hide_from_share", True))

    def overlay_supported(self) -> bool:
        if self.overlay is not None:
            return bool(self.overlay.supported)
        from escort.desktop.win_overlay import capture_exclusion_supported

        return capture_exclusion_supported()

    @property
    def overlay_opacity(self) -> float:
        return clamp_opacity(self.prefs.get("overlay_opacity", DEFAULT_OPACITY))

    def overlay_status(self) -> dict[str, Any]:
        return {
            "hide_from_share": self.hide_from_share,
            "visible": self.overlay_visible,
            "listening": self.listening,
            "supported": self.overlay_supported(),
            "collapsed": self.overlay_collapsed,
            "opacity": self.overlay_opacity,
            "input_focus": self.overlay_input_focus,
        }

    def attach_overlay(self, controller: OverlayController | None) -> None:
        """Bind the overlay window and push the current state to it."""
        self.overlay = controller
        if controller is None:
            return
        self._call_overlay("set_hide_from_share", self.hide_from_share)
        self._call_optional("set_opacity", self.overlay_opacity)
        self._call_optional("set_collapsed", self.overlay_collapsed)
        self._call_overlay("set_visible", self.overlay_visible)

    def attach_overlay_process(self, process: OverlayProcess | None) -> None:
        """Electron mode: the overlay polls /api/overlay, so set_overlay only stores flags."""
        self.overlay_process = process

    def overlay_process_lost(self) -> None:
        """The overlay process is gone for good: never leave the user without the main window."""
        if self._main_hidden:
            self._main("show")

    def _overlay_process_running(self) -> bool:
        process = self.overlay_process
        if process is None:
            return False
        try:
            return bool(process.is_running())
        except Exception:
            return False

    def attach_main_window(self, controller: MainWindowController | None) -> None:
        self.main_window = controller

    def show_main_window(self) -> None:
        """Tray 'Show Escort': always restores and raises the main window."""
        self._main("show")

    def set_overlay(
        self,
        hide_from_share: bool | None = None,
        visible: bool | None = None,
        collapsed: bool | None = None,
        opacity: float | None = None,
        input_focus: bool | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if hide_from_share is not None and hide_from_share != self.hide_from_share:
                self.prefs["hide_from_share"] = hide_from_share
                self._write_prefs()
            if hide_from_share is not None:
                self._call_overlay("set_hide_from_share", hide_from_share)
            if opacity is not None:
                value = clamp_opacity(opacity)
                if value != self.prefs.get("overlay_opacity"):
                    self.prefs["overlay_opacity"] = value
                    self._write_prefs()
                self._call_optional("set_opacity", value)
            if collapsed is not None:
                self.overlay_collapsed = collapsed
                self._call_optional("set_collapsed", collapsed)
            if visible is not None:
                self._set_overlay_visible(visible, bring_main=not visible)
            if input_focus is not None and (self.overlay_visible or not input_focus):
                self.overlay_input_focus = input_focus
                self._call_optional("set_input_focus", input_focus)
        self._notify()
        return self.overlay_status()

    def toggle_overlay(self) -> dict[str, Any]:
        return self.set_overlay(visible=not self.overlay_visible)

    def subscribe(self, callback: Callable[[], None]) -> None:
        """Called after listening or overlay state changes (used by the tray)."""
        self._observers.append(callback)

    def _set_overlay_visible(self, visible: bool, bring_main: bool = False) -> None:
        """Show/hide the overlay; the main window hides while the overlay is up.

        The main window hides *before* the overlay shows, so Windows hands
        activation back to the user's previous app instead of the overlay. If
        there is no overlay it is never hidden; if the overlay call fails it is
        shown again at once, so the user is never left without a window. An
        overlay process (Electron) shows itself from the stored flag; the main
        window only hides while that process is actually running.
        """
        self.overlay_visible = visible
        if not visible:
            self.overlay_input_focus = False
            self._call_overlay("set_visible", False)
            if self._main_hidden or bring_main:
                self._main("show")
            return
        if self.overlay is not None:
            hide_main = not self._main_hidden
            if hide_main:
                self._main("hide")
            if not self._call_overlay("set_visible", True) and hide_main:
                self._main("show")
        elif self._overlay_process_running() and not self._main_hidden:
            self._main("hide")

    def _main(self, action: str) -> None:
        if self.main_window is None:
            return
        try:
            getattr(self.main_window, action)()
            self._main_hidden = action == "hide"
        except Exception as exc:
            log.warning("Main window %s failed: %s", action, exc)

    def _call_overlay(self, method: str, value: Any) -> bool:
        if self.overlay is None:
            return False
        try:
            getattr(self.overlay, method)(value)
            return True
        except Exception as exc:
            log.warning("Overlay %s(%s) failed: %s", method, value, exc)
            return False

    def _call_optional(self, method: str, value: Any) -> bool:
        """Like _call_overlay, for setters older controllers (and test fakes) may not have."""
        if self.overlay is None or not hasattr(self.overlay, method):
            return False
        return self._call_overlay(method, value)

    def _notify(self) -> None:
        for callback in list(self._observers):
            try:
                callback()
            except Exception as exc:
                log.warning("State observer failed: %s", exc)

    def act(self, card_id: str, action: str) -> dict[str, Any]:
        card = next((item for item in self.store.cards if item.id == card_id), None)
        if card is None:
            return self.public_status()
        if action == "ignore":
            self.store.warn(f"Ignored {card.title}")
        elif action == "raise":
            from escort.models import CardRecord
            from escort.store import new_id, utc_now

            self.store.add_card(
                CardRecord(
                    id=new_id("card"),
                    type="COMMITMENT",
                    title="Raise it",
                    body=card.body,
                    prd_section=card.prd_section,
                    who="Me",
                    what=card.title,
                    utterance_id=card.utterance_id,
                    trigger_text=card.title,
                    created_at=utc_now(),
                    latency_ms=0,
                    crop_path=None,
                    crop_url=None,
                    source="desktop",
                    deictic=None,
                    segmenter=None,
                    when="this meeting",
                )
            )
        elif action == "suggest":
            self.store.warn(f"Suggest reply for {card.title}")
        return self.public_status()


def demo_runtime(folder: Path) -> DesktopRuntime:
    store = MeetingStore(folder / "media")
    runtime = DesktopRuntime(store, MemorySecretStore(), folder / "config.json")
    return runtime

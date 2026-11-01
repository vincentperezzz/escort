from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from escort.context.library import ContextLibrary
from escort.context.mock_data import mock_import_item
from escort.context.sources.registry import Connectors
from escort.context.prep_chat import prep_chat
from escort.desktop.devices import list_audio_devices
from escort.desktop.secret_store import MemorySecretStore, SecretStore, open_secret_store
from escort.paths import repo_root
from escort.replay import build_pipeline, load_scenario, run_replay
from escort.settings import load_settings
from escort.store import MeetingStore

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
    ) -> None:
        self.store = store
        self.secrets = secrets or open_secret_store()
        self.config_path = config_path or (config_dir() / "config.json")
        self.listening = False
        self._lock = threading.RLock()
        self.prefs = self._read_prefs()
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

    def public_status(self) -> dict[str, Any]:
        snapshot = self.store.snapshot()
        return {
            "listening": self.listening,
            "has_meta_key": bool(self.secrets.get("meta_api_key")),
            "has_whatsapp_token": bool(self.secrets.get("whatsapp_access_token")),
            "prefs": {
                "mic_id": self.prefs.get("mic_id", ""),
                "loopback_id": self.prefs.get("loopback_id", ""),
                "whatsapp_phone_number_id": self.prefs.get("whatsapp_phone_number_id", ""),
                "whatsapp_allowed_numbers": self.prefs.get("whatsapp_allowed_numbers", ""),
                "whatsapp_graph_version": self.prefs.get("whatsapp_graph_version", "v22.0"),
                "google_client_id": self.prefs.get("google_client_id", ""),
            },
            "devices": list_audio_devices(),
            "meeting": snapshot,
            "context": self.context.public_view(),
            "connectors": self.connectors.status(),
            "mcp": "http://127.0.0.1:8747/mcp",
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
        with self._lock:
            self.context.append_chat("user", message)
            answer = prep_chat(message, self.context.combined_markdown(), self.context.chat_history())
            self.context.append_chat("assistant", answer)
        return {"reply": answer, "context": self.context.public_view()}

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
        self.listening = on
        if on and self.store.mode in {"idle", "deleted"}:
            self.store.mode = "live"
        if not on and self.store.mode == "live":
            self.store.mode = "idle"
        return self.public_status()

    def delete_meeting(self) -> dict[str, Any]:
        self.listening = False
        self.store.delete_meeting()
        self.context.clear()
        return self.public_status()

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

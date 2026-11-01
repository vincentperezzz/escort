from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from escort.desktop.devices import list_audio_devices
from escort.desktop.secrets import MemorySecretStore, SecretStore, open_secret_store
from escort.paths import repo_root
from escort.replay import build_pipeline, load_scenario, run_replay
from escort.settings import load_settings
from escort.store import MeetingStore

KEY_NAMES = ("meta_api_key", "whatsapp_access_token", "whatsapp_app_secret", "whatsapp_verify_token")


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
    def __init__(self, store: MeetingStore, secrets: SecretStore | None = None, config_path: Path | None = None) -> None:
        self.store = store
        self.secrets = secrets or open_secret_store()
        self.config_path = config_path or (config_dir() / "config.json")
        self.listening = False
        self._lock = threading.RLock()
        self.prefs = self._read_prefs()

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
            },
            "devices": list_audio_devices(),
            "meeting": snapshot,
            "mcp": "http://127.0.0.1:8747/mcp",
            "whatsapp": "http://127.0.0.1:8748/webhook",
        }

    def save_setup(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if body.get("meta_api_key"):
                self.secrets.set("meta_api_key", str(body["meta_api_key"]))
                os.environ["MODEL_API_KEY"] = str(body["meta_api_key"])
            for name in ("whatsapp_access_token", "whatsapp_app_secret", "whatsapp_verify_token"):
                if body.get(name):
                    self.secrets.set(name, str(body[name]))
            for key in ("mic_id", "loopback_id", "whatsapp_phone_number_id", "whatsapp_allowed_numbers", "whatsapp_graph_version"):
                if key in body:
                    self.prefs[key] = body[key]
            self._write_prefs()
        return self.public_status()

    def set_prd(self, text: str, name: str = "uploaded.md") -> dict[str, Any]:
        from escort.prd import parse_sections

        self.store.prd_name = name
        self.store.prd_sections = [
            {"anchor": section.anchor, "heading": section.heading, "quote": section.quote()}
            for section in parse_sections(text)
        ]
        path = self.store.media_dir.parent / "prd.md"
        path.write_text(text, encoding="utf-8")
        return self.public_status()

    def load_demo(self) -> dict[str, Any]:
        scenario = load_scenario()
        settings = load_settings()
        pipeline = build_pipeline(
            self.store,
            scenario,
            settings,
            transcriber_mode="scripted",
            spark_mode="rules",
            segmenter_mode="fallback",
        )
        run_replay(pipeline, scenario, pace_ms=0)
        return self.public_status()

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

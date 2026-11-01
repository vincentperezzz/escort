from pathlib import Path

from fastapi.testclient import TestClient

from escort.desktop.api import create_desktop_app
from escort.desktop.runtime import DesktopRuntime
from escort.desktop.secrets import MemorySecretStore
from escort.store import MeetingStore


def _client(tmp_path: Path) -> tuple[TestClient, DesktopRuntime]:
    runtime = DesktopRuntime(MeetingStore(tmp_path / "media"), MemorySecretStore(), tmp_path / "config.json")
    return TestClient(create_desktop_app(runtime)), runtime


def test_setup_stores_the_key_in_the_keychain_not_the_config_file(tmp_path: Path):
    client, runtime = _client(tmp_path)
    saved = client.post("/api/setup", json={"meta_api_key": "LLM_secret", "mic_id": "default-mic"})
    assert saved.status_code == 200
    assert saved.json()["has_meta_key"] is True
    assert "LLM_secret" not in (tmp_path / "config.json").read_text(encoding="utf-8")
    assert runtime.secrets.get("meta_api_key") == "LLM_secret"
    assert runtime.prefs["mic_id"] == "default-mic"


def test_live_demo_cards_actions_and_delete(tmp_path: Path):
    client, runtime = _client(tmp_path)
    demo = client.post("/api/demo")
    assert demo.status_code == 200
    types = {card["type"] for card in demo.json()["meeting"]["cards"]}
    assert types == {"PRD_CONFLICT", "COMMITMENT", "SUGGESTED_REPLY"}
    conflict = next(card for card in runtime.store.cards if card.type == "PRD_CONFLICT")
    acted = client.post(f"/api/cards/{conflict.id}/action", json={"action": "raise"})
    assert any(item["what"] == conflict.title for item in acted.json()["meeting"]["meeting"]["commitments"])
    wiped = client.post("/api/meeting/delete")
    assert wiped.json()["meeting"]["cards"] == []
    listening = client.post("/api/listen", json={"on": True})
    assert listening.json()["listening"] is True

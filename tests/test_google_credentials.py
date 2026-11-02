import json
import logging
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from escort.context.sources import google_auth
from escort.context.sources.registry import Connectors
from escort.desktop.api import create_desktop_app
from escort.desktop.runtime import DesktopRuntime
from escort.desktop.secret_store import MemorySecretStore
from escort.store import MeetingStore

SECRET = "GOCSPX-super-secret-value"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("ESCORT_CONNECTORS", "ESCORT_GOOGLE_CLIENT_ID", "ESCORT_GOOGLE_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)


def _bundle(tmp_path: Path, client_id="bundled-id", client_secret="bundled-secret") -> Path:
    path = tmp_path / "google_client.json"
    path.write_text(json.dumps({"client_id": client_id, "client_secret": client_secret}), encoding="utf-8")
    return path


def _auth(tmp_path: Path, setup_id="", setup_secret="", bundled: Path | None = None):
    secrets = MemorySecretStore()
    if setup_secret:
        secrets.set(google_auth.CLIENT_SECRET_KEY, setup_secret)
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    connectors = Connectors(
        secrets,
        client_id_getter=lambda: setup_id,
        http=http,
        bundled_path=lambda: bundled or tmp_path / "missing.json",
    )
    return connectors


def test_nothing_configured(tmp_path):
    google = _auth(tmp_path).google
    assert not google.configured()
    assert google.credential_source() is None
    assert google.client_secret() is None


def test_precedence_env_over_setup_over_bundled(tmp_path, monkeypatch):
    bundled = _bundle(tmp_path)
    google = _auth(tmp_path, setup_id="setup-id", setup_secret="setup-secret", bundled=bundled).google
    monkeypatch.setenv("ESCORT_GOOGLE_CLIENT_ID", "env-id")
    monkeypatch.setenv("ESCORT_GOOGLE_CLIENT_SECRET", "env-secret")
    assert (google.credential_source(), google.client_id(), google.client_secret()) == ("env", "env-id", "env-secret")
    monkeypatch.delenv("ESCORT_GOOGLE_CLIENT_ID")
    monkeypatch.delenv("ESCORT_GOOGLE_CLIENT_SECRET")
    assert (google.credential_source(), google.client_id(), google.client_secret()) == ("setup", "setup-id", "setup-secret")
    google_none = _auth(tmp_path, bundled=bundled).google
    assert (google_none.credential_source(), google_none.client_id(), google_none.client_secret()) == ("bundled", "bundled-id", "bundled-secret")


def test_setup_id_never_borrows_the_bundled_secret(tmp_path):
    google = _auth(tmp_path, setup_id="setup-id", bundled=_bundle(tmp_path)).google
    assert google.client_id() == "setup-id"
    assert google.client_secret() is None


def test_bundled_file_that_is_broken_is_ignored(tmp_path):
    bad = tmp_path / "google_client.json"
    bad.write_text("{not json", encoding="utf-8")
    assert not _auth(tmp_path, bundled=bad).google.configured()
    bad.write_text("[1, 2]", encoding="utf-8")
    assert not _auth(tmp_path, bundled=bad).google.configured()


def test_bundled_path_dev_and_frozen(tmp_path, monkeypatch):
    from escort.paths import repo_root

    assert google_auth.bundled_client_path() == repo_root() / "google_client.json"
    exe = tmp_path / "Escort.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert google_auth.bundled_client_path() == tmp_path.resolve() / "google_client.json"


def test_status_exposes_source_but_never_the_secret(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    connectors = _auth(tmp_path, setup_id="setup-id", setup_secret=SECRET)
    row = connectors.status()["providers"][0]
    assert row["configured"] is True
    assert row["credentials_source"] == "setup"
    assert row["has_client_secret"] is True
    assert SECRET not in json.dumps(connectors.status())
    assert SECRET not in caplog.text

    bundled = _auth(tmp_path, bundled=_bundle(tmp_path, client_secret=SECRET))
    assert bundled.status()["providers"][0]["credentials_source"] == "bundled"
    assert SECRET not in json.dumps(bundled.status())


def test_save_via_setup_enables_connect_and_hides_the_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(google_auth, "bundled_client_path", lambda: tmp_path / "missing.json")
    runtime = DesktopRuntime(MeetingStore(tmp_path / "media"), MemorySecretStore(), tmp_path / "config.json")
    client = TestClient(create_desktop_app(runtime))
    before = client.get("/api/connectors/status").json()["providers"][0]
    assert before["configured"] is False and before["credentials_source"] is None

    saved = client.post("/api/setup", json={"google_client_id": "  my-id.apps.googleusercontent.com ", "google_client_secret": f" {SECRET} "})
    assert saved.status_code == 200
    assert SECRET not in saved.text
    assert SECRET not in client.get("/api/status").text
    assert SECRET not in (tmp_path / "config.json").read_text(encoding="utf-8")
    assert runtime.secrets.get("google_client_secret") == SECRET

    row = client.get("/api/connectors/status").json()["providers"][0]
    assert row["configured"] is True
    assert row["credentials_source"] == "setup"
    assert row["has_client_secret"] is True
    assert runtime.connectors.google.client_id() == "my-id.apps.googleusercontent.com"

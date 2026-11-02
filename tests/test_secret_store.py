from pathlib import Path
from escort.desktop.secret_store import KeyringSecretStore


def test_keyring_secret_store_backup_and_recovery(tmp_path: Path, monkeypatch):
    backup_file = tmp_path / "secrets.json"
    fake_keyring: dict[str, str] = {}

    class FakeKeyring:
        def get_password(self, service, name):
            return fake_keyring.get(f"{service}:{name}")

        def set_password(self, service, name, value):
            fake_keyring[f"{service}:{name}"] = value

        def delete_password(self, service, name):
            fake_keyring.pop(f"{service}:{name}", None)

    import keyring
    fk = FakeKeyring()
    monkeypatch.setattr(keyring, "get_password", fk.get_password)
    monkeypatch.setattr(keyring, "set_password", fk.set_password)
    monkeypatch.setattr(keyring, "delete_password", fk.delete_password)

    store = KeyringSecretStore(backup_path=backup_file)
    store.set("meta_api_key", "sk_test_key_123")
    assert store.get("meta_api_key") == "sk_test_key_123"
    assert backup_file.is_file()

    # Simulate keyring loss: memory cleared, re-read restores keyring from backup
    fake_keyring.clear()
    store2 = KeyringSecretStore(backup_path=backup_file)
    assert store2.get("meta_api_key") == "sk_test_key_123"
    assert fake_keyring.get("escort:meta_api_key") == "sk_test_key_123"


from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


class MemorySecretStore:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self._values.get(name)

    def set(self, name: str, value: str) -> None:
        self._values[name] = value

    def delete(self, name: str) -> None:
        self._values.pop(name, None)


def _secrets_file() -> Path:
    override = os.environ.get("ESCORT_CONFIG_DIR")
    if override:
        base = Path(override)
    elif os.environ.get("APPDATA"):
        base = Path(os.environ["APPDATA"]) / "Escort"
    else:
        base = Path.home() / ".config" / "Escort"
    return base / "secrets.json"


class KeyringSecretStore:
    service = "escort"

    def __init__(self, backup_path: Path | None = None) -> None:
        self._backup = backup_path or _secrets_file()

    def _read_backup(self) -> dict[str, str]:
        try:
            if not self._backup.is_file():
                return {}
            data = json.loads(self._backup.read_text(encoding="utf-8"))
            return {str(k): str(v) for k, v in data.items() if isinstance(k, str) and v is not None} if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_backup(self, data: dict[str, str]) -> None:
        try:
            self._backup.parent.mkdir(parents=True, exist_ok=True)
            self._backup.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            return

    def get(self, name: str) -> str | None:
        try:
            import keyring

            val = keyring.get_password(self.service, name)
            if val is not None:
                if name != "__probe__":
                    backup = self._read_backup()
                    if backup.get(name) != val:
                        backup[name] = val
                        self._write_backup(backup)
                return val
        except Exception:
            pass
        if name == "__probe__":
            return None
        val = self._read_backup().get(name)
        if val is not None:
            try:
                import keyring

                keyring.set_password(self.service, name, val)
            except Exception:
                pass
        return val

    def set(self, name: str, value: str) -> None:
        if name != "__probe__":
            backup = self._read_backup()
            backup[name] = value
            self._write_backup(backup)
        try:
            import keyring

            keyring.set_password(self.service, name, value)
        except Exception:
            if name == "__probe__":
                raise

    def delete(self, name: str) -> None:
        if name != "__probe__":
            backup = self._read_backup()
            if name in backup:
                backup.pop(name, None)
                self._write_backup(backup)
        try:
            import keyring

            keyring.delete_password(self.service, name)
        except Exception:
            return


def open_secret_store() -> SecretStore:
    try:
        store = KeyringSecretStore()
        store.set("__probe__", "1")
        store.delete("__probe__")
        return store
    except Exception:
        return MemorySecretStore()

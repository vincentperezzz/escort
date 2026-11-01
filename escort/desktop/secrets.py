from __future__ import annotations

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


class KeyringSecretStore:
    service = "escort"

    def get(self, name: str) -> str | None:
        import keyring

        return keyring.get_password(self.service, name)

    def set(self, name: str, value: str) -> None:
        import keyring

        keyring.set_password(self.service, name, value)

    def delete(self, name: str) -> None:
        import keyring

        try:
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

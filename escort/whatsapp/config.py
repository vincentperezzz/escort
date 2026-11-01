from __future__ import annotations

import os
from dataclasses import dataclass


def digits(number: str) -> str:
    return "".join(ch for ch in number if ch.isdigit())


@dataclass(frozen=True)
class WhatsAppConfig:
    verify_token: str
    app_secret: str | None
    allowed_numbers: tuple[str, ...]
    access_token: str
    phone_number_id: str
    graph_version: str = "v22.0"
    state_url: str | None = None
    model_api_key: str | None = None
    model_base: str = "https://api.meta.ai/v1"
    spark_model: str = "muse-spark-1.3"

    def allows(self, number: str) -> bool:
        wanted = digits(number)
        return bool(wanted) and wanted in {digits(item) for item in self.allowed_numbers}


def load_whatsapp_config() -> WhatsAppConfig:
    raw_numbers = os.environ.get("WHATSAPP_ALLOWED_NUMBERS", "")
    numbers = tuple(part.strip() for part in raw_numbers.split(",") if part.strip())
    secret = os.environ.get("WHATSAPP_APP_SECRET") or None
    if secret is not None and not secret.strip():
        secret = None
    key = os.environ.get("MODEL_API_KEY") or os.environ.get("META_API_KEY") or None
    if key is not None and not key.strip():
        key = None
    version = os.environ.get("WHATSAPP_GRAPH_VERSION", "v22.0").strip() or "v22.0"
    if not version.startswith("v"):
        version = f"v{version}"
    state = os.environ.get("ESCORT_STATE_URL", "http://127.0.0.1:8747/panel/state")
    return WhatsAppConfig(
        verify_token=os.environ.get("WHATSAPP_VERIFY_TOKEN", ""),
        app_secret=secret,
        allowed_numbers=numbers,
        access_token=os.environ.get("WHATSAPP_ACCESS_TOKEN", ""),
        phone_number_id=os.environ.get("WHATSAPP_PHONE_NUMBER_ID", ""),
        graph_version=version,
        state_url=state or None,
        model_api_key=key,
        model_base=os.environ.get("META_API_BASE", "https://api.meta.ai/v1").rstrip("/"),
        spark_model=os.environ.get("META_SPARK_MODEL", "muse-spark-1.3"),
    )

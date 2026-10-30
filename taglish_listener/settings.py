from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    api_key: str | None
    base_url: str
    asr_model: str
    spark_model: str
    sam_model: str
    language_bias: tuple[str, ...]
    spark_interval_s: float

    def redacted_key(self) -> str:
        if not self.api_key:
            return ""
        if len(self.api_key) <= 6:
            return "set"
        return self.api_key[:4] + "…"


def load_settings() -> Settings:
    key = os.environ.get("MODEL_API_KEY") or os.environ.get("META_API_KEY") or None
    if key is not None and not key.strip():
        key = None
    base = os.environ.get("META_API_BASE", "https://api.meta.ai/v1").rstrip("/")
    return Settings(
        api_key=key,
        base_url=base,
        asr_model=os.environ.get("META_ASR_MODEL", "muse-voice-transcribe-1.0"),
        spark_model=os.environ.get("META_SPARK_MODEL", "muse-spark-1.3"),
        sam_model=os.environ.get("META_SAM_MODEL", "sam-3.1"),
        language_bias=("Tagalog", "English"),
        spark_interval_s=float(os.environ.get("TAGLISH_SPARK_INTERVAL", "10")),
    )

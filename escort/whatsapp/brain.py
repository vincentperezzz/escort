from __future__ import annotations

import httpx

from escort.spark.muse import spark_model_name
from escort.whatsapp.config import WhatsAppConfig


def ask_meeting(config: WhatsAppConfig, question: str, context: str, http: httpx.Client | None = None) -> str:
    if not config.model_api_key:
        return (
            "Muse is not keyed on this machine, so this is the local read. "
            f"{context[:1200]}"
        )
    client = http or httpx.Client(timeout=60)
    owns = http is None
    try:
        response = client.post(
            f"{config.model_base}/chat/completions",
            headers={
                "Authorization": f"Bearer {config.model_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": spark_model_name(config.spark_model),
                "reasoning_effort": "minimal",
                "max_completion_tokens": 1600,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are Escort, texting Boss on WhatsApp during a live Taglish client call. "
                            "Muse is the brain. Answer in a short text he can read on a phone. "
                            "Use the meeting state. Do not invent a PRD section."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Meeting state:\n{context[:12000]}\n\nQuestion:\n{question}",
                    },
                ],
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
        return str(content).strip() or "Muse came back empty."
    finally:
        if owns:
            client.close()

from __future__ import annotations

import httpx

from escort.settings import Settings, load_settings
from escort.spark.muse import spark_model_name


def prep_chat(message: str, context_markdown: str, history: list[dict[str, str]], http: httpx.Client | None = None) -> str:
    settings = load_settings()
    if not settings.api_key:
        return (
            "Set MODEL_API_KEY in Setup first. "
            "Then ask things like: Ano ang dapat kong bantayan sa BayaniPay call?"
        )
    transcript = "\n".join(f"{turn['role']}: {turn['content']}" for turn in history[-6:])
    prompt = (
        "You are Escort, a pre-meeting copilot for Taglish client calls. "
        "Answer in short, natural Taglish or English mix. "
        "Use only the loaded context. Say when something is not in the notes.\n\n"
        f"CONTEXT:\n{context_markdown[:24000]}\n\n"
        f"CHAT:\n{transcript}\n\n"
        f"USER:\n{message}\n\n"
        "Reply with one or two short paragraphs. No bullet lists unless asked."
    )
    client = http or httpx.Client(timeout=60)
    owns = http is None
    try:
        response = client.post(
            f"{settings.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": spark_model_name(settings.spark_model),
                "reasoning_effort": "minimal",
                "max_completion_tokens": 1600,
                "messages": [
                    {"role": "system", "content": "Pre-meeting prep only. Be concrete and cite note headings when useful."},
                    {"role": "user", "content": prompt},
                ],
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
        return str(content).strip() or "Walang sagot mula kay Muse."
    except Exception as err:
        return f"Muse is quiet right now ({err}). Try again after checking the API key."
    finally:
        if owns:
            client.close()

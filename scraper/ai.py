from __future__ import annotations
import os, json
import httpx
from .net import shared_ssl


def configured() -> bool:
    return bool(os.getenv("AI_API_KEY"))


async def chat_json(system: str, user: dict, max_chars: int = 120000) -> dict:
    """Call an OpenAI-compatible chat endpoint and return its JSON object reply."""
    key = os.getenv("AI_API_KEY")
    if not key: raise RuntimeError("AI_API_KEY is not configured")
    base = os.getenv("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("AI_MODEL", "gpt-4.1-mini")
    payload = {"model": model, "temperature": 0, "response_format": {"type": "json_object"},
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)[:max_chars]}]}
    async with httpx.AsyncClient(verify=shared_ssl(), timeout=90) as client:
        r = await client.post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=payload)
        r.raise_for_status(); data = r.json()
    return json.loads(data["choices"][0]["message"]["content"])

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from .llm_json import deepseek_api_key, live_llm_enabled, load_dotenv, load_prompt, parse_json_content, scrub_why_this_event

DEFAULT_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"


def deepseek_available() -> bool:
    return live_llm_enabled() and bool(deepseek_api_key())


def _redact(text: str) -> str:
    key = deepseek_api_key()
    if key and key in text:
        return text.replace(key, "***")
    return text


def complete_json(user_prompt: str, *, system: str = "只输出合法 JSON 对象，不要 Markdown 前言。") -> dict[str, Any]:
    load_dotenv()
    key = deepseek_api_key()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY missing")
    base = (os.environ.get("DEEPSEEK_API_BASE") or DEFAULT_BASE).rstrip("/")
    model = os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(_redact(f"deepseek HTTP {exc.code}: {detail}")) from exc
    content = (((body.get("choices") or [{}])[0].get("message") or {}).get("content")) or ""
    parsed = parse_json_content(content)
    if not isinstance(parsed, dict):
        raise RuntimeError("deepseek returned non-object JSON")
    parsed["_model_id"] = body.get("model") or model
    return parsed


def call_deepseek(evidence: dict[str, Any]) -> dict[str, Any]:
    return scrub_why_this_event(complete_json(load_prompt(evidence)))

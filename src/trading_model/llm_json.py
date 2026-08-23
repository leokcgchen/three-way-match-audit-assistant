from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


def load_dotenv() -> None:
    roots = [
        Path.cwd(),
        Path(__file__).resolve().parents[1],
        Path(__file__).resolve().parents[2],
    ]
    seen: set[Path] = set()
    for root in roots:
        path = (root / ".env").resolve()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def live_llm_enabled() -> bool:
    load_dotenv()
    flag = os.environ.get("TRADING_MODEL_LIVE_LLM", "1").strip().lower()
    return flag not in {"0", "false", "off", "no"}


def deepseek_api_key() -> str:
    load_dotenv()
    return (os.environ.get("DEEPSEEK_API_KEY") or "").strip()


def compact_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    harvest = dict(evidence.get("harvest") or {})
    harvest.pop("full_text", None)
    return {
        "harvest": {
            "spans": harvest.get("spans") or [],
            "nominal_code": harvest.get("nominal_code"),
            "named_place_or_port": harvest.get("named_place_or_port"),
            "version": harvest.get("version"),
        },
        "slots": evidence.get("slots") or [],
        "date_inventory": evidence.get("date_inventory") or [],
        "control": evidence.get("control") or {},
        "rag_hits": evidence.get("rag_hits") or [],
        "contract_hits": evidence.get("contract_hits") or [],
        "judgment_chunks": evidence.get("judgment_chunks") or [],
        "documents": evidence.get("documents") or [],
        "embedder": evidence.get("embedder"),
    }


def load_prompt(evidence: dict[str, Any]) -> str:
    path = Path(__file__).resolve().parent / "prompts" / "trading_model_v1.md"
    template = path.read_text(encoding="utf-8")
    blob = json.dumps(compact_evidence(evidence), ensure_ascii=False, indent=2)
    return template.replace("{{TRANSACTION_EVIDENCE_JSON}}", blob)


def parse_json_content(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.I)
    if fenced:
        raw = fenced.group(1).strip()
    return json.loads(raw)


def scrub_why_this_event(obj: Any) -> Any:
    if isinstance(obj, dict):
        obj.pop("why_this_event", None)
        for key, value in list(obj.items()):
            obj[key] = scrub_why_this_event(value)
        return obj
    if isinstance(obj, list):
        return [scrub_why_this_event(x) for x in obj]
    return obj


def validate_excerpts(llm_raw: dict[str, Any], source_text: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            if "verbatim_excerpt" in node:
                excerpt = str(node.get("verbatim_excerpt") or "")
                ok = (not excerpt) or (excerpt in source_text)
                findings.append({"path": path, "ok": ok, "excerpt": excerpt[:120]})
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")

    walk(llm_raw, "$")
    return findings

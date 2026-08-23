from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Optional

COLLECTION = "trade_mode_v1"
DEFAULT_URL = "http://127.0.0.1:8000"
_TENANT = "default_tenant"
_DB = "default_database"


def chroma_http_enabled() -> bool:
    flag = os.environ.get("TRADING_MODEL_CHROMA_HTTP", "auto").strip().lower()
    return flag not in {"0", "false", "off", "no"}


def chroma_base_url() -> str:
    return os.environ.get("TRADING_MODEL_CHROMA_URL", DEFAULT_URL).rstrip("/")


def _timeout() -> float:
    try:
        return float(os.environ.get("TRADING_MODEL_CHROMA_TIMEOUT", "0.4"))
    except ValueError:
        return 0.4


def _request(
    method: str,
    url: str,
    body: Optional[dict[str, Any]] = None,
    *,
    timeout: Optional[float] = None,
) -> tuple[int, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout or _timeout()) as resp:
            raw = resp.read().decode("utf-8") if resp.length != 0 else ""
            parsed = json.loads(raw) if raw.strip() else {}
            return resp.status, parsed
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            parsed = {"error": raw[:300]}
        return exc.code, parsed


def detect_api(base: Optional[str] = None) -> Optional[str]:
    if not chroma_http_enabled():
        return None
    root = (base or chroma_base_url()).rstrip("/")
    for version, path in (("v2", "/api/v2/heartbeat"), ("v1", "/api/v1/heartbeat")):
        try:
            status, _ = _request("GET", root + path)
            if 200 <= status < 300:
                return version
        except Exception:
            continue
    return None


def parse_query_hits(payload: dict[str, Any], *, k: int) -> list[dict[str, Any]]:
    """Normalize Chroma v1/v2 query JSON into retrieve() hits."""
    ids = payload.get("ids") or []
    docs = payload.get("documents") or []
    metas = payload.get("metadatas") or []
    dists = payload.get("distances") or []
    if ids and isinstance(ids[0], list):
        ids, docs, metas, dists = (
            ids[0],
            (docs[0] if docs else []),
            (metas[0] if metas else []),
            (dists[0] if dists else []),
        )
    out: list[dict[str, Any]] = []
    for i, doc in enumerate(docs[:k]):
        meta = metas[i] if i < len(metas) and isinstance(metas[i], dict) else {}
        dist = dists[i] if i < len(dists) else None
        score = 1.0 - float(dist) if dist is not None else 0.0
        out.append(
            {
                "source": str(meta.get("source") or ""),
                "title": str(meta.get("title") or ""),
                "excerpt": str(doc or "")[:800],
                "rank": -score,
                "backend": "chroma",
                "score": score,
            }
        )
    return out


def _v1_upsert(base: str, records: list[dict[str, Any]]) -> None:
    name = COLLECTION
    _request("DELETE", f"{base}/api/v1/collections/{name}")
    _request(
        "POST",
        f"{base}/api/v1/collections",
        {"name": name, "metadata": {"hnsw:space": "cosine"}, "get_or_create": True},
    )
    _request(
        "POST",
        f"{base}/api/v1/collections/{name}/add",
        {
            "ids": [r["id"] for r in records],
            "embeddings": [r["vector"] for r in records],
            "documents": [r["body"] for r in records],
            "metadatas": [{"source": r["source"], "title": r["title"]} for r in records],
        },
    )


def _v2_collection_id(base: str) -> Optional[str]:
    status, payload = _request(
        "GET",
        f"{base}/api/v2/tenants/{_TENANT}/databases/{_DB}/collections",
    )
    rows = payload if isinstance(payload, list) else payload.get("collections") or []
    if status >= 300:
        return None
    for row in rows:
        if isinstance(row, dict) and row.get("name") == COLLECTION:
            return str(row.get("id") or row.get("uuid") or "")
    status, created = _request(
        "POST",
        f"{base}/api/v2/tenants/{_TENANT}/databases/{_DB}/collections",
        {"name": COLLECTION, "metadata": {"hnsw:space": "cosine"}},
    )
    if isinstance(created, dict) and (created.get("id") or created.get("uuid")):
        return str(created.get("id") or created.get("uuid"))
    status, payload = _request(
        "GET",
        f"{base}/api/v2/tenants/{_TENANT}/databases/{_DB}/collections",
    )
    rows = payload if isinstance(payload, list) else []
    for row in rows:
        if isinstance(row, dict) and row.get("name") == COLLECTION:
            return str(row.get("id") or row.get("uuid") or "")
    return None


def _v2_upsert(base: str, records: list[dict[str, Any]]) -> None:
    cid = _v2_collection_id(base)
    if not cid:
        raise RuntimeError("chroma v2 collection id missing")
    _request(
        "POST",
        f"{base}/api/v2/tenants/{_TENANT}/databases/{_DB}/collections/{cid}/delete",
        {"where": {"source": {"$ne": ""}}},
    )
    _request(
        "POST",
        f"{base}/api/v2/tenants/{_TENANT}/databases/{_DB}/collections/{cid}/add",
        {
            "ids": [r["id"] for r in records],
            "embeddings": [r["vector"] for r in records],
            "documents": [r["body"] for r in records],
            "metadatas": [{"source": r["source"], "title": r["title"]} for r in records],
        },
    )


def upsert_records(records: list[dict[str, Any]], *, base: Optional[str] = None) -> bool:
    api = detect_api(base)
    if not api or not records:
        return False
    root = (base or chroma_base_url()).rstrip("/")
    if api == "v2":
        _v2_upsert(root, records)
    else:
        _v1_upsert(root, records)
    return True


def query_records(vector: list[float], *, k: int = 8, base: Optional[str] = None) -> list[dict[str, Any]]:
    api = detect_api(base)
    if not api:
        return []
    root = (base or chroma_base_url()).rstrip("/")
    body = {
        "query_embeddings": [vector],
        "n_results": k,
        "include": ["documents", "metadatas", "distances"],
    }
    if api == "v1":
        status, payload = _request("POST", f"{root}/api/v1/collections/{COLLECTION}/query", body)
    else:
        cid = _v2_collection_id(root)
        if not cid:
            return []
        status, payload = _request(
            "POST",
            f"{root}/api/v2/tenants/{_TENANT}/databases/{_DB}/collections/{cid}/query",
            body,
        )
    if status >= 300 or not isinstance(payload, dict):
        return []
    return parse_query_hits(payload, k=k)

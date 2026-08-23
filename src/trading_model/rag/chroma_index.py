from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from src.trading_model.contract_rag.chroma_store import EmbedDimError, cosine

from .chroma_http import detect_api, query_records, upsert_records
from .embeddings import embed_texts as hash_embed_texts

COLLECTION = "trade_mode_v1"


def _encode(texts: list[str]) -> tuple[str, list[list[float]]]:
    try:
        from src.trading_model.contract_rag.embedder import get_embedder

        enc = get_embedder()
        return enc.name, enc.encode(texts)
    except Exception:
        return "char_ngram_v1", hash_embed_texts(texts)


def chroma_dir(db_path: Path) -> Path:
    return Path(db_path).parent / "chroma"


def chroma_available() -> bool:
    return detect_api() is not None


def _write_sidecar(path: Path, records: list[dict[str, Any]], embed_name: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    dim = len(records[0]["vector"]) if records and records[0].get("vector") else None
    payload = {
        "collection": COLLECTION,
        "embedding": embed_name,
        "dim": dim,
        "records": [
            {
                "id": r["id"],
                "source": r["source"],
                "title": r["title"],
                "body": r["body"],
                "vector": r["vector"],
            }
            for r in records
        ],
    }
    (path / "collection.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def rebuild_chroma(path: Path, chunks: list[dict[str, str]]) -> int:
    path = Path(path)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    records = []
    texts = [c["body"] for c in chunks]
    embed_name, vectors = _encode(texts)
    for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
        records.append(
            {
                "id": f"c{i+1}",
                "source": chunk["source"],
                "title": chunk["title"],
                "body": chunk["body"],
                "vector": vec,
            }
        )
    _write_sidecar(path, records, embed_name)
    try:
        upsert_records(records)
    except Exception:
        pass
    return len(records)


def query_chroma(path: Path, query: str, *, k: int = 8) -> list[dict[str, Any]]:
    _, q_vectors = _encode([query])
    qv = q_vectors[0]
    try:
        hits = query_records(qv, k=k)
        if hits:
            return hits
    except Exception:
        pass
    sidecar = Path(path) / "collection.json"
    if not sidecar.exists():
        return []
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    stored_dim = data.get("dim")
    records = data.get("records") or []
    if stored_dim is None and records:
        stored_dim = len(records[0].get("vector") or [])
    if stored_dim and len(qv) != int(stored_dim):
        raise EmbedDimError(
            f"embed dim mismatch: collection={stored_dim} ({data.get('embedding')}) query={len(qv)}"
        )
    scored = []
    for rec in records:
        vec = rec.get("vector") or []
        if not vec:
            continue
        scored.append((cosine(qv, vec), rec))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for score, rec in scored[:k]:
        out.append(
            {
                "source": rec["source"],
                "title": rec["title"],
                "excerpt": rec["body"][:800],
                "rank": -float(score),
                "backend": "chroma",
                "score": float(score),
            }
        )
    return out

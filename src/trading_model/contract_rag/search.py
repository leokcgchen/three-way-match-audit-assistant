from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Optional

from .chroma_store import get_collection
from .embedder import Embedder, get_embedder
from .sqlite_store import all_synonyms, chroma_path, connect, default_data_root, fetch_paragraphs, init_stores

RRF_K = 60
TRADE_INTENT = re.compile(
    r"贸易术语|运输条款|价格条款|交货条件|incoterm|trade term|transport mode|交货方式",
    re.I,
)


def rrf_merge(rank_lists: list[list[str]], *, k: int = RRF_K) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranked in rank_lists:
        for rank, doc_id in enumerate(ranked, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def _quote(term: str) -> str:
    return '"' + term.replace('"', "") + '"'


def _fts_query(conn: sqlite3.Connection, query: str) -> str:
    synonyms = all_synonyms(conn)
    tokens = re.findall(r"[\w\u4e00-\u9fff]{2,}", query, re.I)
    extra: list[str] = []
    lowered = {t.lower() for t in tokens}
    for standard, synonym in synonyms:
        if standard.lower() in lowered or synonym.lower() in lowered:
            extra.extend([standard, synonym])
    if TRADE_INTENT.search(query) or not tokens:
        extra.extend(syn for _, syn in synonyms)
    bag: list[str] = []
    seen = set()
    for item in tokens + extra:
        key = item.lower()
        if key in seen or len(item) < 2:
            continue
        seen.add(key)
        bag.append(_quote(item))
        if len(bag) >= 40:
            break
    return " OR ".join(bag) if bag else '""'


def search_fts(conn: sqlite3.Connection, query: str, *, n: int = 20) -> list[str]:
    sql = (
        "SELECT p.id FROM paragraphs p "
        "JOIN paragraphs_fts f ON f.rowid = p.rowid "
        "WHERE paragraphs_fts MATCH ? "
        "ORDER BY bm25(paragraphs_fts) LIMIT ?"
    )
    try:
        rows = conn.execute(sql, (_fts_query(conn, query), n)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    if rows:
        return [r["id"] for r in rows]
    # synonym LIKE fallback
    likes = []
    args: list[Any] = []
    for _, synonym in all_synonyms(conn)[:30]:
        likes.append("p.raw_text LIKE ?")
        args.append(f"%{synonym}%")
    if not likes:
        return []
    sql = f"SELECT DISTINCT p.id FROM paragraphs p WHERE {' OR '.join(likes)} LIMIT ?"
    args.append(n)
    return [r["id"] for r in conn.execute(sql, args).fetchall()]


def search_chroma(
    persist_dir: Path,
    query: str,
    *,
    n: int = 20,
    embedder: Optional[Embedder] = None,
) -> list[str]:
    encoder = embedder or get_embedder()
    vec = encoder.encode([query])[0]
    dim = getattr(encoder, "dim", len(vec))
    result = get_collection(persist_dir).query(
        query_embeddings=[vec],
        n_results=n,
        embedder_name=encoder.name,
        dim=dim,
    )
    ids = (result.get("ids") or [[]])[0]
    return [str(i) for i in ids]


def hybrid_search(
    query: str,
    *,
    data_root: Optional[Path] = None,
    top_n: int = 8,
    chroma_n: int = 20,
    fts_n: int = 20,
    rrf_k: int = RRF_K,
    embedder: Optional[Embedder] = None,
) -> list[dict[str, Any]]:
    root = default_data_root(data_root)
    init_stores(root)
    conn = connect(root)
    fts_ids = search_fts(conn, query, n=fts_n)
    chroma_ids = search_chroma(chroma_path(root), query, n=chroma_n, embedder=embedder)
    fused = rrf_merge([fts_ids, chroma_ids], k=rrf_k)
    ordered = [doc_id for doc_id, _ in fused[:top_n]]
    rows = fetch_paragraphs(conn, ordered)
    conn.close()
    scores = dict(fused)
    for row in rows:
        row["rrf_score"] = scores.get(row["id"], 0.0)
    return rows

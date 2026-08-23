from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

from .chroma_index import chroma_dir, query_chroma, rebuild_chroma

_ROOT = Path(__file__).resolve().parent
_CORPUS = _ROOT / "corpus"
_CONFIG = _ROOT.parent / "config"

_CJK_PHRASE = re.compile(r"[\u4e00-\u9fff]{2,16}")
_LATIN_PHRASE = re.compile(r"[A-Za-z][A-Za-z0-9 /®._-]{1,40}")


def default_db_path(data_root: Optional[Path] = None) -> Path:
    if data_root is not None:
        return Path(data_root) / "rag" / "index.sqlite"
    return _ROOT / "index.sqlite"


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    ddl_trigram = (
        "CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5("
        "source, title, body, tokenize='trigram')"
    )
    ddl_unicode = (
        "CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5("
        "source, title, body, tokenize='unicode61')"
    )
    try:
        conn.execute(ddl_trigram)
    except sqlite3.OperationalError:
        conn.execute(ddl_unicode)
    return conn


def _split_markdown(text: str, source: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    title = source
    buf: list[str] = []
    for line in text.splitlines():
        if line.startswith("#"):
            if buf:
                body = "\n".join(buf).strip()
                if len(body) >= 12:
                    blocks.append((title, body))
                buf = []
            title = line.lstrip("# ").strip() or source
        else:
            buf.append(line)
    body = "\n".join(buf).strip()
    if len(body) >= 12:
        blocks.append((title, body))
    return blocks


def _iter_seed_docs() -> Iterable[tuple[str, str, bool]]:
    for path in sorted(_CORPUS.glob("*.md")):
        yield path.name, path.read_text(encoding="utf-8"), True
    for name in ("concept_lexicon.json", "section_atlas.json", "field_catalog_trade.json"):
        p = _CONFIG / name
        if p.exists():
            yield name, p.read_text(encoding="utf-8"), False


def rebuild_index(db_path: Path) -> int:
    db_path = Path(db_path)
    if db_path.exists():
        db_path.unlink()
    conn = _connect(db_path)
    chunks: list[dict[str, str]] = []
    n = 0
    for source, text, is_md in _iter_seed_docs():
        rows = _split_markdown(text, source) if is_md else [(source, text)]
        for title, body in rows:
            conn.execute(
                "INSERT INTO chunks(source, title, body) VALUES (?,?,?)",
                (source, title, body),
            )
            chunks.append({"source": source, "title": title, "body": body})
            n += 1
    conn.commit()
    conn.close()
    rebuild_chroma(chroma_dir(db_path), chunks)
    return n


def _ensure_index(db_path: Path) -> None:
    db_path = Path(db_path)
    if not db_path.exists():
        rebuild_index(db_path)


def _fts_query(raw: str) -> str:
    tokens = re.findall(r"[\w\u4e00-\u9fff]{2,}", raw, re.I)
    if not tokens:
        return '""'
    return " OR ".join('"' + t.replace('"', "") + '"' for t in tokens[:12])


def _like_fallback(conn: sqlite3.Connection, query: str, k: int) -> list[tuple]:
    tokens = re.findall(r"[\w\u4e00-\u9fff]{2,}", query, re.I)[:6]
    if not tokens:
        return []
    clauses = " OR ".join(["body LIKE ?" for _ in tokens])
    args = [f"%{t}%" for t in tokens]
    sql = f"SELECT source, title, body, 0 FROM chunks WHERE {clauses} LIMIT ?"
    return conn.execute(sql, (*args, k)).fetchall()


def _merge_hits(
    fts_hits: list[dict[str, Any]],
    vec_hits: list[dict[str, Any]],
    k: int,
) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for hit in fts_hits + vec_hits:
        key = (str(hit.get("source")), str(hit.get("title")), str(hit.get("excerpt") or "")[:80])
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
        if len(out) >= k:
            break
    return out


def retrieve(
    query: str,
    *,
    db_path: Path,
    k: int = 8,
) -> list[dict[str, Any]]:
    db_path = Path(db_path)
    _ensure_index(db_path)
    conn = _connect(db_path)
    rows: list[tuple] = []
    try:
        rows = conn.execute(
            "SELECT source, title, body, rank FROM chunks "
            "WHERE chunks MATCH ? ORDER BY rank LIMIT ?",
            (_fts_query(query), k),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    if not rows:
        rows = _like_fallback(conn, query, k)
    conn.close()
    fts_hits = []
    for source, title, body, rank in rows:
        fts_hits.append(
            {
                "source": source,
                "title": title,
                "excerpt": body[:800],
                "rank": rank,
                "backend": "fts",
            }
        )
    vec_hits = query_chroma(chroma_dir(db_path), query, k=k)
    return _merge_hits(fts_hits, vec_hits, k)


def related_phrases(query: str, *, db_path: Path, k: int = 6) -> list[str]:
    hits = retrieve(query, db_path=db_path, k=k)
    md_hits = [h for h in hits if str(h.get("source") or "").endswith(".md")]
    if not md_hits:
        md_hits = hits
    buckets: list[list[str]] = []
    for hit in md_hits:
        blob = str(hit.get("excerpt") or "")
        ordered: list[str] = []
        local = set()

        def _push(token: str) -> None:
            t = token.strip(" /.-")
            key = t.lower()
            if len(t) < 2 or key in local:
                return
            local.add(key)
            ordered.append(t)

        for tok in _CJK_PHRASE.findall(blob) + _LATIN_PHRASE.findall(blob):
            _push(tok)
            for part in re.split(r"[或及并，,：:；;/]", tok):
                _push(part)
                if part.endswith("日") and len(part) >= 5:
                    _push(part[:-1])
        buckets.append(ordered)

    phrases: list[str] = []
    seen: set[str] = set()
    depth = 0
    while len(phrases) < 80:
        progressed = False
        for ordered in buckets:
            if depth < len(ordered):
                t = ordered[depth]
                key = t.lower()
                if key not in seen:
                    seen.add(key)
                    phrases.append(t)
                progressed = True
                if len(phrases) >= 80:
                    return phrases
        if not progressed:
            break
        depth += 1
    return phrases

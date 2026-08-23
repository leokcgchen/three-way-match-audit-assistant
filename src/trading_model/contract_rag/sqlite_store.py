from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

from .trade_terms import TRADE_TERMS

SCHEMA = """
CREATE TABLE IF NOT EXISTS paragraphs (
    rowid INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    seq INTEGER NOT NULL,
    source_file TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    markdown TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_paragraphs_source ON paragraphs(source_file, seq);

CREATE VIRTUAL TABLE IF NOT EXISTS paragraphs_fts USING fts5(
    raw_text,
    source_file,
    content='paragraphs',
    content_rowid='rowid',
    tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS trade_terms (
    id INTEGER PRIMARY KEY,
    standard_term TEXT NOT NULL,
    synonym TEXT NOT NULL,
    lang TEXT NOT NULL DEFAULT 'zh'
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_trade_terms ON trade_terms(standard_term, synonym);
"""

TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS paragraphs_ai AFTER INSERT ON paragraphs BEGIN
  INSERT INTO paragraphs_fts(rowid, raw_text, source_file)
  VALUES (new.rowid, new.raw_text, new.source_file);
END;
CREATE TRIGGER IF NOT EXISTS paragraphs_ad AFTER DELETE ON paragraphs BEGIN
  INSERT INTO paragraphs_fts(paragraphs_fts, rowid, raw_text, source_file)
  VALUES('delete', old.rowid, old.raw_text, old.source_file);
END;
CREATE TRIGGER IF NOT EXISTS paragraphs_au AFTER UPDATE ON paragraphs BEGIN
  INSERT INTO paragraphs_fts(paragraphs_fts, rowid, raw_text, source_file)
  VALUES('delete', old.rowid, old.raw_text, old.source_file);
  INSERT INTO paragraphs_fts(rowid, raw_text, source_file)
  VALUES (new.rowid, new.raw_text, new.source_file);
END;
"""


def default_data_root(data_root: Optional[Path] = None) -> Path:
    if data_root is not None:
        return Path(data_root)
    return Path(__file__).resolve().parents[1] / "data" / "contract_rag"


def sqlite_path(data_root: Path) -> Path:
    return Path(data_root) / "contracts.sqlite"


def chroma_path(data_root: Path) -> Path:
    return Path(data_root) / "chroma_persist"


def connect(data_root: Path) -> sqlite3.Connection:
    path = sqlite_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_stores(data_root: Optional[Path] = None) -> Path:
    root = default_data_root(data_root)
    root.mkdir(parents=True, exist_ok=True)
    conn = connect(root)
    conn.executescript(SCHEMA)
    conn.executescript(TRIGGERS)
    conn.executemany(
        "INSERT OR IGNORE INTO trade_terms(standard_term, synonym, lang) VALUES (?,?,?)",
        TRADE_TERMS,
    )
    conn.commit()
    conn.close()
    chroma_path(root).mkdir(parents=True, exist_ok=True)
    return root


def replace_source_paragraphs(conn: sqlite3.Connection, source_file: str) -> None:
    conn.execute("DELETE FROM paragraphs WHERE source_file = ?", (source_file,))


def insert_paragraphs(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    n = 0
    for row in rows:
        conn.execute(
            "INSERT INTO paragraphs(id, seq, source_file, raw_text, markdown) VALUES (?,?,?,?,?)",
            (row["id"], row["seq"], row["source_file"], row["raw_text"], row["markdown"]),
        )
        n += 1
    return n


def fetch_paragraphs(conn: sqlite3.Connection, ids: list[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    by_id: dict[str, dict[str, Any]] = {}
    for para_id in ids:
        row = conn.execute("SELECT * FROM paragraphs WHERE id = ?", (para_id,)).fetchone()
        if row:
            by_id[para_id] = dict(row)
    return [by_id[i] for i in ids if i in by_id]


def fetch_all_paragraphs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, seq, source_file, raw_text FROM paragraphs ORDER BY source_file, seq"
    ).fetchall()
    return [dict(r) for r in rows]


def all_synonyms(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    rows = conn.execute("SELECT standard_term, synonym FROM trade_terms").fetchall()
    return [(r["standard_term"], r["synonym"]) for r in rows]

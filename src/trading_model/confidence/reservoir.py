from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Optional

# Sample-size gate only — the score cutoff itself is never hardcoded.
_MIN_USEFUL = 5
_MIN_NOISE = 5


def pool_path(data_root: Path) -> Path:
    return Path(data_root) / "confidence" / "reservoir.sqlite"


def _connect(data_root: Path) -> sqlite3.Connection:
    path = pool_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS chunk_events ("
        "id INTEGER PRIMARY KEY,"
        "created_at TEXT NOT NULL,"
        "transaction_id TEXT,"
        "chunk_id TEXT NOT NULL,"
        "source_file TEXT,"
        "score REAL NOT NULL,"
        "label TEXT"
        ")"
    )
    conn.commit()
    return conn


def record_event(
    data_root: Path,
    *,
    chunk_id: str,
    score: float,
    source_file: str = "",
    transaction_id: str = "",
    label: Optional[str] = None,
) -> None:
    conn = _connect(data_root)
    conn.execute(
        "INSERT INTO chunk_events(created_at, transaction_id, chunk_id, source_file, score, label)"
        " VALUES (?,?,?,?,?,?)",
        (
            datetime.now(timezone.utc).isoformat(),
            transaction_id,
            chunk_id,
            source_file,
            float(score),
            label,
        ),
    )
    conn.commit()
    conn.close()


def label_event(data_root: Path, *, chunk_id: str, label: str, score: Optional[float] = None) -> None:
    mark = "useful" if label in {"useful", "valid", "keep", "有效"} else "not_useful"
    conn = _connect(data_root)
    conn.execute(
        "INSERT INTO chunk_events(created_at, transaction_id, chunk_id, source_file, score, label)"
        " VALUES (?,?,?,?,?,?)",
        (
            datetime.now(timezone.utc).isoformat(),
            "",
            chunk_id,
            "",
            float(score or 0.0),
            mark,
        ),
    )
    conn.commit()
    conn.close()


def learned_cutoff(data_root: Path) -> Optional[float]:
    conn = _connect(data_root)
    rows = conn.execute(
        "SELECT score, label FROM chunk_events WHERE label IN ('useful','not_useful')"
    ).fetchall()
    conn.close()
    useful = [float(r["score"]) for r in rows if r["label"] == "useful"]
    noise = [float(r["score"]) for r in rows if r["label"] == "not_useful"]
    if len(useful) < _MIN_USEFUL or len(noise) < _MIN_NOISE:
        return None
    return (median(useful) + median(noise)) / 2.0


def filter_chunks(
    chunks: list[dict[str, Any]],
    data_root: Path,
    *,
    score_key: str = "rrf_score",
) -> dict[str, Any]:
    cutoff = learned_cutoff(data_root)
    scored: list[dict[str, Any]] = []
    for chunk in chunks:
        item = dict(chunk)
        raw = item.get(score_key)
        if raw is None:
            raw = item.get("score")
        item["decision_score"] = float(raw or 0.0)
        scored.append(item)
    if cutoff is None:
        return {
            "chunks": scored,
            "cutoff": None,
            "policy": "return_all_until_labels_balance",
            "dropped": 0,
        }
    kept = [c for c in scored if c["decision_score"] >= cutoff]
    return {
        "chunks": kept,
        "cutoff": cutoff,
        "policy": "learned_from_reservoir",
        "dropped": len(scored) - len(kept),
    }

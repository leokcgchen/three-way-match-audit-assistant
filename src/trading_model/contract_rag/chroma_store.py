from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

COLLECTION = "contract_paragraphs"
SPEC_FILE = "embedder_spec.json"


class EmbedDimError(ValueError):
    """Stored vectors and the current embedder do not share a dimension."""


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise EmbedDimError(f"embed dim mismatch: query={len(a)} stored={len(b)}")
    if not a:
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def _read_spec_file(persist_dir: Path) -> dict[str, Any]:
    path = Path(persist_dir) / SPEC_FILE
    if not path.exists():
        return {"name": None, "dim": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"name": None, "dim": None}
    dim_raw = data.get("dim")
    return {
        "name": data.get("name") or data.get("embedder"),
        "dim": int(dim_raw) if dim_raw is not None else None,
    }


def _write_spec_file(persist_dir: Path, name: str, dim: int) -> None:
    persist_dir = Path(persist_dir)
    persist_dir.mkdir(parents=True, exist_ok=True)
    payload = {"name": name, "embedder": name, "dim": int(dim)}
    (persist_dir / SPEC_FILE).write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def _first_embedding_len(embeddings: Any) -> Optional[int]:
    if embeddings is None:
        return None
    shape = getattr(embeddings, "shape", None)
    if shape is not None:
        try:
            if len(shape) >= 2 and int(shape[0]) > 0:
                return int(shape[1])
            return None
        except Exception:
            return None
    try:
        if len(embeddings) == 0:
            return None
        first = embeddings[0]
    except TypeError:
        return None
    if first is None:
        return None
    try:
        return len(first)
    except TypeError:
        return None


def chroma_available() -> bool:
    try:
        import chromadb  # noqa: F401

        return True
    except Exception:
        return False


def _check_spec(
    stored_name: Optional[str],
    stored_dim: Optional[int],
    *,
    embedder_name: Optional[str],
    dim: Optional[int],
) -> None:
    if stored_dim is not None and dim is not None and int(stored_dim) != int(dim):
        raise EmbedDimError(
            f"embed dim mismatch: collection={stored_dim} ({stored_name}) query={dim} ({embedder_name})"
        )
    if stored_name and embedder_name and stored_name != embedder_name:
        raise EmbedDimError(
            f"embedder mismatch: collection={stored_name} query={embedder_name}"
        )


class _SqliteCollection:
    def __init__(self, path: Path, name: str) -> None:
        self.path = path
        self.name = name
        path.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path / "chroma_local.sqlite3"))
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS embeddings ("
            "id TEXT PRIMARY KEY, document TEXT, metadata TEXT, vector TEXT)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS collection_meta (k TEXT PRIMARY KEY, v TEXT)"
        )
        self.db.commit()

    def embedder_spec(self) -> dict[str, Any]:
        file_spec = _read_spec_file(self.path)
        if file_spec["dim"] is not None:
            return file_spec
        rows = dict(self.db.execute("SELECT k, v FROM collection_meta"))
        dim_raw = rows.get("dim")
        return {
            "name": rows.get("embedder"),
            "dim": int(dim_raw) if dim_raw is not None else None,
        }

    def _set_spec(self, name: str, dim: int) -> None:
        spec = self.embedder_spec()
        _check_spec(spec["name"], spec["dim"], embedder_name=name, dim=dim)
        self.db.execute("INSERT OR REPLACE INTO collection_meta(k, v) VALUES (?, ?)", ("embedder", name))
        self.db.execute("INSERT OR REPLACE INTO collection_meta(k, v) VALUES (?, ?)", ("dim", str(dim)))
        self.db.commit()
        _write_spec_file(self.path, name, dim)

    def upsert(
        self,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
        embedder_name: Optional[str] = None,
        dim: Optional[int] = None,
    ) -> None:
        if embeddings:
            vec_dim = len(embeddings[0])
            for vec in embeddings:
                if len(vec) != vec_dim:
                    raise EmbedDimError("embedding batch contains mixed dimensions")
            if dim is not None and dim != vec_dim:
                raise EmbedDimError(f"embed dim mismatch: declared={dim} vector={vec_dim}")
            self._set_spec(embedder_name or "unknown", vec_dim)
        for i, doc, meta, vec in zip(ids, documents, metadatas, embeddings):
            self.db.execute(
                "INSERT OR REPLACE INTO embeddings(id, document, metadata, vector) VALUES (?,?,?,?)",
                (i, doc, json.dumps(meta, ensure_ascii=False), json.dumps(vec)),
            )
        self.db.commit()

    def query(
        self,
        *,
        query_embeddings: list[list[float]],
        n_results: int,
        embedder_name: Optional[str] = None,
        dim: Optional[int] = None,
    ) -> dict[str, Any]:
        qv = query_embeddings[0]
        spec = self.embedder_spec()
        _check_spec(spec["name"], spec["dim"], embedder_name=embedder_name, dim=dim or len(qv))
        if spec["dim"] is not None:
            _check_spec(spec["name"], spec["dim"], embedder_name=embedder_name, dim=len(qv))
        rows = self.db.execute("SELECT id, document, metadata, vector FROM embeddings").fetchall()
        scored = []
        for pid, document, metadata, vector_json in rows:
            vec = json.loads(vector_json)
            scored.append((cosine(qv, vec), pid, document, json.loads(metadata)))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:n_results]
        return {
            "ids": [[r[1] for r in top]],
            "documents": [[r[2] for r in top]],
            "metadatas": [[r[3] for r in top]],
            "distances": [[1.0 - r[0] for r in top]],
        }

    def delete(self, ids: list[str]) -> None:
        self.db.executemany("DELETE FROM embeddings WHERE id = ?", [(i,) for i in ids])
        self.db.commit()


class _ChromaCollection:
    def __init__(self, inner: Any, persist_dir: Path) -> None:
        self.inner = inner
        self.persist_dir = Path(persist_dir)

    def embedder_spec(self) -> dict[str, Any]:
        file_spec = _read_spec_file(self.persist_dir)
        if file_spec["dim"] is not None:
            return file_spec
        meta = dict(getattr(self.inner, "metadata", None) or {})
        dim_raw = meta.get("embed_dim")
        return {
            "name": meta.get("embedder"),
            "dim": int(dim_raw) if dim_raw is not None else None,
        }

    def _peek_stored_dim(self) -> Optional[int]:
        try:
            peek = self.inner.get(limit=1, include=["embeddings"])
            embs = peek.get("embeddings") if isinstance(peek, dict) else None
            return _first_embedding_len(embs)
        except Exception:
            return None

    def _ensure_spec(self, name: str, dim: int) -> None:
        spec = self.embedder_spec()
        if spec["dim"] is None:
            peeked = self._peek_stored_dim()
            if peeked is not None:
                spec = {"name": spec["name"], "dim": peeked}
        _check_spec(spec["name"], spec["dim"], embedder_name=name, dim=dim)
        _write_spec_file(self.persist_dir, name, dim)
        meta = dict(getattr(self.inner, "metadata", None) or {})
        meta["embedder"] = name
        meta["embed_dim"] = dim
        meta.setdefault("hnsw:space", "cosine")
        try:
            self.inner.modify(metadata=meta)
        except Exception:
            pass

    def upsert(
        self,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
        embedder_name: Optional[str] = None,
        dim: Optional[int] = None,
    ) -> None:
        if embeddings:
            vec_dim = len(embeddings[0])
            for vec in embeddings:
                if len(vec) != vec_dim:
                    raise EmbedDimError("embedding batch contains mixed dimensions")
            if dim is not None and dim != vec_dim:
                raise EmbedDimError(f"embed dim mismatch: declared={dim} vector={vec_dim}")
            self._ensure_spec(embedder_name or "unknown", vec_dim)
        self.inner.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)

    def query(
        self,
        *,
        query_embeddings: list[list[float]],
        n_results: int,
        embedder_name: Optional[str] = None,
        dim: Optional[int] = None,
    ) -> dict[str, Any]:
        qv = query_embeddings[0]
        spec = self.embedder_spec()
        if spec["dim"] is None:
            peeked = self._peek_stored_dim()
            if peeked is not None:
                spec = {"name": spec["name"], "dim": peeked}
        _check_spec(spec["name"], spec["dim"], embedder_name=embedder_name, dim=dim or len(qv))
        if spec["dim"] is not None:
            _check_spec(spec["name"], spec["dim"], embedder_name=embedder_name, dim=len(qv))
        try:
            return self.inner.query(query_embeddings=query_embeddings, n_results=n_results)
        except EmbedDimError:
            raise
        except Exception as exc:
            msg = str(exc).lower()
            if "dimension" in msg or "expecting embedding" in msg:
                raise EmbedDimError(
                    f"embed dim mismatch: collection={spec['dim']} ({spec['name']}) query={len(qv)} ({embedder_name})"
                ) from exc
            raise

    def delete(self, ids: list[str]) -> None:
        try:
            self.inner.delete(ids=ids)
        except Exception:
            pass


def get_collection(persist_dir: Path):
    persist_dir = Path(persist_dir)
    persist_dir.mkdir(parents=True, exist_ok=True)
    if chroma_available():
        import chromadb

        client = chromadb.PersistentClient(path=str(persist_dir / "chromadb"))
        inner = client.get_or_create_collection(
            name=COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
        return _ChromaCollection(inner, persist_dir)
    return _SqliteCollection(persist_dir, COLLECTION)

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence, Union

FileArg = Union[str, Path, tuple[str, bytes]]


def _file_bytes(item: FileArg) -> bytes:
    if isinstance(item, tuple):
        return item[1]
    path = Path(item)
    return path.read_bytes()


def file_content_hashes(files: Optional[Sequence[FileArg]]) -> list[str]:
    if not files:
        return []
    return sorted(hashlib.sha256(_file_bytes(f)).hexdigest() for f in files)


def classified_fingerprint(classified: Optional[Iterable[dict[str, Any]]]) -> str:
    rows = []
    for item in classified or []:
        rows.append(
            {
                "document_id": item.get("document_id") or item.get("file_name") or "",
                "raw_text": item.get("raw_text") or "",
                "fields": item.get("fields") or {},
            }
        )
    rows.sort(key=lambda r: str(r["document_id"]))
    blob = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def compute_input_fingerprint(
    classified: Optional[Iterable[dict[str, Any]]] = None,
    files: Optional[Sequence[FileArg]] = None,
) -> str:
    has_c = bool(classified)
    has_f = bool(files)
    if not has_c and not has_f:
        return "empty"
    c_fp = classified_fingerprint(classified) if has_c else ""
    f_hashes = file_content_hashes(files) if has_f else []
    f_fp = hashlib.sha256("".join(f_hashes).encode("ascii")).hexdigest() if f_hashes else ""
    if has_c and has_f:
        return hashlib.sha256(f"{c_fp}\n{f_fp}".encode("ascii")).hexdigest()
    return c_fp or f_fp


class ArtifactStore:
    def __init__(self, data_root: Union[str, Path]) -> None:
        self.root = Path(data_root)
        self.runs = self.root / "runs"
        self.by_fp = self.runs / "index" / "by_fingerprint"
        self.by_file = self.runs / "index" / "by_file_hash"

    def artifact_path(self, transaction_id: str) -> Path:
        return self.runs / transaction_id / "artifact.v1.json"

    def lookup(
        self,
        fingerprint: str,
        file_hashes: Sequence[str] = (),
    ) -> Optional[dict[str, Any]]:
        hit = self.by_fp / fingerprint
        if fingerprint != "empty" and hit.exists():
            tx = hit.read_text(encoding="utf-8").strip()
            path = self.artifact_path(tx)
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        for h in file_hashes:
            idx = self.by_file / h
            if idx.exists():
                tx = idx.read_text(encoding="utf-8").strip()
                path = self.artifact_path(tx)
                if path.exists():
                    return json.loads(path.read_text(encoding="utf-8"))
        return None

    def save(self, transaction_id: str, artifact: dict[str, Any]) -> None:
        path = self.artifact_path(transaction_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
        fp = artifact.get("run_meta", {}).get("input_fingerprint") or ""
        self.by_fp.mkdir(parents=True, exist_ok=True)
        if fp and fp != "empty":
            (self.by_fp / fp).write_text(transaction_id, encoding="utf-8")
        for h in artifact.get("run_meta", {}).get("source_file_hashes") or []:
            self.by_file.mkdir(parents=True, exist_ok=True)
            (self.by_file / h).write_text(transaction_id, encoding="utf-8")

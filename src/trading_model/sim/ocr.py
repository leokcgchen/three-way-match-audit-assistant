from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from src.trading_model.sim.paragraphs import split_paragraphs

_SCENE_DIR = Path(__file__).resolve().parent / "scenes"
_CATALOG = json.loads((_SCENE_DIR / "catalog.json").read_text(encoding="utf-8"))
_BY_ID = {row["id"]: row for row in _CATALOG["scenes"]}


def list_scenes() -> list[dict[str, str]]:
    return [
        {"id": row["id"], "title": row["title"], "summary": row["summary"]}
        for row in _CATALOG["scenes"]
    ]


def _pack(
    *,
    document_id: str,
    doc_type: str,
    file_name: str,
    parts: list[str],
    pages: list[int],
) -> dict[str, Any]:
    raw_text = "\n\n".join(parts)
    blocks = []
    for i, part in enumerate(parts):
        page = pages[i] if i < len(pages) else pages[-1] if pages else 1
        blocks.append(
            {
                "page": page,
                "text": part,
                "bbox": [0, 0, 1, 1],
                "confidence": 0.99,
            }
        )
    return {
        "document_id": document_id,
        "doc_type": doc_type,
        "file_name": file_name,
        "raw_text": raw_text,
        "text_blocks": blocks,
        "fields": {},
        "confidence": 0.99,
    }


def _pack_markdown(spec: dict[str, Any]) -> dict[str, Any]:
    markdown = (_SCENE_DIR / spec["file"]).read_text(encoding="utf-8")
    parts = split_paragraphs(markdown)
    if not parts:
        parts = [markdown.strip()]
    pages = list(spec.get("pages") or [1])
    return _pack(
        document_id=str(spec["document_id"]),
        doc_type=str(spec["doc_type"]),
        file_name=str(spec["file"]),
        parts=parts,
        pages=pages,
    )


def mock_ocr(scene_id: str, raw_text: Optional[str] = None) -> list[dict[str, Any]]:
    if scene_id == "custom":
        text = (raw_text or "").strip()
        if not text:
            raise ValueError("custom scene requires raw_text")
        parts = split_paragraphs(text) or [text]
        return [
            _pack(
                document_id="CUSTOM-1",
                doc_type="sales_contract",
                file_name="custom.md",
                parts=parts,
                pages=[1] * len(parts),
            )
        ]
    meta = _BY_ID.get(scene_id)
    if meta is None:
        raise KeyError(f"unknown scene: {scene_id}")
    specs = list(meta.get("documents") or [])
    if not specs:
        specs = [
            {
                "document_id": meta["document_id"],
                "doc_type": meta["doc_type"],
                "file": meta["file"],
                "pages": meta.get("pages") or [1],
            }
        ]
    return [_pack_markdown(spec) for spec in specs]

from __future__ import annotations

import re
from typing import Any

_HEADING = re.compile(r"^#+\s*")


def split_paragraphs(markdown: str) -> list[str]:
    chunks: list[str] = []
    buf: list[str] = []
    for line in markdown.replace("\r\n", "\n").split("\n"):
        if not line.strip():
            if buf:
                chunks.append("\n".join(buf).strip())
                buf = []
            continue
        if line.startswith("#") and buf:
            chunks.append("\n".join(buf).strip())
            buf = [line]
        else:
            buf.append(line)
    body = "\n".join(buf).strip()
    if body:
        chunks.append(body)
    return [c for c in chunks if len(_HEADING.sub("", c).strip()) >= 8]


def paragraphs_from_classified(classified: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seq = 0
    for doc in classified:
        doc_id = str(doc.get("document_id") or "DOC")
        source = str(doc.get("file_name") or f"{doc_id}.txt")
        blocks = list(doc.get("text_blocks") or [])
        raw = str(doc.get("raw_text") or "")
        parts = split_paragraphs(raw)
        if not parts and raw.strip():
            parts = [raw.strip()]
        for part in parts:
            seq += 1
            page = 1
            for block in blocks:
                blob = str(block.get("text") or "")
                if part in blob or blob in part or part[:12] in blob:
                    page = int(block.get("page") or 1)
                    break
            out.append(
                {
                    "id": f"{doc_id}-p{seq}",
                    "document_id": doc_id,
                    "source_file": source,
                    "seq": seq,
                    "page": page,
                    "raw_text": part,
                }
            )
    return out

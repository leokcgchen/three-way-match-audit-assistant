from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Callable, Optional

from .chroma_store import get_collection
from .embedder import Embedder, get_embedder
from .sqlite_store import (
    chroma_path,
    connect,
    default_data_root,
    init_stores,
    insert_paragraphs,
    replace_source_paragraphs,
)


def to_markdown(plain: str, *, source_name: str) -> str:
    body = (plain or "").replace("\r\n", "\n").strip()
    return f"# {source_name}\n\n{body}\n"


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
    if buf:
        chunks.append("\n".join(buf).strip())
    return [c for c in chunks if len(re.sub(r"^#+\s*", "", c).strip()) >= 8]


def _source_name(source: Any) -> str:
    if isinstance(source, (str, Path)):
        return Path(source).name
    return str(getattr(source, "name", "contract"))


def _read_pdf_native_text(path: Path) -> str | None:
    try:
        import pypdfium2 as pdfium
    except Exception:
        return None
    pdf = pdfium.PdfDocument(str(path))
    pages = []
    for page in pdf:
        textpage = page.get_textpage()
        pages.append(textpage.get_text_bounded() or "")
    blob = "\n\n".join(pages).strip()
    return blob if len(blob) >= 80 else None


def _ocr_pdf_with_paddle(path: Path) -> str:
    import tempfile

    import pypdfium2 as pdfium
    from paddleocr import PaddleOCR

    ocr = PaddleOCR(
        lang="ch",
        ocr_version="PP-OCRv5",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        device="cpu",
    )
    pdf = pdfium.PdfDocument(str(path))
    pages: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for i, page in enumerate(pdf):
            image = page.render(scale=2).to_pil()
            img_path = tmp_dir / f"page-{i+1}.png"
            image.save(img_path)
            raw_results = list(ocr.predict(str(img_path)))
            texts: list[str] = []
            for result in raw_results:
                payload = result.json() if callable(getattr(result, "json", None)) else result
                if isinstance(payload, str):
                    import json

                    payload = json.loads(payload)
                body = payload.get("res") if isinstance(payload, dict) else None
                rec = (body or {}).get("rec_texts") if isinstance(body, dict) else None
                if rec:
                    texts.extend(str(t) for t in rec)
            pages.append("\n".join(texts))
    return "\n\n".join(pages)


def _ocr_image_with_paddle(path: Path) -> str:
    from paddleocr import PaddleOCR

    ocr = PaddleOCR(
        lang="ch",
        ocr_version="PP-OCRv5",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        device="cpu",
    )
    raw_results = list(ocr.predict(str(path)))
    texts: list[str] = []
    for result in raw_results:
        payload = result.json() if callable(getattr(result, "json", None)) else result
        if isinstance(payload, str):
            import json

            payload = json.loads(payload)
        body = payload.get("res") if isinstance(payload, dict) else None
        rec = (body or {}).get("rec_texts") if isinstance(body, dict) else None
        if rec:
            texts.extend(str(t) for t in rec)
    return "\n".join(texts)


def extract_document(source: Any, *, ocr_fn: Optional[Callable[[Any], str]] = None) -> tuple[str, str, str]:
    name = _source_name(source)
    if not isinstance(source, (str, Path)):
        return name, to_markdown(str(source), source_name=name), "text"
    path = Path(source)
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt"}:
        return name, path.read_text(encoding="utf-8"), "text"
    if suffix == ".pdf":
        native = _read_pdf_native_text(path)
        if native:
            return name, to_markdown(native, source_name=name), "native"
        if ocr_fn is not None:
            return name, to_markdown(ocr_fn(path), source_name=name), "ocr"
        return name, to_markdown(_ocr_pdf_with_paddle(path), source_name=name), "ocr"
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"}:
        if ocr_fn is not None:
            return name, to_markdown(ocr_fn(path), source_name=name), "ocr"
        return name, to_markdown(_ocr_image_with_paddle(path), source_name=name), "ocr"
    if ocr_fn is not None:
        return name, to_markdown(ocr_fn(path), source_name=name), "ocr"
    return name, to_markdown(path.read_text(encoding="utf-8", errors="ignore"), source_name=name), "text"


def source_to_markdown(source: Any, *, ocr_fn: Optional[Callable[[Any], str]] = None) -> tuple[str, str]:
    name, markdown, _method = extract_document(source, ocr_fn=ocr_fn)
    return name, markdown


def ingest_source(
    source: Any,
    *,
    data_root: Optional[Path] = None,
    ocr_fn: Optional[Callable[[Any], str]] = None,
    embedder: Optional[Embedder] = None,
    replace_source: bool = True,
) -> list[dict[str, Any]]:
    root = default_data_root(data_root)
    init_stores(root)
    name, markdown = source_to_markdown(source, ocr_fn=ocr_fn)
    parts = split_paragraphs(markdown)
    rows = []
    for seq, text in enumerate(parts, start=1):
        para_id = hashlib.sha1(f"{name}:{seq}:{text}".encode("utf-8")).hexdigest()[:16]
        rows.append(
            {
                "id": para_id,
                "seq": seq,
                "source_file": name,
                "raw_text": text,
                "markdown": text,
            }
        )
    conn = connect(root)
    if replace_source:
        existing = conn.execute("SELECT id FROM paragraphs WHERE source_file = ?", (name,)).fetchall()
        old_ids = [r["id"] for r in existing]
        if old_ids:
            get_collection(chroma_path(root)).delete(old_ids)
        replace_source_paragraphs(conn, name)
    insert_paragraphs(conn, rows)
    conn.commit()
    conn.close()

    encoder = embedder or get_embedder()
    if rows:
        vectors = encoder.encode([r["raw_text"] for r in rows])
        get_collection(chroma_path(root)).upsert(
            ids=[r["id"] for r in rows],
            embeddings=vectors,
            documents=[r["raw_text"] for r in rows],
            metadatas=[{"paragraph_id": r["id"], "seq": r["seq"], "source_file": r["source_file"]} for r in rows],
            embedder_name=encoder.name,
            dim=getattr(encoder, "dim", len(vectors[0])),
        )
    return rows


def ingest_text(
    markdown: str,
    *,
    source_name: str = "inline.md",
    data_root: Optional[Path] = None,
    embedder: Optional[Embedder] = None,
    replace_source: bool = True,
) -> list[dict[str, Any]]:
    text = markdown if markdown.lstrip().startswith("#") else to_markdown(markdown, source_name=source_name)
    tmp = default_data_root(data_root) / "_inbox" / source_name
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(text, encoding="utf-8")
    return ingest_source(tmp, data_root=data_root, embedder=embedder, replace_source=replace_source)

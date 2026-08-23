"""预览取证：渲染页图 + OCR 块点选 / 拖框取字。"""

from __future__ import annotations

import io
import threading
from pathlib import Path
from typing import Any, Optional

from PIL import Image

# Windows 上 pypdfium2 不能并发打开同一 PDF：会渲出空白图或拖垮进程。
_RENDER_LOCK = threading.Lock()
_MIN_PDF_PNG_BYTES = 20_000


def _close_pdfium(obj: Any) -> None:
    closer = getattr(obj, "close", None)
    if callable(closer):
        closer()


def _pdf_page_size(path: Path, page_index: int) -> tuple[float, float, int]:
    import pdfplumber

    with pdfplumber.open(str(path)) as pdf:
        n = len(pdf.pages)
        if n <= 0:
            raise ValueError("PDF 无页面")
        if page_index < 0 or page_index >= n:
            page_index = 0
        page = pdf.pages[page_index]
        return float(page.width), float(page.height), n


def _render_pdfium_page(doc: Any, page_index: int, scale: float) -> tuple[bytes, int, int]:
    page = doc[page_index]
    try:
        bitmap = page.render(scale=scale)
        pil = bitmap.to_pil().convert("RGB")
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        return buf.getvalue(), pil.width, pil.height
    finally:
        _close_pdfium(page)


def _render_pdf_page_unlocked(
    path: Path,
    *,
    page_index: int,
    scale: float,
) -> tuple[bytes, dict[str, Any]]:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        page_count = len(doc)
        if page_count <= 0:
            raise ValueError("PDF 无页面")
        if page_index < 0 or page_index >= page_count:
            page_index = 0
        png, iw, ih = _render_pdfium_page(doc, page_index, scale)
        if len(png) < _MIN_PDF_PNG_BYTES:
            png, iw, ih = _render_pdfium_page(doc, page_index, scale)
        scale_f = float(scale) or 1.0
        meta = {
            "page_index": page_index,
            "page_count": page_count,
            "pdf_width": iw / scale_f,
            "pdf_height": ih / scale_f,
            "image_width": iw,
            "image_height": ih,
            "scale": scale,
            "kind": "pdf",
        }
        return png, meta
    finally:
        _close_pdfium(doc)


def render_preview_page(
    path: Path,
    *,
    page_index: int = 0,
    scale: float = 2.0,
) -> tuple[bytes, dict[str, Any]]:
    """渲染单页预览 PNG；返回 (png_bytes, meta)。"""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        with _RENDER_LOCK:
            return _render_pdf_page_unlocked(path, page_index=page_index, scale=scale)

    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
        pil = Image.open(path).convert("RGB")
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        meta = {
            "page_index": 0,
            "page_count": 1,
            "pdf_width": float(pil.width),
            "pdf_height": float(pil.height),
            "image_width": pil.width,
            "image_height": pil.height,
            "scale": 1.0,
            "kind": "image",
        }
        return buf.getvalue(), meta

    raise ValueError(f"不支持预览取证的格式: {suffix}")


def _norm_bbox(bbox: list[float], page_w: float, page_h: float) -> list[float]:
    x0, y0, x1, y1 = [float(x) for x in bbox[:4]]
    # 已是 0~1.5 归一化
    if 0 <= x0 <= 1.5 and 0 <= x1 <= 1.5 and 0 <= y0 <= 1.5 and 0 <= y1 <= 1.5:
        return [
            max(0.0, min(1.0, x0)),
            max(0.0, min(1.0, y0)),
            max(0.0, min(1.0, x1)),
            max(0.0, min(1.0, y1)),
        ]
    if page_w <= 0 or page_h <= 0:
        return [x0, y0, x1, y1]
    return [
        max(0.0, min(1.0, x0 / page_w)),
        max(0.0, min(1.0, y0 / page_h)),
        max(0.0, min(1.0, x1 / page_w)),
        max(0.0, min(1.0, y1 / page_h)),
    ]


def list_page_text_blocks(
    path: Path,
    *,
    page_index: int = 0,
    text_blocks: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """返回当前页可选文本块（归一化 bbox 0~1，原点左上）。"""
    suffix = path.suffix.lower()
    blocks_out: list[dict[str, Any]] = []

    if suffix == ".pdf":
        pw, ph, page_count = _pdf_page_size(path, page_index)
        if page_index < 0 or page_index >= page_count:
            page_index = 0
        # 1) OCR text_blocks（若有）
        for i, b in enumerate(text_blocks or []):
            try:
                pg = int(b.get("page") if b.get("page") is not None else 0)
            except (TypeError, ValueError):
                pg = 0
            if pg != page_index:
                continue
            text = str(b.get("text") or "").strip()
            bb = b.get("bbox") or []
            if not text or len(bb) < 4:
                continue
            blocks_out.append(
                {
                    "id": f"ocr-{i}",
                    "text": text,
                    "bbox": _norm_bbox(list(bb), pw, ph),
                    "source": "ocr",
                }
            )
        # 2) PDF 字词（补充可点选；OCR 很少时更有用）
        try:
            import pdfplumber

            with pdfplumber.open(str(path)) as pdf:
                page = pdf.pages[page_index]
                words = page.extract_words() or []
                for i, w in enumerate(words):
                    text = str(w.get("text") or "").strip()
                    if not text:
                        continue
                    bb = [
                        float(w["x0"]),
                        float(w["top"]),
                        float(w["x1"]),
                        float(w["bottom"]),
                    ]
                    blocks_out.append(
                        {
                            "id": f"pdfw-{i}",
                            "text": text,
                            "bbox": _norm_bbox(bb, pw, ph),
                            "source": "pdf_word",
                        }
                    )
        except Exception:
            pass
        return {
            "page_index": page_index,
            "page_count": page_count,
            "blocks": blocks_out,
            "kind": "pdf",
        }

    # 图片：仅 OCR blocks
    pil = Image.open(path)
    iw, ih = pil.size
    for i, b in enumerate(text_blocks or []):
        text = str(b.get("text") or "").strip()
        bb = b.get("bbox") or []
        if not text or len(bb) < 4:
            continue
        blocks_out.append(
            {
                "id": f"ocr-{i}",
                "text": text,
                "bbox": _norm_bbox(list(bb), float(iw), float(ih)),
                "source": "ocr",
            }
        )
    return {
        "page_index": 0,
        "page_count": 1,
        "blocks": blocks_out,
        "kind": "image",
    }


def _rects_overlap(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
    *,
    min_iou: float = 0.02,
) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return False
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_b = max((bx1 - bx0) * (by1 - by0), 1e-9)
    # 词大部分落在框内也算
    return inter / area_b >= min_iou or inter / max((ax1 - ax0) * (ay1 - ay0), 1e-9) >= 0.05


def capture_text_in_rect(
    path: Path,
    *,
    page_index: int = 0,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    text_blocks: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """按归一化矩形取字（0~1，左上原点）。优先 PDF 词/OCR 块，再 crop 文本。"""
    nx0, nx1 = sorted([float(x0), float(x1)])
    ny0, ny1 = sorted([float(y0), float(y1)])
    # 过小的框忽略
    if (nx1 - nx0) < 0.002 or (ny1 - ny0) < 0.002:
        return {"text": "", "source": "empty", "parts": [], "message": "选区过小"}

    page_data = list_page_text_blocks(path, page_index=page_index, text_blocks=text_blocks)
    sel = (nx0, ny0, nx1, ny1)
    parts: list[str] = []
    hit_ids: list[str] = []
    for b in page_data.get("blocks") or []:
        bb = b.get("bbox") or []
        if len(bb) < 4:
            continue
        box = (float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))
        if _rects_overlap(sel, box):
            t = str(b.get("text") or "").strip()
            if t:
                parts.append(t)
                hit_ids.append(str(b.get("id") or ""))

    if parts:
        # 去重保序
        seen: set[str] = set()
        uniq: list[str] = []
        for p in parts:
            if p in seen:
                continue
            seen.add(p)
            uniq.append(p)
        text = "".join(uniq) if all(len(p) <= 2 for p in uniq) else " ".join(uniq)
        # 金额/编号类：块很碎时用无空格拼接更合适
        if any(ch.isdigit() for ch in text) and len(uniq) <= 8:
            joined = "".join(uniq)
            if any(c in joined for c in ".,"):
                text = joined
        return {
            "text": text.strip(),
            "source": "blocks",
            "parts": uniq,
            "hit_ids": hit_ids,
            "page_index": page_data.get("page_index", page_index),
            "message": f"命中 {len(uniq)} 个文本块",
        }

    # 兜底：pdfplumber crop
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            import pdfplumber

            pw, ph, _ = _pdf_page_size(path, page_index)
            with pdfplumber.open(str(path)) as pdf:
                page = pdf.pages[page_index]
                crop = page.crop((nx0 * pw, ny0 * ph, nx1 * pw, ny1 * ph))
                raw = (crop.extract_text() or "").strip()
                raw = " ".join(raw.split())
                if raw:
                    return {
                        "text": raw,
                        "source": "pdf_crop",
                        "parts": [raw],
                        "hit_ids": [],
                        "page_index": page_index,
                        "message": "由 PDF 文本层裁剪得到",
                    }
        except Exception as exc:
            return {
                "text": "",
                "source": "failed",
                "parts": [],
                "message": f"取字失败：{exc}",
            }

    return {
        "text": "",
        "source": "empty",
        "parts": [],
        "hit_ids": [],
        "page_index": page_index,
        "message": "选区内无可用文字（扫描件需 OCR 坐标；可重跑识别后再试）",
    }

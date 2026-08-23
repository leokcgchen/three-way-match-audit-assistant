"""预览/高亮与 OCR 共用同一路径，避免 text_blocks 坐标错位。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def resolve_document_image_path(doc: dict[str, Any] | None) -> Path:
    """优先使用 OCR 实际读图路径（预处理 JPEG），否则回退原件 path。"""
    if not isinstance(doc, dict):
        return Path()
    ocr_path = str(doc.get("ocr_image_path") or "").strip()
    if ocr_path:
        p = Path(ocr_path)
        if p.is_file():
            return p
    return Path(str(doc.get("path") or ""))

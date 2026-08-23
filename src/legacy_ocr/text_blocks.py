"""从千帆 PaddleOCR 返回中抽取文本块与 bbox。"""

from __future__ import annotations

from typing import Any, Dict, List


def extract_text_blocks_from_paddle(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """尽量抽取 text/bbox/page（结构因 OCR 版本而异）。

    优先保留行级 OCR；layout 表块常覆盖整张明细，仅作补充且标注 source。
    """
    line_blocks: List[Dict[str, Any]] = []
    layout_blocks: List[Dict[str, Any]] = []

    def _add(bucket: List[Dict[str, Any]], text: Any, bbox: Any, page: int = 0, *, source: str) -> None:
        t = str(text or "").strip()
        if not t:
            return
        box = None
        if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
            if isinstance(bbox[0], (list, tuple)):
                xs = [float(p[0]) for p in bbox]
                ys = [float(p[1]) for p in bbox]
                box = [min(xs), min(ys), max(xs), max(ys)]
            else:
                box = [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])]
        bucket.append({"text": t, "bbox": box, "page": page, "source": source})

    result = payload.get("result") or {}
    layout = result.get("layoutParsingResults") or result.get("layout_parsing_results") or []
    for pi, page in enumerate(layout):
        pruned = page.get("prunedResult") or page.get("pruned_result") or {}
        for block in pruned.get("parsing_res_list") or []:
            _add(
                layout_blocks,
                block.get("block_content") or block.get("content"),
                block.get("block_bbox") or block.get("bbox") or block.get("block_box"),
                pi,
                source="layout",
            )
        for line in pruned.get("ocr_res") or pruned.get("overall_ocr_res") or []:
            if isinstance(line, dict):
                _add(
                    line_blocks,
                    line.get("text") or line.get("rec_text"),
                    line.get("poly") or line.get("bbox"),
                    pi,
                    source="ocr_line",
                )

    for item in result.get("ocrResults") or []:
        for line in item.get("words_result") or item.get("wordsResult") or []:
            loc = line.get("location") or line.get("chars") or line.get("boundingBox")
            _add(
                line_blocks,
                line.get("words") or line.get("text"),
                loc,
                0,
                source="ocr_line",
            )

    # 行级在前；layout 大块在后（高亮匹配会优先短块并拒过大框）
    merged = [*line_blocks, *layout_blocks]
    seen = set()
    uniq: List[Dict[str, Any]] = []
    for b in merged:
        key = (b.get("text"), tuple(b.get("bbox") or []), b.get("page"), b.get("source"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(b)
    return uniq

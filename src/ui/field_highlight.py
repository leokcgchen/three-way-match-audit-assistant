"""字段确认预览：叠层标签 + 原文/坐标高亮定位。

阶段1：字段芯片点选高亮；PDF 用 pdfplumber 搜字定位并画框。
阶段2：若 OCR 带回 text_blocks 坐标，图片预览按框高亮。
"""

from __future__ import annotations

import html
import io
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw

# 高对比色循环（描边）
_PALETTE = [
    (230, 57, 70),
    (29, 53, 87),
    (69, 123, 157),
    (42, 157, 143),
    (233, 196, 106),
    (244, 162, 97),
    (231, 111, 81),
    (87, 117, 144),
]


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "")).lower()


def _value_search_variants(value: str) -> List[str]:
    """生成 PDF/OCR 搜索候选：原值、去空白、金额/日期变体、长句截断。

    长条款优先保留「纯中文」短前缀：pdfplumber page.search 对「中文紧挨阿拉伯数字」
    常失败，字符级定位也更吃短而稳的针。
    """
    v = str(value or "").strip()
    if not v:
        return []
    out: List[str] = [v]
    compact = re.sub(r"\s+", "", v)
    if compact and compact not in out:
        out.append(compact)

    def _add(s: str) -> None:
        s = str(s or "").strip()
        if s and s not in out:
            out.append(s)

    # 金额：仅对「像金额」的短数字做千分位/补小数；长票号勿当金额；0.xx 留给税率
    num = re.sub(r"[,\s￥¥$]", "", v)
    int_part = num.split(".", 1)[0]
    is_rate = bool(re.fullmatch(r"0\.\d{1,4}", num))
    is_amountish = (
        not is_rate
        and re.fullmatch(r"\d+(\.\d+)?", num)
        and 1 <= len(int_part) <= 12
    )
    if is_amountish:
        _add(num)
        if "." in num:
            head, frac = num.split(".", 1)
            parts: List[str] = []
            while head:
                parts.append(head[-3:])
                head = head[:-3]
            grouped = ",".join(reversed(parts)) + "." + frac
            _add(grouped)
            if len(frac) == 1:
                _add(f"{num}0")
                parts2: List[str] = []
                h2 = num.split(".", 1)[0]
                while h2:
                    parts2.append(h2[-3:])
                    h2 = h2[:-3]
                _add(",".join(reversed(parts2)) + f".{frac}0")
            elif frac.endswith("0") and len(frac) == 2:
                _add(f"{num.split('.', 1)[0]}.{frac[0]}")
            for prefix in ("¥", "￥", "￥ ", "¥ "):
                _add(prefix + num)
                _add(prefix + grouped)
        else:
            _add(f"{num}.00")
            head = num
            parts = []
            while head:
                parts.append(head[-3:])
                head = head[:-3]
            _add(",".join(reversed(parts)))
    elif re.fullmatch(r"\d{13,}", num):
        _add(num)

    # 小数税率/折扣率 → PDF 常见「13%」「1%」「折扣1%」
    if is_rate:
        pct = round(float(num) * 100)
        for s in (f"{pct}%", f"{pct}％", f"折扣{pct}%", f"税率{pct}%", f"税率{pct}％"):
            _add(s)
        if pct == 1:
            for s in ("1%", "折扣1%", "折扣 1%"):
                _add(s)

    # 日期：2025-12-05 → 2025年12月5日 / 2025年12月05日
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", compact)
    if not m:
        m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", v)
    if m:
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        for s in (
            f"{y}年{mo}月{d}日",
            f"{y}年{mo:02d}月{d:02d}日",
            f"{y} 年 {mo} 月 {d} 日",
            f"{y}/{mo}/{d}",
            f"{y}/{mo:02d}/{d:02d}",
            f"{y}.{mo}.{d}",
            f"{y}.{mo:02d}.{d:02d}",
        ):
            _add(s)

    # 长条款：纯中文短针优先，再按标点拆段；避免一上来用「中文+数字」长针
    if len(compact) >= 8:
        pure = re.sub(r"[\d.,￥¥$%％]", "", compact)
        for size in (16, 12, 10, 8):
            if len(pure) >= size:
                _add(pure[:size])
        for part in re.split(r"[，,。；;：:\n]", v):
            seg = re.sub(r"\s+", "", part.strip())
            if 6 <= len(seg) <= 24:
                _add(seg)
        for size in (20, 16, 12, 8):
            if len(compact) >= size:
                _add(compact[:size])

    # 金额优先用「带格式」的针（千分位/货币符），再回落裸数字
    if is_amountish:
        preferred = [x for x in out if x not in {v, compact} and re.search(r"[,￥¥]", x)]
        rest = [x for x in out if x not in preferred and x not in {v, compact}]
        rebuilt: List[str] = []
        for s in [*preferred, v, compact, *rest]:
            if s and s not in rebuilt:
                rebuilt.append(s)
        return rebuilt

    return out


def field_value_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        # 明细行：优先用商品名/物料编码等高亮，勿整段 JSON 搜 PDF
        for item in value:
            if isinstance(item, dict):
                for k in (
                    "商品名称",
                    "name",
                    "productName",
                    "商品名称及规格",
                    "物料编码",
                    "specification",
                ):
                    t = str(item.get(k) or "").strip()
                    if len(t) >= 4:
                        return t
        import json

        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        import json

        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


_SKIP_HIGHLIGHT_KEYS = frozenset(
    {"rule_engine_status", "documentType", "items_meta", "field_plan"}
)


def collect_highlight_fields(
    fields: Dict[str, Any],
    *,
    label_fn=None,
    skip_empty: bool = True,
) -> List[Dict[str, Any]]:
    """整理可高亮字段列表。"""
    items: List[Dict[str, Any]] = []
    for i, (key, raw) in enumerate(fields.items()):
        if key.startswith("_") or key in _SKIP_HIGHLIGHT_KEYS:
            continue
        text = field_value_text(raw)
        if skip_empty and not text:
            continue
        if text.lower() in {"none", "null", "nan", "-"}:
            continue
        label = label_fn(key) if label_fn else key
        color = _PALETTE[i % len(_PALETTE)]
        items.append(
            {
                "key": key,
                "label": label,
                "value": text,
                "color": color,
                "color_css": f"rgb({color[0]},{color[1]},{color[2]})",
            }
        )
    return items


def match_ocr_blocks(
    value: str,
    blocks: Sequence[Dict[str, Any]],
    *,
    min_len: int = 2,
    image_size: Optional[Tuple[float, float]] = None,
) -> List[Dict[str, Any]]:
    """在 OCR 文本块中匹配字段值，返回带 bbox 的命中。

    长信息栏/合计行会先按针收缩；收缩成功后不再因「块文本太长」丢弃购方/销方。
    """
    variants = _value_search_variants(value)
    needles = [_norm(v) for v in variants if len(_norm(v)) >= min_len or _norm(v).isdigit()]
    if not needles:
        compact = _norm(value)
        if compact:
            needles = [compact]
    if not needles:
        return []
    # 长针优先（公司全称 > 截断前缀）
    needles = sorted(set(needles), key=lambda s: (-len(s), s))
    raw_by_norm = {_norm(v): str(v) for v in variants if _norm(v)}

    img_w, img_h = (0.0, 0.0)
    if image_size and len(image_size) >= 2:
        img_w, img_h = float(image_size[0] or 0), float(image_size[1] or 0)

    scored: List[Tuple[float, Dict[str, Any]]] = []
    for b in blocks or []:
        text = str(b.get("text") or "")
        hay = _norm(text)
        if not hay:
            continue
        bbox = b.get("bbox")
        if not bbox or len(bbox) < 4:
            continue
        best_needle = ""
        for needle in needles:
            if needle in hay or (len(hay) >= 4 and len(needle) >= 4 and hay in needle):
                best_needle = needle
                break
        if not best_needle:
            continue
        try:
            x0, y0, x1, y1 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
        except (TypeError, ValueError):
            continue
        norm_like = 0 <= x0 <= 1.5 and 0 <= y0 <= 1.5 and 0 <= x1 <= 1.5 and 0 <= y1 <= 1.5
        if norm_like and img_w > 0 and img_h > 0:
            x0, y0, x1, y1 = x0 * img_w, y0 * img_h, x1 * img_w, y1 * img_h
        w = max(0.0, x1 - x0)
        h = max(0.0, y1 - y0)
        area = w * h

        def _too_big(ww: float, hh: float, aa: float) -> bool:
            if img_w > 0 and img_h > 0:
                return ww > img_w * 0.55 or hh > img_h * 0.22 or aa > img_w * img_h * 0.12
            if norm_like:
                return ww > 0.55 or hh > 0.22 or aa > 0.12
            return ww > 900 or hh > 420 or aa > 280_000

        display_needle = raw_by_norm.get(best_needle) or best_needle
        tightened = False
        # 块比针长、或框偏大：按针收缩（购方信息栏 / 日期混排 / 合计行）
        if len(hay) > len(best_needle) + 2 or _too_big(w, h, area) or h > 60:
            tight = _tighten_bbox_to_needle(text, [x0, y0, x1, y1], display_needle)
            if not tight and display_needle != best_needle:
                tight = _tighten_bbox_to_needle(text, [x0, y0, x1, y1], best_needle)
            if tight:
                x0, y0, x1, y1 = tight
                w = max(0.0, x1 - x0)
                h = max(0.0, y1 - y0)
                area = w * h
                tightened = True

        if _too_big(w, h, area):
            continue

        if len(best_needle) <= 3:
            hay_digits = re.sub(r"[^\d.]", "", hay)
            almost_exact = hay == best_needle or hay_digits == best_needle
            tightened_ok = best_needle in hay and w <= 360 and h <= 120
            if not almost_exact and not tightened_ok:
                continue

        extra = 0.0
        # 仅在「未能收缩」时惩罚超长块；收缩成功的购方/销方应保留
        if (not tightened) and len(hay) > max(24, len(best_needle) * 4):
            extra += 1e6
        if w > 0 and h / max(w, 1e-6) > 3.5:
            extra += 5e5
        closeness = 0 if tightened else abs(len(hay) - len(best_needle)) * 20
        # 越贴针、面积越小越好；优先已收缩命中
        score = extra + area + closeness - (50_000 if tightened else 0)
        scored.append(
            (
                score,
                {
                    **b,
                    "text": display_needle if tightened else text,
                    "bbox": [x0, y0, x1, y1],
                    "tightened": tightened,
                },
            )
        )
    scored.sort(key=lambda x: x[0])
    out: List[Dict[str, Any]] = []
    for score, row in scored[:4]:
        if score >= 1e6:
            continue
        out.append(row)
        if len(out) >= 2:
            break
    # 字段对照点格：默认只留最贴的一框，避免购方/日期叠两层
    if len(out) >= 2:
        a0 = (out[0]["bbox"][2] - out[0]["bbox"][0]) * (out[0]["bbox"][3] - out[0]["bbox"][1])
        a1 = (out[1]["bbox"][2] - out[1]["bbox"][0]) * (out[1]["bbox"][3] - out[1]["bbox"][1])
        if a1 > a0 * 1.35 or abs(a1 - a0) < a0 * 0.5:
            out = out[:1]
        elif a1 > a0 * 2.2:
            out = out[:1]
    return out[:1]


def _parse_html_table_rows(html_text: str) -> List[List[Tuple[str, int]]]:
    """粗解析 <tr>/<td>，返回每行 [(cell_text, colspan), ...]。"""
    rows: List[List[Tuple[str, int]]] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html_text, flags=re.I | re.S):
        cells: List[Tuple[str, int]] = []
        for m in re.finditer(r"<t[dh]([^>]*)>(.*?)</t[dh]>", tr, flags=re.I | re.S):
            attrs, raw = m.group(1) or "", m.group(2) or ""
            cm = re.search(r"colspan\s*=\s*[\"']?(\d+)", attrs, flags=re.I)
            span = int(cm.group(1)) if cm else 1
            cell_text = re.sub(r"<[^>]+>", "", raw)
            cell_text = re.sub(r"\s+", " ", cell_text).strip()
            cells.append((cell_text, max(1, span)))
        if cells:
            rows.append(cells)
    return rows


def _tighten_bbox_to_needle(
    text: str,
    bbox: Sequence[float],
    needle: str,
) -> Optional[List[float]]:
    """在文本块内按行+字符比例收缩到针附近。

    购方/销方信息栏、开票日期混排块、合计行「数量合计：912件」都靠这个收紧。
    """
    if not text or not needle or len(bbox) < 4:
        return None
    needle_raw = str(needle).strip()
    if not needle_raw:
        return None
    needle_n = _norm(needle_raw)
    x0, y0, x1, y1 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    width = max(1.0, x1 - x0)
    height = max(1.0, y1 - y0)

    lines = [ln for ln in re.split(r"[\r\n]+", str(text)) if str(ln).strip() != ""]
    if not lines:
        lines = [str(text)]

    line_idx = -1
    line_text = ""
    for i, ln in enumerate(lines):
        if needle_raw in ln or (needle_n and needle_n in _norm(ln)):
            line_idx = i
            line_text = ln
            break

    if line_idx >= 0:
        n_lines = max(len(lines), 1)
        cy0 = y0 + height * (line_idx / n_lines)
        cy1 = y0 + height * ((line_idx + 1) / n_lines)
        idx = line_text.find(needle_raw)
        if idx >= 0:
            start_ratio = idx / max(len(line_text), 1)
            end_ratio = (idx + len(needle_raw)) / max(len(line_text), 1)
        else:
            ln_n = _norm(line_text)
            idx_n = ln_n.find(needle_n) if needle_n else -1
            if idx_n < 0:
                start_ratio, end_ratio = 0.05, 0.95
            else:
                start_ratio = idx_n / max(len(ln_n), 1)
                end_ratio = (idx_n + len(needle_n)) / max(len(ln_n), 1)
    else:
        hay = str(text)
        idx = hay.find(needle_raw)
        if idx >= 0:
            start_ratio = idx / max(len(hay), 1)
            end_ratio = (idx + len(needle_raw)) / max(len(hay), 1)
        else:
            hay_n = _norm(hay)
            idx_n = hay_n.find(needle_n) if needle_n else -1
            if idx_n < 0 or not hay_n:
                return None
            start_ratio = idx_n / max(len(hay_n), 1)
            end_ratio = (idx_n + len(needle_n)) / max(len(hay_n), 1)
        cy0 = y0 + height * 0.18
        cy1 = y1 - height * 0.18

    pad = 0.012
    nx0 = x0 + width * max(0.0, start_ratio - pad)
    nx1 = x0 + width * min(1.0, end_ratio + pad)
    if nx1 - nx0 < min(36.0, width * 0.06):
        mid = (nx0 + nx1) / 2
        half = min(28.0, width * 0.05)
        nx0, nx1 = mid - half, mid + half

    if re.fullmatch(r"\d+(\.\d+)?", needle_raw or ""):
        max_w = max(48.0, len(needle_raw) * 30.0 + 20.0)
        if nx1 - nx0 > max_w:
            if start_ratio >= 0.45:
                nx0 = nx1 - max_w
            else:
                nx1 = nx0 + max_w
    elif len(needle_raw) >= 4:
        est = min(width * 0.92, max(90.0, len(needle_raw) * 26.0 + 28.0))
        if nx1 - nx0 > est:
            if start_ratio >= 0.3:
                nx0 = max(x0, nx1 - est)
            else:
                nx1 = min(x1, nx0 + est)

    vpad = max(2.0, (cy1 - cy0) * 0.10)
    ny0 = cy0 + vpad
    ny1 = cy1 - vpad
    if ny1 <= ny0:
        ny0, ny1 = cy0, cy1
    return [nx0, ny0, nx1, ny1]


def _explode_html_table_block(block: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把 OCR layout 吐出的整表 HTML + 大 bbox，拆成单元格估计框。"""
    text = str(block.get("text") or "")
    bbox = block.get("bbox")
    if "<table" not in text.lower() or not bbox or len(bbox) < 4:
        return []
    try:
        x0, y0, x1, y1 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    except (TypeError, ValueError):
        return []
    rows = _parse_html_table_rows(text)
    if len(rows) < 2:
        return []
    n_rows = len(rows)
    n_cols = max(sum(span for _, span in row) for row in rows) or 1
    tw = max(1.0, x1 - x0)
    th = max(1.0, y1 - y0)
    cell_h = th / n_rows
    out: List[Dict[str, Any]] = []
    for ri, row in enumerate(rows):
        col = 0
        for cell_text, span in row:
            if not cell_text:
                col += span
                continue
            c0 = col
            c1 = min(n_cols, col + span)
            cx0 = x0 + tw * (c0 / n_cols)
            cx1 = x0 + tw * (c1 / n_cols)
            cy0 = y0 + cell_h * ri
            cy1 = y0 + cell_h * min(n_rows, ri + 1)
            pad_x = max(2.0, (cx1 - cx0) * 0.06)
            pad_y = max(2.0, (cy1 - cy0) * 0.12)
            out.append(
                {
                    "text": cell_text,
                    "bbox": [cx0 + pad_x, cy0 + pad_y, cx1 - pad_x, cy1 - pad_y],
                    "page": block.get("page", 0),
                    "source": "html_table_cell",
                }
            )
            # 合计行里常夹「数量合计：912件」——再拆一个数字子针，便于字段 quantity 命中
            for m in re.finditer(
                r"(?:数量合计|合计数量|数量)[：:]\s*([0-9]+(?:\.[0-9]+)?)\s*件?",
                cell_text,
            ):
                sub = _tighten_bbox_to_needle(
                    cell_text,
                    [cx0 + pad_x, cy0 + pad_y, cx1 - pad_x, cy1 - pad_y],
                    m.group(1),
                )
                if sub:
                    out.append(
                        {
                            "text": m.group(1),
                            "bbox": sub,
                            "page": block.get("page", 0),
                            "source": "html_table_qty",
                        }
                    )
            col += span
    return out


def _explode_multiline_info_block(block: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把购方/销方信息栏等按行拆开，便于「名称：公司」精确命中。"""
    text = str(block.get("text") or "")
    bbox = block.get("bbox")
    if not bbox or len(bbox) < 4:
        return []
    if "<table" in text.lower() or "<div" in text.lower():
        return []
    lines = [ln.strip() for ln in re.split(r"[\r\n]+", text) if ln.strip()]
    if len(lines) < 2:
        return []
    try:
        x0, y0, x1, y1 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    except (TypeError, ValueError):
        return []
    th = max(1.0, y1 - y0)
    n = len(lines)
    out: List[Dict[str, Any]] = []
    for i, ln in enumerate(lines):
        cy0 = y0 + th * (i / n)
        cy1 = y0 + th * ((i + 1) / n)
        pad_y = max(1.0, (cy1 - cy0) * 0.08)
        out.append(
            {
                "text": ln,
                "bbox": [x0 + 4, cy0 + pad_y, x1 - 4, cy1 - pad_y],
                "page": block.get("page", 0),
                "source": "info_line",
            }
        )
        # 「名称：xxx」再拆出名称本身
        for sep in ("：", ":"):
            if sep in ln:
                label, _, rest = ln.partition(sep)
                rest = rest.strip()
                if len(rest) >= 4 and ("名称" in label or "名" == label[-1:]):
                    tight = _tighten_bbox_to_needle(ln, [x0 + 4, cy0 + pad_y, x1 - 4, cy1 - pad_y], rest)
                    if tight:
                        out.append(
                            {
                                "text": rest,
                                "bbox": tight,
                                "page": block.get("page", 0),
                                "source": "info_name",
                            }
                        )
                break
    return out


def _expand_ocr_blocks_for_highlight(blocks: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """去重、拆 HTML 整表块/信息栏行，并优先短文本块。"""
    out: List[Dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        text = str(b.get("text") or "").strip()
        if not text:
            continue
        exploded = _explode_html_table_block(b)
        if not exploded:
            exploded = _explode_multiline_info_block(b)
        candidates = exploded if exploded else [b]
        for item in candidates:
            t = str(item.get("text") or "").strip()
            if not t:
                continue
            if "<table" in t.lower() and len(t) > 80:
                continue
            bbox = item.get("bbox")
            key = (t, tuple(bbox or []), item.get("page"), item.get("source"))
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    out.sort(key=lambda x: len(str(x.get("text") or "")))
    return out


def find_in_raw_text(raw_text: str, value: str) -> Optional[Tuple[int, int]]:
    """返回 raw_text 中匹配区间 [start, end)。"""
    if not raw_text or not value:
        return None
    if value in raw_text:
        i = raw_text.find(value)
        return i, i + len(value)
    # 去空白再定位（回映到原文较粗：取包含段）
    n_raw = _norm(raw_text)
    n_val = _norm(value)
    if len(n_val) < 2 or n_val not in n_raw:
        return None
    # 退化：直接展示值本身高亮提示
    return None


def highlight_raw_html(raw_text: str, value: str, *, limit: int = 2500) -> str:
    text = (raw_text or "")[:limit]
    if not value or value not in text:
        return f"<pre style='white-space:pre-wrap;font-size:12px'>{html.escape(text)}</pre>"
    parts = text.split(value)
    out = html.escape(parts[0])
    mark = (
        "<mark style='background:#FFE08A;padding:0 2px;border-radius:2px'>"
        + html.escape(value)
        + "</mark>"
    )
    for p in parts[1:]:
        out += mark + html.escape(p)
    return f"<pre style='white-space:pre-wrap;font-size:12px'>{out}</pre>"


def chips_html(items: Sequence[Dict[str, Any]], selected_key: Optional[str]) -> str:
    bits = []
    for it in items:
        sel = it["key"] == selected_key
        border = "3px" if sel else "1px"
        opacity = "1" if (not selected_key or sel) else "0.45"
        bg = it["color_css"] if sel else "#FFFFFF"
        fg = "#FFFFFF" if sel else it["color_css"]
        val = html.escape((it["value"] or "")[:48])
        lab = html.escape(str(it["label"]))
        bits.append(
            f"<span style='display:inline-block;margin:3px 4px;padding:4px 8px;"
            f"border:{border} solid {it['color_css']};border-radius:6px;"
            f"background:{bg};color:{fg};opacity:{opacity};font-size:12px;"
            f"font-family:Microsoft YaHei,sans-serif'>"
            f"<b>{lab}</b> · {val}</span>"
        )
    return (
        "<div style='line-height:1.7;max-height:120px;overflow:auto;"
        "border:1px solid #E2E8F0;padding:6px;border-radius:8px;"
        "background:#F8FAFC'>"
        + "".join(bits)
        + "</div>"
    )


def _draw_boxes(
    img: Image.Image,
    boxes: Sequence[Tuple[float, float, float, float, Tuple[int, int, int]]],
    *,
    width: int = 3,
) -> Image.Image:
    out = img.convert("RGBA")
    overlay = Image.new("RGBA", out.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for x0, y0, x1, y1, color in boxes:
        fill = (*color, 48)
        outline = (*color, 230)
        draw.rectangle([x0, y0, x1, y1], outline=outline, width=width, fill=fill)
    return Image.alpha_composite(out, overlay).convert("RGB")


def _box_area(box: Tuple[float, float, float, float, Any]) -> float:
    return max(0.0, float(box[2]) - float(box[0])) * max(0.0, float(box[3]) - float(box[1]))


def _is_oversized_box(
    box: Tuple[float, float, float, float, Any],
    *,
    page_width: float,
    page_height: float,
    needle_len: int,
) -> bool:
    """拒掉「框整表/整段」的命中：宽高相对页过大，或相对针长不合理。"""
    x0, y0, x1, y1 = (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
    w = max(0.0, x1 - x0)
    h = max(0.0, y1 - y0)
    if page_width <= 0 or page_height <= 0:
        return False
    if w > page_width * 0.55 or h > page_height * 0.10:
        return True
    if w * h > page_width * page_height * 0.06:
        return True
    # 字段高亮不应接近半页宽（否则像框整表/整行段落）
    if w > 260:
        return True
    # 短针不应画出超宽框（常见于 page.search 吞并整行/整表）
    if needle_len <= 6 and w > page_width * 0.40:
        return True
    if needle_len <= 12 and h > 36:
        return True
    return False


def _boxes_from_char_span(
    chars: Sequence[Dict[str, Any]],
    start: int,
    end: int,
    *,
    color: Tuple[int, int, int],
    page_width: float,
    page_height: float,
    needle_len: int,
) -> List[Tuple[float, float, float, float, Tuple[int, int, int]]]:
    """把一段字符命中按行拆成紧框，避免跨行合成整段大框。"""
    slice_chars = [chars[i] for i in range(start, end) if 0 <= i < len(chars)]
    if not slice_chars:
        return []
    lines: Dict[int, List[Dict[str, Any]]] = {}
    for ch in slice_chars:
        key = int(round(float(ch.get("top") or 0) * 2))
        lines.setdefault(key, []).append(ch)
    boxes: List[Tuple[float, float, float, float, Tuple[int, int, int]]] = []
    for row in lines.values():
        x0 = min(float(c.get("x0") or 0) for c in row)
        x1 = max(float(c.get("x1") or 0) for c in row)
        y0 = min(float(c.get("top") or 0) for c in row)
        y1 = max(float(c.get("bottom") or 0) for c in row)
        box = (x0, y0, x1, y1, color)
        if _is_oversized_box(box, page_width=page_width, page_height=page_height, needle_len=needle_len):
            continue
        if x1 - x0 < 1 or y1 - y0 < 1:
            continue
        boxes.append(box)
    return boxes


def _locate_on_page_chars(
    page: Any,
    variants: Sequence[str],
    *,
    color: Tuple[int, int, int],
) -> List[Tuple[float, float, float, float, Tuple[int, int, int]]]:
    """字符流定位（忽略空白）：比 page.search 更能打中「中文+数字」与跨行条款。"""
    chars = list(page.chars or [])
    if not chars:
        return []
    page_width = float(getattr(page, "width", 0) or 0)
    page_height = float(getattr(page, "height", 0) or 0)
    index_map: List[int] = []
    compact_parts: List[str] = []
    for i, ch in enumerate(chars):
        t = str(ch.get("text") or "")
        if not t or t.isspace():
            continue
        compact_parts.append(t)
        index_map.append(i)
    hay = "".join(compact_parts).lower()
    if not hay:
        return []

    # 较长变体优先，减少短针误命中整表
    ordered = sorted(
        {re.sub(r"\s+", "", str(v)).strip() for v in variants if str(v or "").strip()},
        key=lambda s: (-len(s), s),
    )
    for cand in ordered:
        needle = cand.lower()
        if len(needle) < 1:
            continue
        pos = hay.find(needle)
        if pos < 0:
            continue
        start_i = index_map[pos]
        end_i = index_map[pos + len(needle) - 1] + 1
        boxes = _boxes_from_char_span(
            chars,
            start_i,
            end_i,
            color=color,
            page_width=page_width,
            page_height=page_height,
            needle_len=len(needle),
        )
        if boxes:
            return boxes
    return []


def _locate_on_page_search(
    page: Any,
    variants: Sequence[str],
    *,
    color: Tuple[int, int, int],
) -> List[Tuple[float, float, float, float, Tuple[int, int, int]]]:
    """回退：pdfplumber page.search；过滤过大框，金额类只保留更紧的命中。"""
    page_width = float(getattr(page, "width", 0) or 0)
    page_height = float(getattr(page, "height", 0) or 0)
    ordered = sorted(
        {str(v).strip() for v in variants if str(v or "").strip()},
        key=lambda s: (-len(re.sub(r"\s+", "", s)), s),
    )
    for cand in ordered:
        try:
            found = page.search(cand, regex=False, case=False) or []
        except Exception:
            found = []
        boxes: List[Tuple[float, float, float, float, Tuple[int, int, int]]] = []
        needle_len = len(re.sub(r"\s+", "", cand))
        for f in found:
            box = (
                float(f["x0"]),
                float(f["top"]),
                float(f["x1"]),
                float(f["bottom"]),
                color,
            )
            if _is_oversized_box(
                box, page_width=page_width, page_height=page_height, needle_len=needle_len
            ):
                continue
            boxes.append(box)
        if not boxes:
            continue
        # 多命中时保留面积更小的前 2 个，避免一次框出整表多格
        boxes.sort(key=_box_area)
        return boxes[:2]
    return []


def locate_pdf_boxes(
    path: Path,
    value: str,
    *,
    page_index: int = 0,
    color: Tuple[int, int, int] = (230, 57, 70),
) -> List[Tuple[int, Tuple[float, float, float, float, Tuple[int, int, int]]]]:
    """定位字段值；优先字符级紧框，失败再 page.search。返回 (page_i, box)。"""
    variants = [x for x in _value_search_variants(value) if len(x.strip()) >= 1]
    if not variants:
        return []
    try:
        import pdfplumber
    except ImportError:
        return []
    hits: List[Tuple[int, Tuple[float, float, float, float, Tuple[int, int, int]]]] = []
    try:
        with pdfplumber.open(str(path)) as pdf:
            if not pdf.pages:
                return []
            order: List[int] = []
            if 0 <= page_index < len(pdf.pages):
                order.append(page_index)
            for i in range(len(pdf.pages)):
                if i not in order:
                    order.append(i)
            for i in order:
                page = pdf.pages[i]
                page_boxes = _locate_on_page_chars(page, variants, color=color)
                if not page_boxes:
                    page_boxes = _locate_on_page_search(page, variants, color=color)
                if page_boxes:
                    # 字符级也可能多行；金额等多命中只留最紧的 2 框
                    page_boxes = sorted(page_boxes, key=_box_area)[:2]
                    hits.extend((i, b) for b in page_boxes)
                    break
    except Exception:
        return []
    return hits


def render_pdf_highlighted(
    path: Path,
    boxes_by_page: Dict[int, List[Tuple[float, float, float, float, Tuple[int, int, int]]]],
    *,
    page_index: int = 0,
    scale: float = 2.0,
) -> Optional[Image.Image]:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None
    try:
        doc = pdfium.PdfDocument(str(path))
        if page_index < 0 or page_index >= len(doc):
            page_index = 0
        page = doc[page_index]
        bitmap = page.render(scale=scale)
        pil = bitmap.to_pil()
        page_boxes = boxes_by_page.get(page_index) or []
        if not page_boxes:
            return pil.convert("RGB")
        # pdfplumber top 原点在上；渲染图同向，按页高宽缩放
        # pdfium 渲染尺寸 / PDF 点尺寸
        # 用 pdfplumber 页尺寸做映射更稳
        import pdfplumber

        with pdfplumber.open(str(path)) as pdf:
            pw = float(pdf.pages[page_index].width)
            ph = float(pdf.pages[page_index].height)
        sx = pil.width / pw
        sy = pil.height / ph
        scaled = []
        for x0, y0, x1, y1, color in page_boxes:
            scaled.append((x0 * sx, y0 * sy, x1 * sx, y1 * sy, color))
        return _draw_boxes(pil, scaled, width=4)
    except Exception:
        return None


def render_image_highlighted(
    path: Path,
    boxes: Sequence[Tuple[float, float, float, float, Tuple[int, int, int]]],
    *,
    bbox_normalized: bool = False,
) -> Optional[Image.Image]:
    try:
        img = Image.open(path).convert("RGB")
    except Exception:
        return None
    if not boxes:
        return img
    w, h = img.size
    scaled = []
    for x0, y0, x1, y1, color in boxes:
        if bbox_normalized or (0 <= x0 <= 1.5 and 0 <= x1 <= 1.5):
            scaled.append((x0 * w, y0 * h, x1 * w, y1 * h, color))
        else:
            scaled.append((x0, y0, x1, y1, color))
    return _draw_boxes(img, scaled, width=4)


def build_boxes_for_fields(
    *,
    path: Path,
    items: Sequence[Dict[str, Any]],
    selected_key: Optional[str],
    text_blocks: Sequence[Dict[str, Any]],
    page_index: int = 0,
) -> Tuple[Optional[Image.Image], str]:
    """生成高亮预览图；返回 (image, note)。"""
    suffix = path.suffix.lower()
    focus = [it for it in items if not selected_key or it["key"] == selected_key]
    # 无匹配字段条目时仍渲染原件，避免预览空白
    if not focus and selected_key:
        focus = []

    if suffix == ".pdf":
        by_page: Dict[int, List[Tuple[float, float, float, float, Tuple[int, int, int]]]] = {}
        matched = 0
        render_page = page_index
        for it in focus:
            hits = locate_pdf_boxes(path, it["value"], page_index=page_index, color=it["color"])
            for pi, box in hits:
                by_page.setdefault(pi, []).append(box)
                matched += 1
                # 命中在其它页时，预览切到该页
                if pi != render_page and matched == 1:
                    render_page = pi
        # 无命中时仍渲染首页，便于看原文
        img = render_pdf_highlighted(path, by_page, page_index=render_page)
        if img is None:
            return None, "PDF 渲染失败（缺 pypdfium2）"
        if not focus and selected_key:
            note = f"字段「{selected_key}」无有效值可定位，已显示原件首页"
        elif matched:
            note = f"PDF 精确定位：命中 {matched} 处（第 {render_page + 1} 页）"
        else:
            tried = next((it["value"] for it in focus if it.get("value")), "")
            if tried:
                note = (
                    f"PDF 未搜到字段值「{tried}」（已试金额/日期变体）。"
                    "对照表显示的是系统字段值，若与原件不一致则无法画框，请核对或改字段。"
                )
            else:
                note = "PDF 未搜到一致文本（字段值为空；可填写后重试）"
        return img, note

    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
        boxes: List[Tuple[float, float, float, float, Tuple[int, int, int]]] = []
        matched = 0
        img_size: Optional[Tuple[float, float]] = None
        try:
            with Image.open(path) as probe:
                img_size = (float(probe.size[0]), float(probe.size[1]))
        except Exception:
            img_size = None
        blocks = _expand_ocr_blocks_for_highlight(text_blocks)
        rejected_large = False
        for it in focus:
            val = str(it.get("value") or "")
            hits = match_ocr_blocks(val, blocks, image_size=img_size)
            if not hits and text_blocks and _norm(val):
                needle = _norm(val)
                for b in text_blocks:
                    hay = _norm(str(b.get("text") or ""))
                    if needle and needle in hay:
                        rejected_large = True
                        break
            for h in hits:
                bb = h.get("bbox") or []
                if len(bb) >= 4:
                    boxes.append((bb[0], bb[1], bb[2], bb[3], it["color"]))
                    matched += 1
        img = render_image_highlighted(path, boxes)
        if img is None:
            return None, "图片无法打开"
        if matched:
            return img, f"图片 OCR 坐标定位：命中 {matched} 框"
        if rejected_large:
            return (
                img,
                "OCR 仅命中过大的版面块（如整张明细表），已跳过画框以免框住整表；请用「取证回填」点选精确位置，或重跑行级 OCR。",
            )
        return img, "暂无 OCR 坐标或未匹配到字段；已显示原图 + 叠层标签（重跑 OCR 后可画框）"

    return None, f"格式 {suffix} 暂不支持图上画框"


def extract_text_blocks_from_paddle(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    from src.legacy_ocr.text_blocks import extract_text_blocks_from_paddle as _impl

    return _impl(payload)

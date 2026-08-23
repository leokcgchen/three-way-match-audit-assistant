"""页级切分：标题过渡 + 票号变化 + 低质量强制复核 + 可选页对 VLM。

切分先于分类。不确定页并入最近单元并打标，禁止静默丢页。
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from src.workflow.packet_cards import (
    UNRESOLVED,
    classify_page_text,
    detect_header_card_type,
    load_category_cards,
    title_type_at_page_start,
)

LOW_TEXT_CHARS = 40
INVOICE_NO_RE = re.compile(
    r"(?:发票号码|发票号|Invoice\s*No\.?)\s*[:：]?\s*([0-9A-Za-z\-]{8,24})",
    re.I,
)
CONTRACT_NO_HEAD_RE = re.compile(
    r"(?:合同编号|合同号|合同索引号)\s*[:：=＝]?\s*([A-Za-z0-9\-_]+)"
)


@dataclass
class PageRec:
    source_file: str
    source_path: str
    page: int  # 1-based
    text: str
    extractor: str = "pdf_text"
    quality: str = "ok"  # ok | low
    page_role: str = "content"  # content | blank | separator
    invoice_no: str = ""
    biz_ids: list[str] = field(default_factory=list)
    needs_review: bool = False
    review_reasons: list[str] = field(default_factory=list)
    card_type: str = UNRESOLVED
    host_type: str = UNRESOLVED
    title_type: str | None = None
    boundary_hint: str = ""  # continue | new_document | uncertain | ""


@dataclass
class UnitDraft:
    unit_id: str
    source_file: str
    source_path: str
    pages: list[int]
    split_reason: str
    card_type: str = UNRESOLVED
    host_type: str = UNRESOLVED
    uncertain_pages: list[int] = field(default_factory=list)
    boundary: dict[str, Any] | None = None
    type_candidates: list[dict[str, Any]] = field(default_factory=list)
    keys: dict[str, str] = field(default_factory=dict)
    excerpt: str = ""
    needs_review: bool = False
    review_reasons: list[str] = field(default_factory=list)
    text: str = ""


def _sha16(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8", errors="ignore"))
        h.update(b"|")
    return h.hexdigest()[:16]


def extract_invoice_no(text: str, *, head_only: bool = False) -> str:
    sample = text or ""
    if head_only:
        lines = [ln for ln in sample.splitlines() if ln.strip()][:8]
        sample = "\n".join(lines)
    m = INVOICE_NO_RE.search(sample)
    return (m.group(1) or "").strip() if m else ""


def extract_head_contract_no(text: str) -> str:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()][:8]
    m = CONTRACT_NO_HEAD_RE.search("\n".join(lines))
    return (m.group(1) or "").strip() if m else ""


def extract_page_keys(text: str) -> dict[str, Any]:
    from src.legacy_ocr.ledger_parser import extract_biz_ids_from_free_text

    biz = extract_biz_ids_from_free_text(text or "")
    keys: dict[str, str] = {}
    so = next((x for x in biz if str(x).upper().startswith(("SO", "PO"))), "")
    ht = next((x for x in biz if "HT" in str(x).upper() or str(x).upper().startswith("CT")), "")
    inv = extract_invoice_no(text)
    if so:
        keys["orderNo"] = so
    if ht:
        keys["contractNo"] = ht
    if inv:
        keys["invoiceNo"] = inv
    return {"biz_ids": biz, "keys": keys}


def _primary_so(page: PageRec) -> str:
    for bid in page.biz_ids:
        u = str(bid).upper()
        if u.startswith(("SO", "PO")):
            return str(bid)
    return ""


def pdf_page_count(path: str) -> int:
    p = Path(path)
    if not p.is_file() or p.suffix.lower() != ".pdf":
        return 1 if p.is_file() else 0
    try:
        import pdfplumber

        with pdfplumber.open(str(p)) as doc:
            return len(doc.pages)
    except Exception:
        return 0


def extract_pdf_page_texts(path: str) -> list[dict[str, Any]]:
    """优先文字层；不丢页。"""
    p = Path(path)
    if not p.is_file() or p.suffix.lower() != ".pdf":
        return []
    import pdfplumber

    rows: list[dict[str, Any]] = []
    with pdfplumber.open(str(p)) as doc:
        for i, page in enumerate(doc.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            rows.append({"page": i, "text": text, "extractor": "pdf_text"})
    return rows


def _ocr_rendered_page(path: str, page_1based: int, workdir: Path | None) -> str:
    """扫描页：渲染后走现有 Paddle/文字层适配器。失败返回空串。"""
    try:
        from src.legacy_ocr import LegacyOcrAdapter
        from src.ui.preview_capture import render_preview_page
    except Exception:
        return ""
    src = Path(path)
    try:
        png, _meta = render_preview_page(src, page_index=page_1based - 1, scale=1.6)
    except Exception:
        return ""
    if not png or len(png) < 15000:
        return ""
    folder = Path(workdir) if workdir else src.parent / "_packet_ocr"
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f"{src.stem}_p{page_1based}.png"
    try:
        tmp.write_bytes(png)
        from src.image_preprocess import prepare_for_ocr

        pp = prepare_for_ocr(tmp, cache_dir=folder / "_ocr_work")
        ocr_path = pp.ocr_path
        adapter = LegacyOcrAdapter()
        result = adapter.recognize_document(str(ocr_path), "other", allow_degraded=True)
        return str(result.get("rawText") or "")
    except Exception:
        return ""


def load_file_pages(
    file_name: str,
    path: str,
    *,
    workdir: Path | None = None,
    ocr_low_quality: bool = True,
) -> list[PageRec]:
    src = Path(path)
    cards = load_category_cards()
    pages: list[PageRec] = []
    if src.suffix.lower() == ".pdf":
        raw_pages = extract_pdf_page_texts(str(src))
        if not raw_pages:
            pages.append(
                PageRec(
                    source_file=file_name,
                    source_path=str(src),
                    page=1,
                    text="",
                    extractor="empty",
                    quality="low",
                    needs_review=True,
                    review_reasons=["无法读取页面"],
                )
            )
            return pages
        for row in raw_pages:
            text = str(row.get("text") or "")
            extractor = str(row.get("extractor") or "pdf_text")
            if ocr_low_quality and len(text.strip()) < LOW_TEXT_CHARS:
                ocr_text = _ocr_rendered_page(str(src), int(row["page"]), workdir)
                if ocr_text.strip():
                    text = ocr_text
                    extractor = "paddleocr"
            rec = _page_from_text(
                file_name,
                str(src),
                int(row["page"]),
                text,
                extractor,
                cards,
            )
            pages.append(rec)
        return pages

    # 单图：一文件一页，通常走 standard，这里仍给出页记录
    text = ""
    extractor = "none"
    if src.suffix.lower() in {".txt", ".md"}:
        text = src.read_text(encoding="utf-8", errors="ignore")
        extractor = "text"
    elif ocr_low_quality:
        try:
            from src.legacy_ocr import LegacyOcrAdapter

            result = LegacyOcrAdapter().recognize_document(str(src), "other", allow_degraded=True)
            text = str(result.get("rawText") or "")
            extractor = str(result.get("source") or "ocr")
        except Exception:
            text = ""
    pages.append(_page_from_text(file_name, str(src), 1, text, extractor, cards))
    return pages


def _looks_blank_page(text: str) -> bool:
    visible = re.sub(r"\s+", "", text or "")
    if len(visible) <= 8:
        return True
    # 扫描空白页偶有噪点/页码
    if len(visible) <= 24 and re.fullmatch(r"[0-9\-./第页]+", visible):
        return True
    return False


def _page_from_text(
    file_name: str,
    path: str,
    page: int,
    text: str,
    extractor: str,
    cards: dict[str, Any],
) -> PageRec:
    info = extract_page_keys(text)
    classified = classify_page_text(text, cards)
    blank = _looks_blank_page(text)
    quality = "low" if blank or len((text or "").strip()) < LOW_TEXT_CHARS else "ok"
    reasons: list[str] = []
    needs = bool(classified.get("needs_review"))
    page_role = "content"
    if blank:
        page_role = "blank"
        needs = True
        reasons.append("疑似空白/隔页，建议核对后去掉或并入相邻单")
    elif quality == "low":
        needs = True
        reasons.append("文本过少，强制复核")
    if classified.get("primary_type") == UNRESOLVED and not blank:
        needs = True
        reasons.append("类型证据不足")
    return PageRec(
        source_file=file_name,
        source_path=path,
        page=page,
        text=text or "",
        extractor=extractor,
        quality=quality,
        page_role=page_role,
        invoice_no=str((info.get("keys") or {}).get("invoiceNo") or ""),
        biz_ids=list(info.get("biz_ids") or []),
        needs_review=needs,
        review_reasons=reasons,
        card_type=str(classified.get("primary_type") or UNRESOLVED),
        host_type=str(classified.get("host_type") or UNRESOLVED),
        title_type=detect_header_card_type(text) or title_type_at_page_start(
            text, cards, str(classified.get("primary_type") or "")
        ),
    )


def _vlm_enabled() -> bool:
    try:
        from src.llm.qianfan_vision import vision_status

        st = vision_status()
        return bool(st.get("enabled") and st.get("configured"))
    except Exception:
        return False


def _vlm_max_calls() -> int:
    raw = os.getenv("PACKET_VLM_MAX_CALLS_PER_FILE", "12").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 12


def vlm_boundary_decision(
    prev_png: bytes,
    curr_png: bytes,
) -> Optional[str]:
    """相邻页边界：continue | new_document | uncertain。失败返回 None。"""
    if not prev_png or not curr_png or not _vlm_enabled():
        return None
    try:
        import base64

        import requests

        from config.settings import settings
        from src.llm.qianfan_vision import _api_key, _parse_json, vision_status

        status = vision_status()
        prompt = (
            "两张连续扫描页（左=上一页，右=当前页）。只判断当前页相对上一页的边界。"
            "输出 JSON：{\"boundary_decision\": \"continue\"|\"new_document\"|\"uncertain\", "
            "\"reason\": \"一句话依据\"}。"
            "continue=同一单据续页；new_document=新单据首页；uncertain=看不清。"
            "不要给出已支持、放行、审计结论或类型裁定。"
        )

        def _part(png: bytes) -> dict[str, Any]:
            b64 = base64.b64encode(png).decode("ascii")
            return {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "low"},
            }

        payload = {
            "model": status["model"],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        _part(prev_png),
                        _part(curr_png),
                    ],
                }
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": 220,
            "stream": False,
        }
        response = requests.post(
            str(status["api_url"]),
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {_api_key()}",
            },
            timeout=int(getattr(settings, "QIANFAN_VISION_TIMEOUT_SECONDS", 30) or 30),
        )
        response.raise_for_status()
        data = _parse_json(
            response.json().get("choices", [{}])[0].get("message", {}).get("content")
        )
        decision = str(data.get("boundary_decision") or "").strip().lower()
        if decision in {"continue", "new_document", "uncertain"}:
            return decision
        # 兼容旧布尔字段
        val = data.get("is_new_document")
        if isinstance(val, bool):
            return "new_document" if val else "continue"
        if str(val).strip().lower() in {"true", "1", "yes"}:
            return "new_document"
        if str(val).strip().lower() in {"false", "0", "no"}:
            return "continue"
        return None
    except Exception:
        return None


def vlm_is_new_document(
    prev_png: bytes,
    curr_png: bytes,
) -> Optional[bool]:
    """相邻页是否新单据开头。只出布尔；失败返回 None。"""
    decision = vlm_boundary_decision(prev_png, curr_png)
    if decision == "new_document":
        return True
    if decision == "continue":
        return False
    return None


def _needs_vlm_window(prev: PageRec, curr: PageRec) -> bool:
    """低质量窗口才打视觉：空白/弱文本/无表头未识别，避免全页盲跑。"""
    if prev.page_role == "blank" or curr.page_role == "blank":
        return True
    if prev.quality == "low" or curr.quality == "low":
        return True
    if prev.card_type == UNRESOLVED or curr.card_type == UNRESOLVED:
        return True
    if not curr.title_type and not detect_header_card_type(curr.text):
        # 无页首标题且与当前单元类型冲突时，交给视觉看是不是新首页
        if prev.card_type and curr.card_type and prev.card_type != curr.card_type:
            return True
    return False


def _maybe_vlm_split(prev: PageRec, curr: PageRec, *, budget: list[int] | None = None) -> bool:
    """只对规则吃不准的页对调用。budget=[used, max]。"""
    if not _vlm_enabled():
        return False
    if not _needs_vlm_window(prev, curr):
        return False
    if budget is not None:
        used, limit = budget[0], budget[1]
        if limit <= 0 or used >= limit:
            curr.needs_review = True
            curr.review_reasons.append("视觉边界预算用尽，已并入最近单元待人工核对")
            return False
    try:
        from src.ui.preview_capture import render_preview_page

        prev_png, _ = render_preview_page(Path(prev.source_path), page_index=prev.page - 1, scale=1.2)
        curr_png, _ = render_preview_page(Path(curr.source_path), page_index=curr.page - 1, scale=1.2)
    except Exception:
        return False
    if budget is not None:
        budget[0] += 1
    decision = vlm_boundary_decision(prev_png, curr_png)
    curr.boundary_hint = decision or ""
    if decision == "new_document":
        return True
    if decision in {"continue", "uncertain", None}:
        if decision == "uncertain":
            curr.needs_review = True
            curr.review_reasons.append("视觉边界不确定，已并入最近单元")
        return False
    return False


def page_coverage_ok(pages: list[PageRec], drafts: list[UnitDraft]) -> tuple[bool, list[str]]:
    """每个源页必须恰好落入一个单元；禁止静默丢页/重页。"""
    warnings: list[str] = []
    if not pages:
        return True, warnings
    expected = {p.page for p in pages}
    seen: set[int] = set()
    for draft in drafts:
        for n in draft.pages:
            if n in seen:
                warnings.append(f"页 {n} 被重复归入多个单元")
            seen.add(int(n))
    missing = sorted(expected - seen)
    extra = sorted(seen - expected)
    if missing:
        warnings.append(f"切开后漏页：{missing}")
    if extra:
        warnings.append(f"切开后出现未知页：{extra}")
    return not warnings, warnings


def split_pages_into_units(
    pages: list[PageRec],
    *,
    cards: dict[str, Any] | None = None,
    use_vlm: bool = True,
) -> list[UnitDraft]:
    """顺序切分：标题过渡 → 票号变化 → 订单号换笔 → 可选 VLM；不确定页并入最近单元。"""
    if not pages:
        return []
    cards = cards or load_category_cards()
    fallback = str(cards.get("fallback_type") or UNRESOLVED)
    groups: list[tuple[list[PageRec], str, dict[str, Any] | None]] = []
    current: list[PageRec] = []
    current_type: str | None = None
    current_title: str | None = None
    current_invoice = ""
    current_so = ""
    reason = "source_document_start"
    boundary: dict[str, Any] | None = None
    vlm_budget = [0, _vlm_max_calls() if use_vlm else 0]

    def flush() -> None:
        nonlocal current, reason, boundary
        if current:
            groups.append((current, reason, boundary))

    for page in pages:
        recognised = page.card_type != fallback
        this_title = detect_header_card_type(page.text) or page.title_type
        head_invoice = extract_invoice_no(page.text, head_only=True)
        this_so = _primary_so(page)
        independent_title = bool(current and this_title and this_title != current_title)
        invoice_changed = bool(
            current
            and head_invoice
            and current_invoice
            and head_invoice != current_invoice
        )
        chain_changed = bool(
            current
            and this_so
            and current_so
            and this_so != current_so
            and (this_title or (recognised and page.quality == "ok"))
        )
        vlm_new = False
        if (
            use_vlm
            and current
            and not independent_title
            and not invoice_changed
            and not chain_changed
            and page.page_role != "blank"
        ):
            vlm_new = _maybe_vlm_split(current[-1], page, budget=vlm_budget)

        if not current:
            current = [page]
            current_type = page.card_type if recognised else None
            current_title = this_title
            current_invoice = head_invoice or page.invoice_no
            current_so = this_so
            continue

        # 空白/隔页：并入当前单元，禁止单独成单，留给人工去掉
        if page.page_role == "blank":
            current.append(page)
            continue

        if independent_title:
            flush()
            boundary = {
                "at_page": page.page,
                "from_type": current_type,
                "to_type": page.card_type,
                "reason": "grounded_title_transition",
            }
            current = [page]
            current_type = this_title or page.card_type
            current_title = this_title
            current_invoice = head_invoice or page.invoice_no
            current_so = this_so
            reason = "grounded_title_transition"
            continue

        if invoice_changed:
            flush()
            boundary = {
                "at_page": page.page,
                "from_invoice": current_invoice,
                "to_invoice": head_invoice,
                "reason": "invoice_no_change",
            }
            current = [page]
            current_type = page.card_type if recognised else None
            current_title = this_title
            current_invoice = head_invoice
            current_so = this_so or current_so
            reason = "invoice_no_change"
            continue

        if chain_changed:
            flush()
            boundary = {
                "at_page": page.page,
                "from_so": current_so,
                "to_so": this_so,
                "reason": "chain_id_change",
            }
            current = [page]
            current_type = this_title or (page.card_type if recognised else None)
            current_title = this_title
            current_invoice = head_invoice or page.invoice_no
            current_so = this_so
            reason = "chain_id_change"
            continue

        if vlm_new:
            flush()
            boundary = {
                "at_page": page.page,
                "reason": "vlm_window_boundary",
                "boundary_decision": page.boundary_hint or "new_document",
            }
            current = [page]
            current_type = page.card_type if recognised else None
            current_title = this_title
            current_invoice = head_invoice or page.invoice_no
            current_so = this_so or current_so
            reason = "vlm_window_boundary"
            continue

        current.append(page)
        if recognised and current_type is None:
            current_type = page.card_type
            current_title = this_title
        if not current_invoice and (head_invoice or page.invoice_no):
            current_invoice = head_invoice or page.invoice_no
        if not current_so and this_so:
            current_so = this_so

    flush()

    drafts: list[UnitDraft] = []
    for group, split_reason, group_boundary in groups:
        start, end = group[0].page, group[-1].page
        unit_id = "du_" + _sha16(group[0].source_file, group[0].source_path, start, end)
        text = "\n\n".join(p.text for p in group if p.text and p.page_role != "blank")
        classified = classify_page_text(text, cards)
        info = extract_page_keys(text)
        uncertain = [p.page for p in group if p.needs_review or p.quality == "low" or p.page_role == "blank"]
        reasons: list[str] = []
        for p in group:
            reasons.extend(p.review_reasons)
        needs = bool(uncertain) or bool(classified.get("needs_review"))
        if classified.get("host_type") == UNRESOLVED:
            needs = True
            reasons.append("未能映射到工作台单据类型")
        drafts.append(
            UnitDraft(
                unit_id=unit_id,
                source_file=group[0].source_file,
                source_path=group[0].source_path,
                pages=[p.page for p in group],
                split_reason=split_reason,
                card_type=str(classified.get("primary_type") or UNRESOLVED),
                host_type=str(classified.get("host_type") or UNRESOLVED),
                uncertain_pages=uncertain,
                boundary=group_boundary,
                type_candidates=list(classified.get("candidates") or []),
                keys=dict(info.get("keys") or {}),
                excerpt=(text or "").strip()[:400],
                needs_review=needs,
                review_reasons=list(dict.fromkeys(reasons)),
                text=text,
            )
        )
    ok, cov_warn = page_coverage_ok(pages, drafts)
    if not ok:
        for draft in drafts:
            draft.needs_review = True
            draft.review_reasons.extend(cov_warn)
            draft.review_reasons = list(dict.fromkeys(draft.review_reasons))
    return drafts

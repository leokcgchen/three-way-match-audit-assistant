"""凭证包类别卡：从插件迁入，映射到工作台 doc_type。

闭集外不是「其他」：映射不上则为 unresolved。模型分数不能单独放行类型。
"""

from __future__ import annotations

import html
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

HOST_DOC_TYPES = ("contract", "order", "delivery", "receipt", "invoice", "payment")

# 插件类别 id → 现有工作台 doc_type；未列入则 unresolved
TYPE_MAP: dict[str, str] = {
    "contract": "contract",
    "supplemental_agreement": "contract",
    "order": "order",
    "acceptance_record": "receipt",
    "delivery_receipt": "delivery",
    "delivery_note": "delivery",
    "delivery_acceptance": "delivery",
    "receipt_acceptance": "receipt",
    "invoice": "invoice",
    "bank_receipt": "payment",
    "payment_request": "payment",
}

EXPLICIT_TITLES: dict[str, tuple[str, ...]] = {
    "contract": ("销售合同", "采购合同", "购销合同", "服务合同", "合同书"),
    "supplemental_agreement": ("补充协议", "变更协议", "修订协议"),
    "order": ("销售订单", "采购订单", "purchaseorder", "salesorder"),
    "acceptance_record": ("验收报告", "产品验收单", "交付证明"),
    "delivery_note": ("销售发货单", "出口发货单", "发货单"),
    "delivery_acceptance": ("发货验收单",),
    "receipt_acceptance": (
        "签收验收单",
        "客户签收验收单",
        "目的地交货签收单",
        "客户签收单",
        "签收单",
    ),
    "delivery_receipt": ("送货单", "出库单", "收货确认"),
    "invoice": (
        "增值税专用发票",
        "增值税普通发票",
        "增值税电子发票",
        "增值税发票",
        "数电发票",
        "商业发票",
        "commercialinvoice",
    ),
    "payment_request": ("付款申请", "付款审批单"),
    "settlement_statement": ("结算单", "对账结算单"),
    "bank_receipt": ("电子回单", "银行电子回单", "银行回单", "银行回款单", "收款回单", "付款回单"),
    "confirmation": ("询证函", "函证"),
    "transport_document": ("billoflading", "海运提单", "国际货运单", "airwaybill", "航空运单"),
    "freight_insurance_policy": ("货运保险单", "运输保险单"),
}

# 页首表头：按字数从长到短。扫描件常把公司名写在标题前，所以允许「行尾等于标题」。
HEADER_TITLES: tuple[tuple[str, str], ...] = (
    ("目的地交货签收单", "receipt_acceptance"),
    ("客户签收验收单", "receipt_acceptance"),
    ("增值税电子专用发票", "invoice"),
    ("增值税电子普通发票", "invoice"),
    ("增值税专用发票", "invoice"),
    ("增值税普通发票", "invoice"),
    ("增值税电子发票", "invoice"),
    ("数电发票", "invoice"),
    ("商业发票", "invoice"),
    ("增值税发票", "invoice"),
    ("银行电子回单", "bank_receipt"),
    ("中国工商银行电子回单", "bank_receipt"),
    ("电子回单", "bank_receipt"),
    ("银行回款单", "bank_receipt"),
    ("银行回单", "bank_receipt"),
    ("收款回单", "bank_receipt"),
    ("付款回单", "bank_receipt"),
    ("销售发货单", "delivery_note"),
    ("出口发货单", "delivery_note"),
    ("发货验收单", "delivery_acceptance"),
    ("签收验收单", "receipt_acceptance"),
    ("产品验收单", "acceptance_record"),
    ("客户签收单", "receipt_acceptance"),
    ("验收报告", "acceptance_record"),
    ("销售订单", "order"),
    ("采购订单", "order"),
    ("销售合同", "contract"),
    ("采购合同", "contract"),
    ("购销合同", "contract"),
    ("服务合同", "contract"),
    ("补充协议", "supplemental_agreement"),
    ("付款申请", "payment_request"),
    ("送货单", "delivery_receipt"),
    ("出库单", "delivery_receipt"),
    ("发货单", "delivery_note"),
    ("签收单", "receipt_acceptance"),
    ("合同书", "contract"),
)

UNRESOLVED = "unresolved"
_CARDS_PATH = Path(__file__).resolve().parents[2] / "config" / "packet_category_cards.v1.json"


@lru_cache(maxsize=1)
def load_category_cards() -> dict[str, Any]:
    raw = json.loads(_CARDS_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("packet_category_cards.v1.json 必须是对象")
    return raw


def map_to_host_type(card_type: str | None) -> str:
    key = str(card_type or "").strip()
    if not key or key == UNRESOLVED:
        return UNRESOLVED
    return TYPE_MAP.get(key, UNRESOLVED)


def host_type_label(doc_type: str) -> str:
    from src.workflow.classify import DOC_TYPE_LABELS

    if doc_type == UNRESOLVED:
        return "未识别类型"
    return DOC_TYPE_LABELS.get(doc_type, doc_type)


def _norm_line(text: str) -> str:
    return re.sub(r"\s+", "", text or "").casefold()


def ocr_visible_text(text: str) -> str:
    """Paddle 常把表头包进 HTML/Markdown，先剥标签再认标题。"""
    raw = text or ""
    raw = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", raw)
    raw = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?i)</(?:p|div|tr|h[1-6]|li|td|th)>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = re.sub(r"[#*`]+", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n{2,}", "\n", raw)
    return raw.strip()


def _header_lines(text: str, *, limit: int = 15) -> list[str]:
    visible = ocr_visible_text(text)
    lines = [_norm_line(line) for line in visible.splitlines() if line.strip()]
    return lines[:limit]


def _line_matches_title(line: str, title: str) -> bool:
    if not line or not title:
        return False
    compact = re.sub(r"[（）()·•、,，.。:：\-—_]+", "", line)
    title_c = re.sub(r"[（）()·•、,，.。:：\-—_]+", "", title)
    stripped = re.sub(r"第[0-9一二三四五六七八九十百]+页.*$", "", compact)
    if stripped == title_c or compact == title_c:
        return True
    # 页首常有公司名 / ICBC 等前缀；短标题（发货单）仍收紧，避免正文误切
    extra = max(8, len(title_c) + 4) if len(title_c) <= 3 else max(28, len(title_c) + 16)
    if title_c and title_c in compact and (len(compact) - len(title_c)) <= extra:
        return True
    return False


def detect_header_card_type(text: str) -> str | None:
    """只看页首若干行的表头，最长标题优先。不扫正文条款，避免合同续页被当成订单。"""
    lines = _header_lines(text)
    if not lines:
        return None
    # 续页常以「二、…」开头，表格里会提到别的单据名，不能当新表头
    first = lines[0]
    continuation = bool(re.match(r"^[0-9一二三四五六七八九十]+[、.．]", first))
    scan = lines[:2] if continuation else lines
    if not continuation:
        head = "".join(lines[:8])
        if (
            "发货单位信息" in head
            and "验收单位信息" in head
            and "客户签收验收单" not in head
            and "目的地交货签收单" not in head
            and "销售发货单" not in head
            and "数电发票" not in head
            and "增值税" not in head
        ):
            # 0281 发货验收单无印刷标题；0282「产品验收单」同样是发/收双方栏
            return "delivery_acceptance"
    # 从上往下认第一个表头行，同行取最长标题。禁止用「增值税发票开具之日起」这类正文抢类型。
    for line in scan:
        best: tuple[int, str] | None = None
        for title, cid in HEADER_TITLES:
            nt = _norm_line(title)
            if len(nt) < 3:
                continue
            if continuation:
                compact = re.sub(r"[（）()·•、,，.。:：\-—_]+", "", line)
                stripped = re.sub(r"第[0-9一二三四五六七八九十百]+页.*$", "", compact)
                hit = stripped == nt or compact == nt
            else:
                hit = _line_matches_title(line, nt)
            if hit and (best is None or len(nt) > best[0]):
                best = (len(nt), cid)
        if best:
            return best[1]
    return None


def title_type_at_page_start(text: str, cards: dict[str, Any], grounded_type: str) -> str | None:
    """页首表头等于 grounded_type 时返回该类型（合同续页同标题不得切开）。"""
    if grounded_type == str(cards.get("fallback_type") or UNRESOLVED):
        return None
    header = detect_header_card_type(text)
    if header == grounded_type:
        return grounded_type
    return None


def page_prefix_title_ids(text: str, cards: dict[str, Any]) -> set[str]:
    header = detect_header_card_type(text)
    return {header} if header else set()


def _patterns_of(card: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for item in card.get("identity_patterns") or []:
        if item:
            out.append(str(item))
    for sub in card.get("subtypes") or []:
        if isinstance(sub, dict):
            if sub.get("label"):
                out.append(str(sub["label"]))
            for p in sub.get("patterns") or []:
                if p:
                    out.append(str(p))
    return out


def _hit_patterns(text: str, patterns: list[str]) -> list[str]:
    hits: list[str] = []
    for pat in patterns:
        p = str(pat or "").strip()
        if p and p in (text or "") and p not in hits:
            hits.append(p)
    return hits


def classify_page_text(text: str, cards: dict[str, Any] | None = None) -> dict[str, Any]:
    """页级类别卡打分：必要证据/反证；不足则 unresolved，仅出候选。"""
    cards = cards or load_category_cards()
    fallback = str(cards.get("fallback_type") or UNRESOLVED)
    body = ocr_visible_text(text or "") or (text or "")
    ranked: list[dict[str, Any]] = []
    for card in cards.get("categories") or []:
        if not isinstance(card, dict):
            continue
        cid = str(card.get("id") or "")
        if not cid:
            continue
        identity = _hit_patterns(body, _patterns_of(card) + list(EXPLICIT_TITLES.get(cid, ())))
        required_groups = []
        for group in card.get("required_evidence") or []:
            if not isinstance(group, dict):
                continue
            g_hits = _hit_patterns(body, list(group.get("patterns") or []))
            required_groups.append(
                {
                    "id": group.get("id"),
                    "label": group.get("label"),
                    "hits": g_hits,
                    "ok": bool(g_hits),
                }
            )
        contrary = _hit_patterns(body, [str(x) for x in (card.get("contrary_evidence") or [])])
        strong = _hit_patterns(body, [str(x) for x in (card.get("strong_evidence") or [])])
        req_ok = sum(1 for g in required_groups if g["ok"])
        need = int(card.get("minimum_required_evidence") or 2)
        score = (3 * len(identity)) + (2 * req_ok) + len(strong) - (2 * len(contrary))
        if detect_header_card_type(body) == cid:
            score += 8
        elif title_type_at_page_start(body, cards, cid) == cid:
            score += 4
        ranked.append(
            {
                "id": cid,
                "label": card.get("label") or cid,
                "score": score,
                "identity_hits": identity,
                "required_ok": req_ok,
                "required_need": need,
                "required_groups": required_groups,
                "contrary": contrary,
                "strong": strong,
                "evidence_ok": req_ok >= need and bool(identity or strong or req_ok),
            }
        )
    ranked.sort(key=lambda x: (-int(x["score"]), str(x["id"])))
    top3 = ranked[:3]
    header = detect_header_card_type(body)
    primary = fallback
    if header:
        primary = header
    elif top3:
        best = top3[0]
        if best["evidence_ok"] and int(best["score"]) > 0:
            primary = str(best["id"])
        elif int(best["score"]) >= 4 and not best["contrary"]:
            # 有标题/身份但证据不足：仍作候选，类型标 unresolved 强制复核
            primary = fallback
    header_ok = bool(header)
    evidence_ok = False
    if top3:
        for item in top3:
            if str(item["id"]) == primary:
                evidence_ok = bool(item["evidence_ok"])
                break
        if not evidence_ok:
            evidence_ok = bool(top3[0]["evidence_ok"]) and str(top3[0]["id"]) == primary
    return {
        "primary_type": primary,
        "host_type": map_to_host_type(primary if primary != fallback else None),
        "candidates": top3,
        "header_type": header,
        "needs_review": (not header_ok) and (primary == fallback or not evidence_ok),
    }

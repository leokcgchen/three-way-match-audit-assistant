"""批测路径 LLM 辅助：金额缺字段补抽、合同条款语义补漏、截止控制权日期补抽。

设计约束：
- 金额：只补规则未抽到的计价要素；最终仍用公式重算，不信任模型直接给「正确金额」。
- 条款：规则优先；LLM 仅可追加手册内问题码，且 excerpt 必须能在原文中核验；结论最高 WARNING。
- 截止：只补规则未取得的控制权/验收候选日；应确认日仍由规则用权威日计算，不得用付款账期推算。
- BATCH_LLM_ASSIST=0/off/false 关闭；未配置 Key 时自动跳过。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests
from loguru import logger

from config.settings import is_valid_api_credential, settings

_CUTOFF_EVENT_PRIORITY: Dict[str, int] = {
    "acceptance_completion": 100,
    "acceptance": 95,
    "验收完成": 100,
    "实质验收": 98,
    "customer_signoff": 90,
    "signoff": 90,
    "sign_off": 90,
    "签收": 90,
    "客户签收": 90,
    "bill_of_lading": 80,
    "bol": 80,
    "loaded_on_board": 75,
    "装船": 75,
    "shipment": 70,
    "arrival": 40,
    "到货": 40,
    "delivery": 35,
    "gate_arrival": 30,
}

KNOWN_CLARITY_CODES: Dict[str, Tuple[str, str]] = {
    "CONSIDERATION_FORMULA_AMBIGUOUS": (
        "交易对价",
        "对价形成机制不明确（如随行就市缺少指数/公式）。",
    ),
    "VARIABLE_CONSIDERATION_UNRESOLVED": (
        "交易对价",
        "可变对价/协商调价缺少频率、数据来源或调整公式。",
    ),
    "REBATE_TERM_AMBIGUOUS": (
        "交易对价",
        "返利比例、计算基础或兑现方式不明确。",
    ),
    "UNILATERAL_PRICE_ADJUSTMENT": (
        "交易对价",
        "允许单方调价或以未异议视为接受，价格变更不可双方确认。",
    ),
    "PAYMENT_DUE_DATE_UNDEFINED": (
        "支付条款",
        "付款起算节点或可计算期限不明确。",
    ),
    "PAYMENT_PERIOD_AMBIGUOUS": (
        "支付条款",
        "付款期限表述模糊（如及时结清、酌情顺延）。",
    ),
    "INSTALLMENT_TERM_UNDEFINED": (
        "支付条款",
        "分期比例/节点待另行通知，条件不可执行。",
    ),
    "DP_TENOR_UNDEFINED": (
        "支付条款",
        "D/P 未明确即期/远期或提示后付款天数。",
    ),
    "PERFORMANCE_OBLIGATION_BOUNDARY_UNCLEAR": (
        "履约义务",
        "安装/调试/培训等履约边界不清。",
    ),
    "SOFTWARE_SERVICE_BOUNDARY_UNCLEAR": (
        "履约义务",
        "软件持续支持/升级等服务边界不清。",
    ),
    "TOOLING_AND_GOODS_BOUNDARY_UNCLEAR": (
        "履约义务",
        "模具与货物交付义务边界不清。",
    ),
    "STAND_READY_SERVICE_UNCLEAR": (
        "履约义务",
        "驻场/待命等随时履约义务约定不清。",
    ),
    "CONTROL_TRANSFER_TRIGGER_CONFLICT": (
        "运输及控制权转移",
        "控制权转移时点与贸易术语冲突或不清。",
    ),
    "CIF_CONTROL_POINT_AMBIGUOUS": (
        "运输及控制权转移",
        "CIF 控制权转移点不明确。",
    ),
    "DAP_DELIVERY_PLACE_UNDEFINED": (
        "运输及控制权转移",
        "DAP 交货地点/置于买方处置要求不明确。",
    ),
}


def batch_llm_assist_enabled() -> bool:
    raw = (
        os.getenv("BATCH_LLM_ASSIST")
        or getattr(settings, "BATCH_LLM_ASSIST", None)
        or "1"
    )
    flag = str(raw).strip().lower()
    if flag in {"0", "false", "off", "no", "disable", "disabled"}:
        return False
    key = os.getenv("LLM_API_KEY") or settings.LLM_API_KEY or settings.QIANFAN_API_KEY
    return is_valid_api_credential(str(key or ""))


def _auth_header() -> str:
    key = (
        os.getenv("LLM_API_KEY")
        or settings.LLM_API_KEY
        or settings.QIANFAN_API_KEY
        or ""
    ).strip()
    return f"Bearer {key}"


def _parse_llm_json(raw: str) -> Dict[str, Any]:
    stripped = raw.replace("\ufeff", "").replace("```json", "").replace("```", "").strip()
    candidates = [stripped]
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        candidates.append(stripped[start : end + 1])
    last_err: Optional[Exception] = None
    for cand in candidates:
        for version in (cand, re.sub(r",\s*([}\]])", r"\1", cand)):
            try:
                data = json.loads(version)
                if isinstance(data, dict):
                    return data
            except Exception as exc:  # noqa: BLE001
                last_err = exc
    raise ValueError(f"LLM returned invalid JSON: {last_err}")


def llm_chat_json(
    prompt: str,
    *,
    system: Optional[str] = None,
    max_tokens: int = 1200,
) -> Dict[str, Any]:
    url = os.getenv("LLM_API_URL") or settings.LLM_API_URL
    model = os.getenv("LLM_MODEL") or settings.LLM_MODEL
    messages: List[Dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = requests.post(
        url,
        json={
            "model": model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
        },
        headers={
            "Content-Type": "application/json",
            "Authorization": _auth_header(),
        },
        timeout=60,
    )
    resp.raise_for_status()
    content = (
        resp.json().get("choices", [{}])[0].get("message", {}).get("content") or "{}"
    )
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return _parse_llm_json(str(content))


def excerpt_in_text(excerpt: str, full_text: str) -> bool:
    """核验摘录来自原文（允许空白差异）。"""
    from src.llm.verifier import excerpt_in_text as _excerpt_in_text

    return _excerpt_in_text(excerpt, full_text)


def _clip_blob(documents: Sequence[Dict[str, Any]], limit: int = 10000) -> str:
    parts: List[str] = []
    for doc in documents:
        name = str(doc.get("file_name") or "")
        role = str(doc.get("doc_type") or doc.get("role") or "")
        text = str(doc.get("raw_text") or doc.get("ocr_text") or "")
        if not text.strip():
            continue
        parts.append(f"【{role}|{name}】\n{text[:3500]}")
    blob = "\n\n".join(parts)
    return blob[:limit]


def llm_fill_pricing_gaps(
    *,
    quantity: Optional[float],
    unit_price_excl_tax: Optional[float],
    discount_rate: Optional[float],
    vat_rate: Optional[float],
    documents: Sequence[Dict[str, Any]],
) -> Tuple[Dict[str, Any], List[str], List[Dict[str, Any]]]:
    """规则抽不全时，用 LLM 补计价要素。返回 (补丁字段, 提示, 顾问主张)。"""
    notes: List[str] = []
    need_price = unit_price_excl_tax is None
    need_qty = quantity is None
    if not need_price and not need_qty:
        return {}, notes, []
    if not batch_llm_assist_enabled():
        notes.append("计价要素不足且未启用 BATCH_LLM_ASSIST / 无 LLM Key")
        return {}, notes, []

    blob = _clip_blob(documents)
    if not blob.strip():
        return {}, notes, []

    from src.llm.prompts import (
        UNIFIED_SYSTEM_PROMPT,
        build_amount_gap_fill_user,
        extract_amount_facts,
    )

    prompt = build_amount_gap_fill_user(
        quantity=quantity,
        unit_price_excl_tax=unit_price_excl_tax,
        discount_rate=discount_rate,
        vat_rate=vat_rate,
        documents_blob=blob,
    )
    try:
        data = llm_chat_json(
            prompt, system=UNIFIED_SYSTEM_PROMPT, max_tokens=800
        )
        data = extract_amount_facts(data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("amount LLM gap-fill failed: {}", exc)
        notes.append(f"LLM 补抽计价失败：{exc}")
        return {}, notes, []

    from src.amount_test.pricing_extract import parse_discount_rate, parse_vat_rate
    from src.legacy_ocr.amount_resolve import _parse_number
    from src.llm.verifier import excerpt_in_text as _ex_ok

    # 若模型给了 evidence 摘录，必须能在原文核验；否则丢弃该次补抽
    evidence = data.get("evidence")
    excerpts: List[str] = []
    if isinstance(evidence, list):
        for ev in evidence:
            if isinstance(ev, dict):
                excerpts.append(str(ev.get("text_excerpt") or ev.get("excerpt") or ""))
            else:
                excerpts.append(str(ev))
    elif isinstance(evidence, dict):
        excerpts.append(str(evidence.get("text_excerpt") or evidence.get("excerpt") or ""))
    elif isinstance(evidence, str):
        excerpts.append(evidence)
    checked_excerpts = [e for e in excerpts if (e or "").strip()]
    if checked_excerpts and not any(_ex_ok(e, blob) for e in checked_excerpts):
        notes.append("LLM 计价证据摘录未通过原文核验，已丢弃补抽")
        return {}, notes, []

    patch: Dict[str, Any] = {}
    if need_qty:
        q = _parse_number(data.get("quantity"))
        if q and float(q) > 0:
            patch["quantity"] = float(q)
    if need_price:
        p = _parse_number(data.get("unit_price_excl_tax"))
        if p and float(p) > 0:
            patch["unit_price_excl_tax"] = float(p)
    if discount_rate is None and data.get("discount_rate") is not None:
        d = parse_discount_rate(data.get("discount_rate"), "")
        if d is not None and 0 <= d < 1:
            patch["discount_rate"] = d
    if vat_rate is None and data.get("vat_rate") is not None:
        v = parse_vat_rate(data.get("vat_rate"), str(data.get("vat_rate")))
        if v is not None and 0 <= v <= 1:
            patch["vat_rate"] = v

    if patch:
        notes.append("LLM 已补抽计价要素：" + ",".join(patch.keys()))
        if not checked_excerpts:
            notes.append("计价补抽无摘录（弱核验）；请在字段确认中人工核对")
        logger.info("amount LLM filled {}", list(patch.keys()))
    else:
        notes.append("LLM 未能补全缺失计价要素")

    claims: List[Dict[str, Any]] = []
    excerpt = (checked_excerpts[0] if checked_excerpts else "")[:200]
    # 无摘录时仍建弱候选，编排器可用 require_excerpt=False 入库；默认仍要求摘录
    for key, val in patch.items():
        claims.append(
            {
                "field_name": key,
                "normalized_candidate": val,
                "value": val,
                "excerpt": excerpt,
                "confidence": 0.9 if excerpt else 0.5,
                "kind": "fact",
                "source": "llm",
            }
        )
    return patch, notes, claims


def llm_supplement_clarity_issues(
    text: str,
    existing_codes: Sequence[str],
    *,
    allowed_dimensions: Optional[Sequence[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """规则之后语义补漏。返回 (issue dict 列表, 提示)。

    allowed_dimensions：仅对这些维度开放问题码（按维补漏）；None 表示全部维度。
    """
    notes: List[str] = []
    if not text.strip() or not batch_llm_assist_enabled():
        return [], notes

    from src.llm.prompts import (
        UNIFIED_SYSTEM_PROMPT,
        build_contract_clarity_user,
        extract_contract_issues,
    )

    existing = set(existing_codes or [])
    if allowed_dimensions is None:
        allowed_set = set(KNOWN_CLARITY_CODES) - existing
    else:
        dim_set = {str(d).strip() for d in allowed_dimensions if str(d).strip()}
        allowed_set = {
            code
            for code, (dim, _) in KNOWN_CLARITY_CODES.items()
            if dim in dim_set
        } - existing
    if not allowed_set:
        notes.append("未覆盖维度无待补问题码，跳过 LLM 条款补漏")
        return [], notes

    code_list = sorted(allowed_set)
    prompt = build_contract_clarity_user(
        text=text,
        existing_codes=list(existing),
        allowed_codes=code_list,
    )
    try:
        data = llm_chat_json(
            prompt, system=UNIFIED_SYSTEM_PROMPT, max_tokens=1000
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("clarity LLM assist failed: {}", exc)
        notes.append(f"LLM 条款补漏失败：{exc}")
        return [], notes

    from src.llm.verifier import verify_claims

    raw_issues = extract_contract_issues(data)
    verified = verify_claims(
        raw_issues,
        full_text=text,
        allowed_codes=allowed_set,
        min_confidence=0.85,
        require_excerpt=True,
    )
    out: List[Dict[str, Any]] = []
    for item in verified.accepted:
        code = str(item.get("issue_code") or "").strip()
        if code not in KNOWN_CLARITY_CODES:
            continue
        dim, default_desc = KNOWN_CLARITY_CODES[code]
        desc = str(item.get("description") or "").strip() or default_desc
        excerpt = str(item.get("excerpt") or "").strip()
        try:
            conf = float(item.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0.0
        out.append(
            {
                "issue_code": code,
                "dimension": dim,
                "description": desc,
                "excerpt": excerpt[:200],
                "source": "llm",
                "confidence": conf,
            }
        )
    if verified.rejected:
        notes.append(f"LLM 条款主张核验拒绝 {len(verified.rejected)} 条")
    if out:
        notes.append("LLM 条款补漏：" + ",".join(i["issue_code"] for i in out))
        logger.info("clarity LLM added {}", [i["issue_code"] for i in out])
    elif allowed_dimensions is not None:
        notes.append(
            "LLM 按维补漏未新增："
            + ",".join(sorted(str(d) for d in allowed_dimensions))
        )
    return out, notes


def _parse_iso_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nan", "-"}:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(text[:20], fmt).date().isoformat()
        except ValueError:
            continue
    m = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date().isoformat()
        except ValueError:
            return None
    return None


def _event_priority(event_type: Any) -> int:
    key = str(event_type or "").strip().lower()
    if not key:
        return 10
    if key in _CUTOFF_EVENT_PRIORITY:
        return _CUTOFF_EVENT_PRIORITY[key]
    for k, score in _CUTOFF_EVENT_PRIORITY.items():
        if k in key or key in k:
            return score
    return 10


def pick_authoritative_control_date(
    semantic: Dict[str, Any],
    *,
    full_text: str = "",
) -> Tuple[Optional[str], Optional[Dict[str, Any]], List[str]]:
    """从 LLM 语义结果选权威控制权日；冲突/未决则返回 None。"""
    notes: List[str] = []
    codes = [str(c) for c in (semantic.get("unresolved_codes") or []) if c]
    candidates = list(semantic.get("candidate_control_dates") or [])
    scored: List[Tuple[int, float, str, Dict[str, Any]]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        date = _parse_iso_date(item.get("date") or item.get("event_date"))
        if not date:
            continue
        excerpt = str(item.get("text_excerpt") or item.get("excerpt") or "").strip()
        if full_text and excerpt and not excerpt_in_text(excerpt, full_text):
            continue
        try:
            conf = float(item.get("confidence") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        if conf and conf < 0.85:
            continue
        scored.append((_event_priority(item.get("event_type")), conf, date, item))

    if not scored:
        notes.append("LLM 未提供可核验的控制权候选日")
        return None, None, notes

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    top_score, top_conf, top_date, top_item = scored[0]
    # 同分不同日 → 冲突，阻断确定性结论
    peer_dates = {d for s, _c, d, _i in scored if s == top_score}
    unique = bool(semantic.get("unique_control_point_resolved"))
    if len(peer_dates) > 1 or (codes and not unique):
        notes.append(
            "控制权节点不唯一/存在冲突："
            + ",".join(sorted(peer_dates)[:4])
            + (("; " + ",".join(codes)) if codes else "")
        )
        return None, top_item, notes

    notes.append(
        f"LLM 控制权候选日采用 {top_date}"
        f"（event={top_item.get('event_type')}, conf={top_conf or 'n/a'}）"
    )
    return top_date, top_item, notes


def llm_fill_cutoff_control_date(
    *,
    documents: Sequence[Dict[str, Any]],
    rule_fields: Optional[Dict[str, Any]] = None,
    business_id: str = "",
) -> Tuple[Optional[str], Dict[str, Any], List[str]]:
    """规则抽不到控制权/验收日时，用 CUTOFF_SEMANTIC_EXTRACTION 补候选日。

    返回 (采用日或None, 语义结构化结果, 提示)。不计算应确认日，不加付款账期。
    """
    notes: List[str] = []
    semantic: Dict[str, Any] = {}
    if not batch_llm_assist_enabled():
        notes.append("控制权日期不足且未启用 BATCH_LLM_ASSIST / 无 LLM Key")
        return None, semantic, notes

    blob = _clip_blob(documents)
    if not blob.strip():
        notes.append("无可用 OCR 文本，跳过截止语义补抽")
        return None, semantic, notes

    from src.llm.prompts import (
        UNIFIED_SYSTEM_PROMPT,
        build_cutoff_semantic_user,
        extract_cutoff_semantic,
    )

    prompt = build_cutoff_semantic_user(
        documents_blob=blob,
        rule_fields=rule_fields or {},
        business_id=business_id,
    )
    try:
        data = llm_chat_json(
            prompt, system=UNIFIED_SYSTEM_PROMPT, max_tokens=1000
        )
        semantic = extract_cutoff_semantic(data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cutoff LLM semantic fill failed: {}", exc)
        notes.append(f"LLM 截止语义补抽失败：{exc}")
        return None, semantic, notes

    chosen, _item, pick_notes = pick_authoritative_control_date(
        semantic, full_text=blob
    )
    notes.extend(pick_notes)
    if chosen:
        logger.info("cutoff LLM filled control date={}", chosen)
    return chosen, semantic, notes


def enrich_receipt_fields_with_cutoff_llm(
    receipt_fields: Dict[str, Any],
    documents: Sequence[Dict[str, Any]],
    *,
    contract_fields: Optional[Dict[str, Any]] = None,
    business_id: str = "",
) -> Tuple[Dict[str, Any], List[str]]:
    """若规则未选出签收/验收日，则 LLM 补抽并写回字段（不改终态公式）。"""
    from src.utils.date_extractor import pick_receipt_date_from_fields

    notes: List[str] = []
    fields = dict(receipt_fields or {})
    existing = pick_receipt_date_from_fields(fields)
    if existing:
        return fields, notes

    rule_fields = {
        **(contract_fields or {}),
        **fields,
    }
    # 附带合同控制权条款摘要，便于模型判定触发点
    docs = list(documents)
    chosen, semantic, llm_notes = llm_fill_cutoff_control_date(
        documents=docs,
        rule_fields={
            "controlTransferTerms": rule_fields.get("controlTransferTerms"),
            "transportTerms": rule_fields.get("transportTerms"),
            "paymentTerms": rule_fields.get("paymentTerms"),
            "deliveryDate": fields.get("deliveryDate"),
            "acceptanceDate": fields.get("acceptanceDate"),
            "documentDate": fields.get("documentDate"),
        },
        business_id=business_id,
    )
    notes.extend(llm_notes)
    if semantic:
        fields["_cutoffSemantic"] = semantic
    if not chosen:
        if semantic.get("unresolved_codes"):
            fields["_cutoffUnresolved"] = list(semantic.get("unresolved_codes") or [])
        return fields, notes

    fields["acceptanceDate"] = chosen
    fields["receiptDateForCutoff"] = chosen
    fields["documentDate"] = fields.get("documentDate") or chosen
    fields["_receiptDateSource"] = "llm_cutoff_semantic"
    return fields, notes

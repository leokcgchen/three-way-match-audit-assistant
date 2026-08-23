from __future__ import annotations

import re
from typing import Any, Optional

# Exact labels from GOSPD01030!E13 data validation.
TERM_PICKUP = "客户自提"
TERM_RECEIPT = "签收确认"
TERM_ACCEPTANCE = "验收确认"
TERM_FCA = "外销-FCA货交承运人"
TERM_FOB = "外销-FOB离岸价格"
TERM_CIF = "外销-CIF成本加保险费、运费"
TERM_CIP = "外销-CIP运费、保险费付至指定目的地"
TERM_DDP = "外销-DDP完税后交货"

DOC_FOB_CIF_CIP = "出口报关单、承运人提单"
DOC_RECEIPT = "签收单"
DOC_ACCEPTANCE = "验收单"
DOC_FCA = "货物运单、报关清单资料"
DOC_PICKUP = "提货单"

_CODE_TO_TERM = {
    "FOB": TERM_FOB,
    "CFR": TERM_FOB,
    "CIF": TERM_CIF,
    "CIP": TERM_CIP,
    "FCA": TERM_FCA,
    "DDP": TERM_DDP,
    "EXW": TERM_PICKUP,
    "CUSTOMER_PICKUP": TERM_PICKUP,
}

_TERM_TO_DOC = {
    TERM_FOB: DOC_FOB_CIF_CIP,
    TERM_CIF: DOC_FOB_CIF_CIP,
    TERM_CIP: DOC_FOB_CIF_CIP,
    TERM_DDP: DOC_RECEIPT,
    TERM_FCA: DOC_FCA,
    TERM_PICKUP: DOC_PICKUP,
    TERM_RECEIPT: DOC_RECEIPT,
    TERM_ACCEPTANCE: DOC_ACCEPTANCE,
}

_BL_NO = re.compile(
    r"(?:B/?L\s*(?:No\.?|number|#)|提单号)\s*[:：]?\s*([A-Z0-9][A-Z0-9\-]{4,})",
    re.I,
)

_DOC_TYPE_TO_EVIDENCE = {
    "bill_of_lading": "承运人提单",
    "customs": "出口报关单",
    "delivery_receipt": "签收单",
    "receipt": "签收单",
    "acceptance": "验收单",
    "booking_or_forwarder": "货物运单",
    "warehouse": "提货单",
}

_BL_NO_LOOSE = re.compile(r"\b(BL[\-_]?[A-Z0-9][\-_A-Z0-9]{3,})\b", re.I)
_FILENAME_BL = re.compile(r"(海运提单|提单|B/?L)", re.I)


def _nominal_code(classification: dict[str, Any]) -> str:
    nom = classification.get("nominal_incoterm") or {}
    return str(nom.get("code") or "").upper()


def _e13_term(classification: dict[str, Any], control: dict[str, Any]) -> str:
    code = _nominal_code(classification)
    if code in _CODE_TO_TERM:
        return _CODE_TO_TERM[code]
    if code == "DAP":
        return TERM_RECEIPT
    event = str((control or {}).get("candidate_event") or "")
    if event == "at_final_acceptance":
        return TERM_ACCEPTANCE
    if event in {"at_customer_receipt", "at_destination_arrival"}:
        return TERM_RECEIPT
    return ""


def _extract_doc_no(documents: list[dict[str, Any]]) -> str:
    def _is_bl(doc: dict[str, Any]) -> bool:
        kind = str(doc.get("document_type") or doc.get("doc_type") or "")
        name = str(doc.get("file_name") or "")
        return kind in {"bill_of_lading", "bl"} or bool(_FILENAME_BL.search(name))

    ordered = sorted(documents, key=lambda d: 0 if _is_bl(d) else 1)
    for doc in ordered:
        text = str(doc.get("raw_text") or "")
        hit = _BL_NO.search(text)
        if hit:
            return hit.group(1)
        fields = doc.get("fields") or {}
        for key in ("documentNo", "blNo", "billOfLadingNo", "deliveryNo"):
            val = str(fields.get(key) or "").strip()
            if val and _BL_NO_LOOSE.search(val):
                return val
        if _is_bl(doc):
            loose = _BL_NO_LOOSE.search(text) or _BL_NO_LOOSE.search(
                str(doc.get("file_name") or "")
            )
            if loose:
                return loose.group(1)
    return ""


def _available_evidence(documents: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    seen = set()
    for doc in documents:
        kind = str(doc.get("document_type") or doc.get("doc_type") or "")
        label = _DOC_TYPE_TO_EVIDENCE.get(kind)
        if label and label not in seen:
            seen.add(label)
            out.append(label)
    return out


def _missing_for_term(term: str, available: list[str]) -> list[str]:
    required = [p.strip() for p in (_TERM_TO_DOC.get(term) or "").split("、") if p.strip()]
    return [item for item in required if item not in available]


def _cutoff_note(term: str, control: dict[str, Any]) -> str:
    date = control.get("candidate_date")
    event = str(control.get("candidate_event") or "")
    if not term:
        return "尚未识别运输条款，截止期长度待补件后确定。"
    if term in {TERM_FOB, TERM_CIF, TERM_CIP, TERM_FCA}:
        base = "以已装船/交承运人单据日为控制权时点，确定期末前后截止期；不得用仓库签收日替代。"
        if event == "at_on_board":
            base = "以已装船提单日（On Board Date）为控制权时点，确定期末前后截止期；不得用仓库签收日替代。"
        if date:
            return f"{base}本笔候选日 {date}。"
        return f"{base}本笔尚缺控制权日期。"
    if term in {TERM_DDP, TERM_RECEIPT}:
        base = "以签收日为控制权时点确定截止期。"
        return f"{base}本笔候选日 {date}。" if date else f"{base}本笔尚缺签收日期。"
    if term == TERM_ACCEPTANCE:
        base = "以验收完成日为控制权时点确定截止期，不得用数量签收日替代。"
        return f"{base}本笔候选日 {date}。" if date else f"{base}本笔尚缺验收日期。"
    if term == TERM_PICKUP:
        base = "以提货日为控制权时点确定截止期。"
        return f"{base}本笔候选日 {date}。" if date else f"{base}本笔尚缺提货日期。"
    return ""


def _exception(classification: dict[str, Any], control: dict[str, Any], missing: list[str]) -> str:
    parts: list[str] = []
    status = classification.get("status")
    candidate = classification.get("candidate_profile")
    code = _nominal_code(classification)
    if status == "major_conflict" and candidate:
        parts.append(
            f"名义{code or '条款'}与实际履约重大冲突，画像接近{candidate}。"
            "截止测试仍按已装船日，不改为到港日。"
        )
    if status == "standard_modified":
        parts.append(f"名义{code}存在修改安排（如代垫/重收费），样本需在异常栏说明。")
    if missing:
        parts.append("缺件：" + "；".join(str(x) for x in missing))
    if control.get("result") == "unresolved" and code:
        parts.append("已识别名义条款，但控制权日期未获支持，P列暂空。")
    return "".join(parts)


def _date_meaning(term: str, control: dict[str, Any]) -> str:
    event = str(control.get("candidate_event") or "")
    if event == "at_on_board" or term in {TERM_FOB, TERM_CIF, TERM_CIP}:
        return "已装船日（On Board Date），不是仓库签收日"
    if event == "at_final_acceptance" or term == TERM_ACCEPTANCE:
        return "验收完成日，不是数量签收日"
    if event in {"at_customer_receipt", "at_destination_arrival"} or term in {TERM_RECEIPT, TERM_DDP}:
        return "签收日"
    if event == "at_carrier_handover" or term == TERM_FCA:
        return "货交承运人日，不是已装船日"
    if term == TERM_PICKUP:
        return "提货日"
    return ""


def project_gospd01030(
    *,
    classification: dict[str, Any],
    control: Optional[dict[str, Any]] = None,
    documents: Optional[list[dict[str, Any]]] = None,
    missing_documents: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Map interpret artifacts onto GOSPD01030 fill-in cells. Does not write the xlsx."""
    control = control or {}
    documents = documents or []
    term = _e13_term(classification, control)
    available = _available_evidence(documents)
    missing_ev = _missing_for_term(term, available)
    extra_missing = [str(x) for x in (missing_documents or []) if x]
    control_date = control.get("candidate_date")
    fillable = bool(term)
    exception = _exception(classification, control, extra_missing)
    return {
        "E13_transport_terms": term,
        "M_transport_terms": term,
        "N_delivery_document_type": _TERM_TO_DOC.get(term, ""),
        "O_delivery_document_no": _extract_doc_no(documents),
        "P_control_date": control_date,
        "P_date_meaning": _date_meaning(term, control),
        "C23_cutoff_period_note": _cutoff_note(term, control),
        "X_exception": exception,
        "V_period_correct": None,
        "available_delivery_evidence": available,
        "missing_delivery_evidence": missing_ev,
        "fillable": fillable,
        "not_exported_to_xlsx": True,
    }

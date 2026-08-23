from __future__ import annotations

from typing import Any


_CANDIDATE_SCENARIO = {
    "CIF-like": "CIF 型履约（卖方安排主运并为买方利益投保）",
    "FCA_or_nonstandard_FOB": "FCA 或非标准 FOB 型履约（未见已装船证据）",
    "DPU-like": "DPU 型履约（目的地卸货后交付）",
}

UNRESOLVED = "无法判断实际贸易模式，请按切段证据复核"


def contract_label_text(classification: dict[str, Any]) -> str:
    nom = classification.get("nominal_incoterm") or {}
    code = nom.get("code")
    if not code:
        return ""
    extra = " ".join(
        x for x in (nom.get("named_place_or_port") or "", nom.get("version") or "") if x
    ).strip()
    return f"{code} {extra}".strip() if extra else str(code)


def deterministic_actual(classification: dict[str, Any]) -> tuple[bool, str]:
    status = classification.get("status") or "insufficient_evidence"
    nom = classification.get("nominal_incoterm") or {}
    code = nom.get("code")
    label = contract_label_text(classification)
    candidate = classification.get("candidate_profile")
    profile = classification.get("actual_fulfillment_profile") or {}
    if status == "standard_consistent" and code:
        return True, label
    if status == "standard_modified" and code:
        hint = profile.get("main_carriage_actual_payer") or "代垫/附加安排"
        return True, f"{code} 型履约（附修改：{hint}）"
    if status == "major_conflict" and candidate:
        return True, _CANDIDATE_SCENARIO.get(str(candidate), f"{candidate} 型履约")
    if status == "not_an_incoterms_transaction":
        return True, "非 Incoterms 交易，按合同交付条款描述履约"
    return False, UNRESOLVED


def project_workbook(classification: dict[str, Any]) -> dict[str, str]:
    status = classification.get("status") or "insufficient_evidence"
    confidence = classification.get("confidence") or "no_conclusion"
    can = classification.get("can_conclude")
    actual = (classification.get("actual_scenario") or "").strip()
    if can is False:
        conclusion = UNRESOLVED
    elif actual:
        conclusion = actual
    else:
        can, conclusion = deterministic_actual(classification)
        if not can:
            conclusion = UNRESOLVED
    return {
        "trading_mode_conclusion": conclusion,
        "status": status,
        "confidence": confidence,
    }

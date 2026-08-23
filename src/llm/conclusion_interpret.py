"""规则结论的 LLM 解读层（不改 PASS/FAIL/WARNING）。

对齐 prompts V1.0：task_type=CONCLUSION_INTERPRETATION + 统一系统提示词。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from loguru import logger

from config.settings import is_valid_api_credential, settings
from src.llm.batch_assist import llm_chat_json
from src.llm.prompts import (
    UNIFIED_SYSTEM_PROMPT,
    build_conclusion_interpret_user,
    extract_conclusion_fields,
)


def conclusion_llm_enabled() -> bool:
    raw = (
        os.getenv("CONCLUSION_LLM_ASSIST")
        or getattr(settings, "CONCLUSION_LLM_ASSIST", None)
        or "1"
    )
    flag = str(raw).strip().lower()
    if flag in {"0", "false", "off", "no", "disable", "disabled"}:
        return False
    key = os.getenv("LLM_API_KEY") or settings.LLM_API_KEY or settings.QIANFAN_API_KEY
    return is_valid_api_credential(str(key or ""))


def _empty(*, reason: str = "", skipped: bool = False) -> Dict[str, Any]:
    return {
        "explanation": reason,
        "review_checklist": [],
        "candidate_issue_types": [],
        "agrees_with_rule": None,
        "escalate_manual": False,
        "skipped": skipped,
        "source": "llm_interpret",
        "prompt_version": "audit-agent-llm-v1.0",
    }


def _normalize(data: Dict[str, Any], rule_status: str) -> Dict[str, Any]:
    flat = extract_conclusion_fields(data)
    agrees = flat.get("agrees_with_rule")
    if isinstance(agrees, str):
        agrees = agrees.strip().lower() in {"1", "true", "yes", "是"}
    elif agrees is not None:
        agrees = bool(agrees)
    escalate = bool(flat.get("escalate_manual"))
    if agrees is False and str(rule_status).upper() == "PASS":
        escalate = True
    checklist = flat.get("review_checklist") or []
    if not isinstance(checklist, list):
        checklist = [str(checklist)]
    candidates = flat.get("candidate_issue_types") or []
    if not isinstance(candidates, list):
        candidates = [str(candidates)]
    return {
        "explanation": str(flat.get("explanation") or "").strip() or "（模型未返回说明）",
        "review_checklist": [str(x).strip() for x in checklist if str(x).strip()][:8],
        "candidate_issue_types": [str(x).strip() for x in candidates if str(x).strip()][
            :6
        ],
        "agrees_with_rule": agrees,
        "escalate_manual": escalate,
        "skipped": False,
        "source": "llm_interpret",
        "prompt_version": "audit-agent-llm-v1.0",
    }


def _should_skip_pass(status: str) -> bool:
    if str(status).upper() != "PASS":
        return False
    raw = (
        os.getenv("CONCLUSION_LLM_ON_PASS")
        or getattr(settings, "CONCLUSION_LLM_ON_PASS", None)
        or "0"
    )
    return str(raw).strip().lower() not in {"1", "true", "on", "yes"}


def _interpret(
    *,
    family: str,
    payload: Dict[str, Any],
    allowed: List[str],
) -> Dict[str, Any]:
    status = str(payload.get("test_status") or payload.get("测试状态") or "")
    if not conclusion_llm_enabled():
        return _empty(reason="未启用 CONCLUSION_LLM_ASSIST / 无 LLM Key", skipped=True)
    if _should_skip_pass(status):
        return _empty(
            reason="规则判定 PASS，已跳过 LLM 解读（设 CONCLUSION_LLM_ON_PASS=1 可开启）。",
            skipped=True,
        )
    prompt = build_conclusion_interpret_user(
        family=family,
        rule_final_payload=payload,
        allowed_issue_types=allowed,
    )
    try:
        data = llm_chat_json(
            prompt, system=UNIFIED_SYSTEM_PROMPT, max_tokens=900
        )
        return _normalize(data, status)
    except Exception as exc:  # noqa: BLE001
        logger.warning("{} conclusion interpret failed: {}", family, exc)
        return _empty(reason=f"LLM 解读失败：{exc}", skipped=True)


def interpret_amount_conclusion(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _interpret(
        family="AMOUNT_ACCURACY",
        payload=payload,
        allowed=[
            "UNIT_PRICE_ENTRY_ERROR",
            "COMMERCIAL_DISCOUNT_ERROR",
            "OUTPUT_VAT_ENTRY_ERROR",
            "AMOUNT_ENTRY_ERROR",
            "LEDGER_BASIS_MISMATCH",
            "NONE",
        ],
    )


def interpret_cutoff_conclusion(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _interpret(
        family="CUTOFF",
        payload=payload,
        allowed=["EARLY_REVENUE", "LATE_REVENUE", "DATE_MISSING", "NONE"],
    )


def interpret_contract_conclusion(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _interpret(
        family="CONTRACT_CLARITY",
        payload=payload,
        allowed=list(payload.get("issue_codes") or [])
        or [
            "CONSIDERATION_FORMULA_AMBIGUOUS",
            "PAYMENT_DUE_DATE_UNDEFINED",
            "PAYMENT_PERIOD_AMBIGUOUS",
            "PERFORMANCE_OBLIGATION_BOUNDARY_UNCLEAR",
            "CONTROL_TRANSFER_TRIGGER_CONFLICT",
            "NONE",
        ],
    )


def format_interpretation_caption(interp: Optional[Dict[str, Any]]) -> str:
    if not interp:
        return ""
    if interp.get("skipped"):
        return str(interp.get("explanation") or "")
    parts: List[str] = []
    if interp.get("explanation"):
        parts.append(str(interp["explanation"]))
    if interp.get("escalate_manual"):
        parts.append("模型建议升级人工复核（未改规则结论）。")
    return " ".join(parts)

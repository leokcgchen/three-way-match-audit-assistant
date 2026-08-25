"""三单匹配的可审计阶段结论。

这里记录的是可复算的规则、输入证据和结果，不记录或展示模型的隐藏推理过程。
三单匹配必须先确认三份单据能构成同一笔业务，再做字段一致性检验；截止性测试
是独立的时间维度测试，不能反向改变三单匹配本身的结论。
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any


_ROLE_LABELS = {
    "contract": "合同",
    "order": "订单",
    "receipt": "签收/验收",
    "delivery": "发货",
    "invoice": "发票",
}
_BUSINESS_KEY_RE = re.compile(r"\b(?:SO|HT|PO)[-_]?\d{2,4}[-_]?\d{3,6}\b", re.I)


def _status(value: Any, fallback: str = "NOT_TESTED") -> str:
    text = str(value or "").upper()
    return text if text in {"PASS", "WARNING", "FAIL", "NOT_TESTED", "SKIPPED"} else fallback


def _document_business_keys(document: dict[str, Any]) -> list[str]:
    fields = dict(document.get("fields") or {})
    raw = " ".join(
        str(value or "")
        for value in (
            fields.get("documentNo"),
            fields.get("orderNo"),
            fields.get("salesOrderNo"),
            fields.get("contractNo"),
            fields.get("remarks"),
            document.get("raw_text"),
            document.get("ocr_text"),
        )
    )
    return sorted({match.upper().replace("_", "-") for match in _BUSINESS_KEY_RE.findall(raw)})


def _date_value(raw: Any) -> date | None:
    text = str(raw or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def build_date_chronology(classified: list[dict[str, Any]]) -> dict[str, Any]:
    """合同→订单→签收/验收→发票的独立时序判断。"""
    by_type: dict[str, dict[str, Any]] = {}
    for item in classified:
        role = str(item.get("doc_type") or "").lower()
        if role and role not in by_type:
            by_type[role] = item
    receipt = by_type.get("receipt") or by_type.get("delivery") or {}
    raw = [
        ("合同日", (by_type.get("contract") or {}).get("fields", {}).get("documentDate")),
        ("订单日", (by_type.get("order") or {}).get("fields", {}).get("documentDate")),
        ("签收/验收日", (receipt.get("fields") or {}).get("acceptanceDate") or (receipt.get("fields") or {}).get("documentDate")),
        ("发票日", (by_type.get("invoice") or {}).get("fields", {}).get("documentDate")),
    ]
    parsed = [(label, _date_value(value), value) for label, value in raw]
    inversions = [
        f"{parsed[i - 1][0]} {parsed[i - 1][1]} 晚于 {label} {value}"
        for i, (label, value, _raw) in enumerate(parsed)
        if i > 0 and value is not None and parsed[i - 1][1] is not None and value < parsed[i - 1][1]
    ]
    missing = [label for label, value, _raw in parsed if value is None]
    if inversions:
        status = "FAIL"
        summary = "时序异常：" + "；".join(inversions)
    elif missing:
        status = "REVIEW"
        summary = "时序待复核：缺少或无法识别" + "、".join(missing)
    else:
        status = "PASS"
        summary = "时序通过：合同日≤订单日≤签收/验收日≤发票日"
    return {
        "status": status,
        "summary": summary,
        "rule": "合同日≤订单日≤签收/验收日≤发票日；先检查所有有效相邻对，倒序优先于缺失。",
        "dates": [{"label": label, "value": str(value or "")} for label, value, _raw in parsed],
        "inversions": inversions,
        "missing": missing,
    }


def build_three_way_audit_view(
    classified: list[dict[str, Any]],
    result: dict[str, Any] | None = None,
    *,
    business_binding_confirmed: bool = False,
) -> dict[str, Any]:
    """构造单据捆绑、字段一致性和规则轨迹三个独立视图。"""
    result = result or {}
    active = [item for item in classified if not item.get("excluded_from_match")]
    roles: dict[str, list[dict[str, Any]]] = {key: [] for key in _ROLE_LABELS}
    for item in active:
        role = str(item.get("doc_type") or "").lower()
        if role in roles:
            roles[role].append(item)

    contract_anchor_confirmed = (
        str(result.get("anchor_source") or "").upper()
        == "CONTRACT_AS_ORDER_ANCHOR"
    )
    anchor_role = (
        "order" if roles["order"]
        else "contract" if roles["contract"] and contract_anchor_confirmed
        else None
    )
    missing: list[str] = []
    if not anchor_role:
        missing.append("order_or_contract")
    if not roles["invoice"]:
        missing.append("invoice")
    if not roles["receipt"] and not roles["delivery"]:
        missing.append("receipt_or_delivery")

    documents: list[dict[str, Any]] = []
    business_keys: set[str] = set()
    document_key_sets: list[set[str]] = []
    for role, docs in roles.items():
        for doc in docs:
            keys = _document_business_keys(doc)
            business_keys.update(keys)
            document_key_sets.append(set(keys))
            documents.append(
                {
                    "role": role,
                    "role_label": _ROLE_LABELS[role],
                    "file_name": str(doc.get("file_name") or "未命名单据"),
                    "business_keys": keys,
                }
            )

    shared_business_keys = (
        set.intersection(*document_key_sets)
        if document_key_sets and all(document_key_sets)
        else set()
    )

    if missing:
        binding_status = "FAIL"
        binding_code = "REQUIRED_DOCUMENT_MISSING"
        binding_reason = "缺少必要单据：" + "、".join(
            "签收/验收或发货" if item == "receipt_or_delivery"
            else "订单或合格合同" if item == "order_or_contract"
            else _ROLE_LABELS[item]
            for item in missing
        )
    elif any(not keys for keys in document_key_sets):
        binding_status = "FAIL"
        binding_code = "BUSINESS_KEY_UNRESOLVED"
        binding_reason = "至少一份单据未提取到可交叉验证的业务编号，不能自动确认属于同一笔业务"
    elif not shared_business_keys and business_binding_confirmed:
        binding_status = "PASS"
        binding_code = "DOCUMENT_GROUP_HUMAN_CONFIRMED"
        binding_reason = "审计师已确认该组单据属于同一抽样业务；字段一致性仍由后续硬规则独立检验"
    elif not shared_business_keys:
        binding_status = "FAIL"
        binding_code = "BUSINESS_REFERENCE_UNCONFIRMED"
        binding_reason = (
            "各单据未出现完全相同的业务引用，不能自动确认属于同一笔业务；"
            "SO/HT/PO 编号仅数字尾号相同只能作为线索，须人工确认："
            + "、".join(sorted(business_keys))
        )
    else:
        binding_status = "PASS"
        binding_code = "DOCUMENT_GROUP_CONFIRMED"
        binding_reason = "单据角色齐全，且存在跨单据完全相同的业务引用：" + "、".join(sorted(shared_business_keys))

    match_result = dict(result.get("match_result") or {})
    comparisons = list(match_result.get("comparisons") or [])
    # 签收单常不载金额：引擎已将其明确标为「该金额维未测/不适用」且订单↔发票
    # 已通过时，不能反向制造 FIELD_MISSING 假异常。因此仅把“不一致且缺失”的项目归为缺项。
    missing_fields = [
        item for item in comparisons
        if not bool(item.get("is_consistent"))
        and ("缺失" in str(item.get("diff_description") or "") or "未提供" in str(item.get("diff_description") or ""))
    ]
    inconsistent_fields = [item for item in comparisons if not bool(item.get("is_consistent"))]
    if binding_status != "PASS":
        field_status, field_code, field_reason = "NOT_TESTED", "BINDING_NOT_CONFIRMED", "单据尚未捆绑为同一业务组，字段一致性不执行"
    elif not comparisons:
        field_status, field_code, field_reason = "FAIL", "FIELD_EVIDENCE_MISSING", "未生成字段比对结果，无法完成三单字段一致性检验"
    elif missing_fields:
        field_status, field_code, field_reason = "FAIL", "FIELD_MISSING", "存在字段缺失，无法完成完整的三单字段一致性检验"
    elif inconsistent_fields:
        field_status, field_code, field_reason = "FAIL", "FIELD_INCONSISTENT_HIGH_RISK", "三单字段存在不一致，形成较高错报或舞弊风险"
    else:
        field_status = _status(match_result.get("overall_status"), "PASS")
        field_code, field_reason = "FIELD_CONSISTENT", "已完成客户、金额和数量等字段的一致性检验"

    if binding_status == "FAIL":
        three_way_status, failure_category = "FAIL", "DOCUMENT_BINDING"
    elif field_status == "FAIL":
        three_way_status, failure_category = "FAIL", "FIELD_CONSISTENCY"
    elif field_status == "WARNING":
        three_way_status, failure_category = "WARNING", "FIELD_CONSISTENCY"
    else:
        three_way_status, failure_category = "PASS", None

    # 决策四态：绑定失败优先 HOLD；字段层沿用引擎 decision；分数不参与
    match_decision = str(match_result.get("decision") or "").upper()
    hold_reason = match_result.get("hold_reason_code")
    decision_reasons = list(match_result.get("decision_reasons") or [])
    if binding_status == "FAIL":
        decision = "HOLD_REVIEW"
        if binding_code == "REQUIRED_DOCUMENT_MISSING":
            hold_reason = "DOCUMENT_MISSING"
        else:
            hold_reason = "AMBIGUOUS_BINDING"
        decision_reasons = [f"D2:{binding_code} {binding_reason}"] + decision_reasons
    elif field_status == "FAIL":
        decision = match_decision if match_decision in {
            "AUTO_PASS", "HOLD_REVIEW", "PASS_WITH_WARNING", "NOT_APPLICABLE"
        } else "HOLD_REVIEW"
        hold_reason = hold_reason or "PAPER_FIELD"
    elif field_status == "WARNING":
        decision = "PASS_WITH_WARNING"
        hold_reason = None
        if not decision_reasons:
            decision_reasons = ["D5:纸面硬规则通过，存在容差内偏差"]
    elif three_way_status == "PASS":
        decision = "AUTO_PASS" if match_decision in {"", "AUTO_PASS"} else match_decision
        hold_reason = None
        if not decision_reasons:
            decision_reasons = ["D6:纸面字段勾稽通过"]
    else:
        decision = match_decision or "HOLD_REVIEW"

    erp_review = dict(match_result.get("erp_review") or {})
    if not erp_review:
        erp_review = {
            "status": "UNAVAILABLE",
            "note": "未接公司 ERP 过账/审批；纸面结论不冒充已过账。",
        }

    cutoff_result = dict(result.get("cutoff_result") or {})
    cutoff_status = _status(cutoff_result.get("测试状态"), "NOT_TESTED")
    if not result.get("cutoff_available", False) and cutoff_status == "NOT_TESTED":
        cutoff_status = "SKIPPED"
    chronology = build_date_chronology(active)

    trace = [
        {
            "stage": "DATE_CHRONOLOGY",
            "title": "独立测试：单据时序",
            "status": chronology["status"],
            "rule": chronology["rule"],
            "evidence": chronology["dates"],
            "outcome": chronology["summary"],
        },
        {
            "stage": "DOCUMENT_BINDING",
            "title": "第一步：单据捆绑",
            "status": binding_status,
            "rule": "订单/合同、签收/验收或发货、发票齐备；至少存在一个跨单据完全相同的业务引用。不同前缀仅数字尾号相同不自动放行。",
            "evidence": documents,
            "outcome": binding_reason,
        },
        {
            "stage": "FIELD_CONSISTENCY",
            "title": "第二步：字段一致性检验",
            "status": field_status,
            "rule": "仅在单据捆绑通过后，对客户、金额、数量等映射字段逐项勾稽；缺失与不一致分别归因。不以打分放行。",
            "evidence": comparisons,
            "outcome": field_reason,
        },
        {
            "stage": "CUTOFF",
            "title": "独立测试：截止性",
            "status": cutoff_status,
            "rule": "以控制权转移/签收日期和财务入账日期判断收入归属期间；该结果不改变三单匹配结论。",
            "evidence": cutoff_result,
            "outcome": str(cutoff_result.get("问题描述") or result.get("cutoff_skipped_reason") or "截止性结果已单独列示"),
        },
    ]
    return {
        "document_binding": {
            "status": binding_status,
            "reason_code": binding_code,
            "reason": binding_reason,
            "documents": documents,
            "business_keys": sorted(business_keys),
            "shared_business_keys": sorted(shared_business_keys),
            "missing_roles": missing,
            "anchor_role": anchor_role,
        },
        "field_consistency": {
            "status": field_status,
            "reason_code": field_code,
            "reason": field_reason,
            "comparisons": comparisons,
            "risk_level": "HIGH" if field_code == "FIELD_INCONSISTENT_HIGH_RISK" else ("MEDIUM" if field_status == "FAIL" else "LOW"),
        },
        "three_way_status": three_way_status,
        "three_way_failure_category": failure_category,
        "cutoff_status": cutoff_status,
        "date_chronology": chronology,
        "decision": decision,
        "decision_reasons": decision_reasons,
        "hold_reason_code": hold_reason,
        "quantity_roles": match_result.get("quantity_roles") or {},
        "slot_reasons": match_result.get("slot_reasons") or {},
        "erp_review": erp_review,
        "decision_trace": trace,
    }

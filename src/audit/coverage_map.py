"""规则覆盖地图 v0：标明各审计维度已检查 / 未检查 / 不适用。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

CoverageStatus = str  # CHECKED | UNCHECKED | NOT_APPLICABLE | PARTIAL


def _status_of_run(ran: bool, test_status: str = "") -> CoverageStatus:
    if not ran:
        return "UNCHECKED"
    if str(test_status).upper() in {"SKIPPED", "N/A", "NA"}:
        return "NOT_APPLICABLE"
    return "CHECKED"


def build_coverage_map(
    *,
    classified: Optional[Sequence[Dict[str, Any]]] = None,
    evidence: Optional[Dict[str, Any]] = None,
    amount: Optional[Dict[str, Any]] = None,
    contract: Optional[Dict[str, Any]] = None,
    three_way: Optional[Dict[str, Any]] = None,
    cutoff: Optional[Dict[str, Any]] = None,
    fields_confirmed: bool = False,
    matching_confirmed: bool = False,
    conclusion_confirmed: bool = False,
    relations: Optional[Sequence[Dict[str, Any]]] = None,
    duplicates: Optional[Dict[str, Any]] = None,
    sample_population: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """根据当前会话/结果构建覆盖地图（不改变任何终态）。"""
    classified = list(classified or [])
    docs_present = {str(x.get("doc_type") or "") for x in classified}
    has_contract = "contract" in docs_present
    has_invoice = "invoice" in docs_present
    has_receipt = "receipt" in docs_present or "warehouse_receipt" in docs_present

    evidence_ran = bool(evidence)
    amount_ran = bool(amount)
    contract_ran = bool(contract)
    three_ran = bool(three_way)
    cutoff_payload = cutoff or (three_way or {}).get("cutoff_result") or {}
    cutoff_ran = bool(cutoff_payload) and str(
        cutoff_payload.get("测试状态") or cutoff_payload.get("test_status") or ""
    ).upper() not in {"", "SKIPPED"}

    rel_list = list(relations or [])
    rel_proposed = sum(
        1 for r in rel_list if str((r or {}).get("status") or "").upper() == "PROPOSED"
    )
    rel_verified = sum(
        1 for r in rel_list if str((r or {}).get("status") or "").upper() == "VERIFIED"
    )
    dup_ran = bool(duplicates and (duplicates.get("ran") or duplicates.get("findings") is not None))
    dup_n = int(((duplicates or {}).get("summary") or {}).get("total") or 0)

    dimensions: List[Dict[str, Any]] = [
        {
            "dimension_id": "HITL_FIELD_CONFIRM",
            "label": "字段人工确认",
            "status": "CHECKED" if fields_confirmed else "UNCHECKED",
            "ceavop": "A/E",
            "evidence": "workflow_fields_confirmed",
            "note": "未确认则 UI 测试环节阻断",
        },
        {
            "dimension_id": "HITL_MATCH_CONFIRM",
            "label": "串单确认",
            "status": "CHECKED" if matching_confirmed else ("PARTIAL" if evidence_ran else "UNCHECKED"),
            "ceavop": "E/C",
            "evidence": "workflow_matching_confirmed",
            "note": "确认证据链/关系/重复号后，才可跑金额与三单",
        },
        {
            "dimension_id": "RELATION_CANDIDATES",
            "label": "单据候选关系表",
            "status": (
                "CHECKED"
                if rel_list and rel_proposed == 0
                else ("PARTIAL" if rel_list else ("PARTIAL" if evidence_ran else "UNCHECKED"))
            ),
            "ceavop": "C/E",
            "result_status": f"V{rel_verified}/P{rel_proposed}/T{len(rel_list)}",
            "note": "PROPOSED→VERIFIED/REJECTED；不上 Neo4j",
        },
        {
            "dimension_id": "DUPLICATE_DETECTION",
            "label": "重复号/多版本检测",
            "status": "CHECKED" if dup_ran else "UNCHECKED",
            "ceavop": "C/E",
            "result_status": f"findings={dup_n}",
            "note": "重复发票号、同合同/订单多文件；只提示不改终态",
        },
        {
            "dimension_id": "EVIDENCE_MATCH",
            "label": "证据匹配（业务编号串联）",
            "status": _status_of_run(evidence_ran, str((evidence or {}).get("status") or "")),
            "ceavop": "E/C",
            "result_status": (evidence or {}).get("status"),
            "note": (evidence or {}).get("issue_description") or "",
        },
        {
            "dimension_id": "AMOUNT_ACCURACY",
            "label": "金额准确性重算",
            "status": _status_of_run(
                amount_ran,
                str(
                    (amount or {}).get("status")
                    or ((amount or {}).get("accuracy_report") or {})
                    .get("amount_test", {})
                    .get("test_status")
                    or ""
                ),
            ),
            "ceavop": "A",
            "result_status": (amount or {}).get("status"),
            "note": "容差 0.02；LLM 不得直接给正确金额",
        },
        {
            "dimension_id": "CONTRACT_CLARITY",
            "label": "合同条款清晰性",
            "status": (
                "NOT_APPLICABLE"
                if not has_contract and not contract_ran
                else _status_of_run(
                    contract_ran,
                    str((contract or {}).get("status") or ""),
                )
            ),
            "ceavop": "O/A",
            "result_status": (contract or {}).get("status"),
            "note": "最高 WARNING，不因条款歧义出账务 FAIL",
        },
        {
            "dimension_id": "THREE_WAY_MATCH",
            "label": "三单匹配",
            "status": _status_of_run(
                three_ran, str((three_way or {}).get("overall_status") or "")
            ),
            "ceavop": "A/E",
            "result_status": (three_way or {}).get("overall_status"),
            "note": "",
        },
        {
            "dimension_id": "CUTOFF",
            "label": "截止性（控制权转移日 vs 入账日）",
            "status": (
                "NOT_APPLICABLE"
                if not has_receipt and not cutoff_ran and not has_invoice
                else _status_of_run(
                    cutoff_ran,
                    str(
                        cutoff_payload.get("测试状态")
                        or cutoff_payload.get("test_status")
                        or ""
                    ),
                )
            ),
            "ceavop": "A/O",
            "result_status": cutoff_payload.get("测试状态")
            or cutoff_payload.get("test_status"),
            "note": "付款账期不参与应确认日",
        },
        {
            "dimension_id": "MATCHING_DISAMBIGUATION",
            "label": "证据匹配 LLM 消歧（候选）",
            "status": (
                "CHECKED"
                if (evidence or {}).get("llm_disambiguation")
                else ("PARTIAL" if evidence_ran else "UNCHECKED")
            ),
            "ceavop": "E",
            "note": "仅候选建议，不改规则终态；需人工采纳",
        },
        {
            "dimension_id": "HITL_CONCLUSION_CONFIRM",
            "label": "测试结论确认",
            "status": (
                "CHECKED"
                if conclusion_confirmed
                else (
                    "PARTIAL"
                    if any([evidence_ran, amount_ran, contract_ran, three_ran])
                    else "UNCHECKED"
                )
            ),
            "ceavop": "A/E/O",
            "evidence": "workflow_conclusion_confirmed",
            "note": "生成底稿前需人工签认当前测试结论",
        },
        {
            "dimension_id": "POPULATION_COMPLETENESS",
            "label": "总体完整性勾稽",
            "status": (
                "PARTIAL"
                if isinstance(sample_population, dict)
                and (sample_population or {}).get("business_ids")
                else "UNCHECKED"
            ),
            "ceavop": "C",
            "note": (
                "已导入上游抽样清单（"
                + str((sample_population or {}).get("count") or 0)
                + " 个业务号）；仍不证明总体完整性/漏记"
                if isinstance(sample_population, dict)
                and (sample_population or {}).get("business_ids")
                else "当前系统未实现总体完整性程序；可通过 PUT sample-population 导入外部抽样清单"
            ),
        },
        {
            "dimension_id": "PRESENTATION_DISCLOSURE",
            "label": "列报与披露",
            "status": "UNCHECKED",
            "ceavop": "P",
            "note": "当前系统未实现",
        },
    ]

    checked = sum(1 for d in dimensions if d["status"] == "CHECKED")
    unchecked = sum(1 for d in dimensions if d["status"] == "UNCHECKED")
    na = sum(1 for d in dimensions if d["status"] == "NOT_APPLICABLE")
    partial = sum(1 for d in dimensions if d["status"] == "PARTIAL")

    return {
        "version": "coverage-map-v1-phase2",
        "summary": {
            "checked": checked,
            "unchecked": unchecked,
            "not_applicable": na,
            "partial": partial,
            "total": len(dimensions),
        },
        "dimensions": dimensions,
        "doc_types_present": sorted(docs_present),
        "fields_confirmed": fields_confirmed,
        "matching_confirmed": matching_confirmed,
        "conclusion_confirmed": conclusion_confirmed,
        "what_pass_does_not_prove": [
            "单据真实有效或未伪造",
            "总体漏记（完整性）",
            "列报披露恰当",
            "规则库未覆盖的新型条款/跨单模式（未知的未知）",
            "未确认的候选关系与未处理的重复号风险",
        ],
    }

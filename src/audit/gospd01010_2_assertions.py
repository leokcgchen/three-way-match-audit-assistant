"""GOSPD01010.2 履约义务抽凭断言。

程序步骤（模板）：
检查合同和其他相关沟通文件，确认管理层是否已适当确定合同中可明确区分的履约义务。

底稿结论列（K）：
- YES 是 / NO 否 / Not applicable 不适用

H 列（若适用）：检查其他相关文件确定交易价格
- Applicable 适用 / Not applicable 不适用
"""

from __future__ import annotations

from typing import Any, Optional

from src.audit.workpaper_notes import attach_workpaper_notes


def _yn_po(ok: Optional[bool]) -> str:
    """履约义务结论枚举（对齐模板下拉 W15:W17）。"""
    if ok is True:
        return "YES 是"
    if ok is False:
        return "NO 否"
    return ""


def _status_bucket(raw: Any) -> Optional[str]:
    s = str(raw or "").strip().upper()
    if not s:
        return None
    if s in {"PASS", "通过", "OK"}:
        return "PASS"
    if s in {"FAIL", "失败", "不通过"}:
        return "FAIL"
    if s in {"WARNING", "WARN", "需关注", "SKIPPED"}:
        return "WARNING"
    return s


_PERF_ISSUE_CODES = frozenset(
    {
        "PERFORMANCE_OBLIGATION_BOUNDARY_UNCLEAR",
        "SOFTWARE_SERVICE_BOUNDARY_UNCLEAR",
        "TOOLING_AND_GOODS_BOUNDARY_UNCLEAR",
        "STAND_READY_SERVICE_UNCLEAR",
        "CONTRACT_MISSING",
        "EVIDENCE_PACK_MISSING",
    }
)


def _collect_issue_codes(contract_res: Optional[dict[str, Any]]) -> list[str]:
    if not isinstance(contract_res, dict):
        return []
    codes: list[str] = []
    extracted = contract_res.get("extracted") or {}
    if isinstance(extracted, dict):
        for x in extracted.get("issue_codes") or []:
            if x:
                codes.append(str(x))
    report = contract_res.get("clarity_report") or {}
    tr = report.get("test_result") if isinstance(report, dict) else {}
    if isinstance(tr, dict) and tr.get("issue_code"):
        codes.append(str(tr["issue_code"]))
    # 结构化 issues（含维度），供履约义务维精确过滤
    for bucket in (
        contract_res.get("checks") or [],
        (tr.get("issues") if isinstance(tr, dict) else None) or [],
        contract_res.get("issues") or [],
    ):
        for it in bucket:
            if not isinstance(it, dict):
                # pydantic model
                code = getattr(it, "issue_code", None)
                if code:
                    codes.append(str(code))
                continue
            if it.get("issue_code"):
                codes.append(str(it["issue_code"]))
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for c in codes:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _perf_related(codes: list[str]) -> list[str]:
    return [
        c
        for c in codes
        if c in _PERF_ISSUE_CODES
        or "PERFORMANCE" in c.upper()
        or "OBLIGATION" in c.upper()
    ]


def _perf_codes_from_dimensioned_issues(
    contract_res: Optional[dict[str, Any]],
) -> list[str]:
    """仅收集维度=履约义务的问题码（避免支付/对价码误伤）。"""
    from src.contract_terms.dimension_status import PERF_DIMENSION

    if not isinstance(contract_res, dict):
        return []
    out: list[str] = []
    report = contract_res.get("clarity_report") or {}
    tr = report.get("test_result") if isinstance(report, dict) else {}
    buckets = [
        contract_res.get("checks") or [],
        (tr.get("issues") if isinstance(tr, dict) else None) or [],
        contract_res.get("issues") or [],
    ]
    for bucket in buckets:
        for it in bucket:
            if hasattr(it, "dimension"):
                dim = str(getattr(it, "dimension") or "")
                code = str(getattr(it, "issue_code", "") or "")
            elif isinstance(it, dict):
                dim = str(it.get("dimension") or it.get("clause_name") or "")
                code = str(it.get("issue_code") or "")
            else:
                continue
            if dim == PERF_DIMENSION and code:
                out.append(code)
    seen: set[str] = set()
    uniq: list[str] = []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def assert_performance_obligation(
    *,
    has_contract: bool,
    contract_res: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """是否已适当确定可明确区分的履约义务。

    GOSPD01010.2 专用口径：优先读 dimension_statuses「履约义务」，
    不因支付/对价等其他维度 WARNING 而留空或否决。
    """
    from src.contract_terms.dimension_status import (
        PERF_DIMENSION,
        performance_obligation_status,
    )

    gaps: list[str] = []
    notes: list[str] = []
    codes = _collect_issue_codes(contract_res)
    # 优先：带维度标注的履约问题；其次：问题码白名单/PERFORMANCE 关键字
    perf_codes = _perf_codes_from_dimensioned_issues(contract_res) or _perf_related(
        codes
    )

    dim_map = None
    if isinstance(contract_res, dict):
        extracted = contract_res.get("extracted") or {}
        if isinstance(extracted, dict):
            dim_map = extracted.get("dimension_statuses")
        if not isinstance(dim_map, dict):
            cr = contract_res.get("clarity_report") or {}
            if isinstance(cr, dict):
                ex2 = cr.get("extracted") or {}
                if isinstance(ex2, dict):
                    dim_map = ex2.get("dimension_statuses")

    po_dim = performance_obligation_status(
        dim_map if isinstance(dim_map, dict) else None
    )

    # 无维度状态时，从 checks 推断
    if not po_dim and isinstance(contract_res, dict):
        for it in contract_res.get("checks") or []:
            if not isinstance(it, dict):
                continue
            if str(it.get("clause_name") or "") == PERF_DIMENSION or str(
                it.get("clause_id") or ""
            ) == "performance_obligation":
                st = _status_bucket(it.get("status"))
                if st == "PASS":
                    po_dim = "CLEAR"
                elif st in {"WARNING", "FAIL"}:
                    po_dim = "AMBIGUOUS"
                break

    if not has_contract or po_dim == "MISSING" or "CONTRACT_MISSING" in codes:
        return {
            "verdict": False,
            "verdict_label": _yn_po(False),
            "gaps": ["未取得销售合同，无法评价履约义务是否可明确区分"],
            "notes": notes,
            "issue_codes": codes,
            "dimension_status": po_dim or "MISSING",
        }

    if po_dim == "AMBIGUOUS" or perf_codes:
        return {
            "verdict": False,
            "verdict_label": _yn_po(False),
            "gaps": [
                "履约义务边界不清："
                + ("、".join(perf_codes) if perf_codes else "见履约义务维度问题")
            ],
            "notes": notes,
            "issue_codes": codes,
            "dimension_status": "AMBIGUOUS",
        }

    if po_dim == "CLEAR":
        return {
            "verdict": True,
            "verdict_label": _yn_po(True),
            "gaps": [],
            "notes": notes
            + (
                ["其他条款维度存在 WARNING，但不影响本表履约义务结论"]
                if _status_bucket((contract_res or {}).get("status")) == "WARNING"
                else []
            ),
            "issue_codes": codes,
            "dimension_status": "CLEAR",
        }

    # 回退：旧数据无维度状态
    status: Optional[str] = None
    if isinstance(contract_res, dict):
        status = _status_bucket(contract_res.get("status"))
        cr = contract_res.get("clarity_report") or {}
        tr = cr.get("test_result") if isinstance(cr, dict) else {}
        if isinstance(tr, dict) and tr.get("test_status"):
            status = _status_bucket(tr.get("test_status")) or status

    if status == "PASS":
        return {
            "verdict": True,
            "verdict_label": _yn_po(True),
            "gaps": [],
            "notes": notes + ["无维度状态字段，按整单 PASS 回退"],
            "issue_codes": codes,
            "dimension_status": "CLEAR",
        }

    if status == "WARNING":
        return {
            "verdict": None,
            "verdict_label": _yn_po(None),
            "gaps": ["合同条款测试 WARNING 且无履约义务维度状态，待复核"],
            "notes": notes,
            "issue_codes": codes,
            "dimension_status": None,
        }

    return {
        "verdict": None,
        "verdict_label": _yn_po(None),
        "gaps": ["合同条款测试未完成或证据不足，履约义务结论待填"],
        "notes": notes,
        "issue_codes": codes,
        "dimension_status": None,
    }


def assert_other_files_for_price(
    *,
    docs_by_type: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """检查其他相关文件确定交易价格（H/I/J）。"""
    # 优先顺序：订单 → 发票 → 回款 → 发货/签收（有金额时）
    type_labels = {
        "order": "销售订单",
        "invoice": "增值税发票/商业发票",
        "payment": "银行回款单",
        "delivery": "销售发货单",
        "receipt": "客户签收/验收单",
    }
    for dtype in ("order", "invoice", "payment", "delivery", "receipt"):
        doc = docs_by_type.get(dtype)
        if not doc:
            continue
        from src.models.field_values import rule_readable_fields

        fields = rule_readable_fields(doc)
        amt = fields.get("totalAmount") or fields.get("amount") or doc.get("totalAmount")
        idx = (
            fields.get("documentNo")
            or fields.get("orderNo")
            or fields.get("invoiceNo")
            or fields.get("contractNo")
            or doc.get("file_name")
            or ""
        )
        # 有文件即视为可用于佐证交易价格（金额可在合同列体现）
        return {
            "applicable_label": "Applicable 适用",
            "file_type": type_labels.get(dtype, dtype),
            "file_index": str(idx),
            "has_amount": amt not in (None, ""),
        }

    return {
        "applicable_label": "Not applicable 不适用",
        "file_type": "",
        "file_index": "",
        "has_amount": False,
    }


def build_gospd01010_2_assertions(
    *,
    docs: list[dict[str, Any]],
    job: dict[str, Any],
    chain_id: str = "",
    apply_job_tests: bool = True,
) -> dict[str, Any]:
    by: dict[str, dict[str, Any]] = {}
    for d in docs or []:
        t = str(d.get("doc_type") or "")
        if t and t not in by:
            by[t] = d

    contract = by.get("contract")
    has_contract = bool(contract) and bool(
        (contract.get("raw_text") or "").strip()
        or (contract.get("fields") or {})
    )

    contract_res = None
    if apply_job_tests:
        samples = job.get("gospd_sample_results") if isinstance(job.get("gospd_sample_results"), dict) else {}
        per = samples.get(chain_id) or {} if chain_id else {}
        contract_res = per.get("contract_terms") if isinstance(per, dict) else None
        if not isinstance(contract_res, dict):
            contract_res = job.get("contract_terms") if isinstance(job.get("contract_terms"), dict) else None

    po = assert_performance_obligation(has_contract=has_contract, contract_res=contract_res)
    other = assert_other_files_for_price(docs_by_type=by)

    gaps = list(po.get("gaps") or [])
    exception = "；".join(g for g in gaps if g) if gaps else ""

    out: dict[str, Any] = {
        "performance_obligation": po,
        "other_files": other,
        "po_label": po.get("verdict_label") or "",
        "other_applicable": other.get("applicable_label") or "",
        "other_file_type": other.get("file_type") or "",
        "other_file_index": other.get("file_index") or "",
        "exception": exception,
        "all_ok": po.get("verdict") is True,
        "all_ok_label": po.get("verdict_label") or "",
    }
    return attach_workpaper_notes(
        out,
        job=job,
        chain_id=chain_id,
        contract_res=contract_res if isinstance(contract_res, dict) else None,
        empty_verdict_labels=(
            ["履约义务区分"] if not (po.get("verdict_label") or "").strip() else None
        ),
    )

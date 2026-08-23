"""GOSPD01010.1 三列结论断言层。

将现有测试结果 + 单据存在性补检，映射为底稿步骤结论：
- 步骤1：合同法律上可执行（非仅条款清晰）
- 2.1：交易价格/收入金额准确性（计价为主；分摊另标）
- 2.2：控制权是否已转移（交付证据 + 三单/截止）

口径：证据不足 → 不写 Yes（空或待复核说明进 exception），避免假结论。
"""

from __future__ import annotations

import re
from typing import Any, Optional


Verdict = str  # Yes 是 | No 否 | ""（待复核/不足）


def _yn(ok: Optional[bool]) -> Verdict:
    if ok is True:
        return "Yes 是"
    if ok is False:
        return "No 否"
    return ""


def _status_bucket(status: Any) -> Optional[str]:
    s = str(status or "").upper().strip()
    if not s:
        return None
    if s in {"PASS", "OK", "通过"}:
        return "PASS"
    if s in {"FAIL", "ERROR", "未通过"}:
        return "FAIL"
    if s in {"WARNING", "WARN", "需关注"}:
        return "WARNING"
    if s in {"SKIPPED", "N/A", "NA"}:
        return "SKIPPED"
    return None


def _f(doc: Optional[dict[str, Any]], *keys: str) -> Any:
    if not doc:
        return None
    from src.models.field_values import rule_readable_fields

    fields = rule_readable_fields(doc)
    for k in keys:
        v = fields.get(k)
        if v is not None and str(v).strip() and str(v).strip().lower() not in {
            "none",
            "null",
            "nan",
            "-",
        }:
            return v
    return None


def _filled(value: Any) -> bool:
    return value is not None and bool(str(value).strip()) and str(value).strip().lower() not in {
        "none",
        "null",
        "nan",
        "-",
    }


def _by_type(docs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in docs or []:
        dt = str(item.get("doc_type") or "other")
        if dt == "other":
            continue
        prev = out.get(dt)
        if prev is None:
            out[dt] = item
            continue
        score = len([v for v in (item.get("fields") or {}).values() if _filled(v)])
        prev_score = len([v for v in (prev.get("fields") or {}).values() if _filled(v)])
        if score >= prev_score:
            out[dt] = item
    return out


def _raw_blob(docs: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for d in docs or []:
        parts.append(str(d.get("raw_text") or ""))
        fields = d.get("fields") or {}
        for k in (
            "paymentTerms",
            "settlementTerms",
            "controlTransferTerms",
            "transportTerms",
            "remarks",
            "performanceObligations",
        ):
            if fields.get(k):
                parts.append(str(fields.get(k)))
    return "\n".join(parts)


_APPROVAL_RE = re.compile(
    r"(签字|签署|盖章|公章|合同专用章|授权代表|法定代表人|双方盖章|已批准|批准生效|signed|seal|authorized)",
    re.I,
)
_SUBSTANCE_RE = re.compile(
    r"(商业实质|真实交易|买卖合同|购销|销售合同|supply of goods|sale of goods|purchase and sale)",
    re.I,
)
_COLLECT_RISK_RE = re.compile(
    r"(坏账|无法收回|信用风险极高|拒付|预期无法收回|doubtful|uncollectible)",
    re.I,
)


def _contract_status(contract_res: Optional[dict[str, Any]]) -> Optional[str]:
    if not contract_res:
        return None
    report = contract_res.get("clarity_report") or contract_res.get("report") or {}
    tr = report.get("test_result") or {}
    return _status_bucket(contract_res.get("status") or tr.get("test_status"))


def _amount_status(amount: Optional[dict[str, Any]]) -> Optional[str]:
    if not amount:
        return None
    ar = amount.get("accuracy_report") or {}
    at = ar.get("amount_test") or {}
    return _status_bucket(amount.get("status") or at.get("test_status"))


def _three_way_pieces(three_way: Optional[dict[str, Any]]) -> tuple[Optional[str], Optional[str]]:
    if not three_way:
        return None, None
    match = three_way.get("match_result") or {}
    if hasattr(match, "model_dump"):
        match = match.model_dump()
    cutoff = three_way.get("cutoff_result") or {}
    if hasattr(cutoff, "model_dump"):
        cutoff = cutoff.model_dump()
    m = _status_bucket(three_way.get("overall_status") or match.get("overall_status"))
    c = _status_bucket(cutoff.get("测试状态")) if isinstance(cutoff, dict) else None
    return m, c


def assert_step1_enforceable(
    *,
    docs: list[dict[str, Any]],
    contract_res: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """步骤1：合同法律上可执行（四要素 + 合同存在）。"""
    by = _by_type(docs)
    contract = by.get("contract")
    order = by.get("order")
    payment = by.get("payment")
    blob = _raw_blob([d for d in docs if d.get("doc_type") in {"contract", "order"}])

    sub: dict[str, Any] = {
        "contract_present": bool(contract),
        "rights_payment_identifiable": False,
        "commercial_substance": False,
        "approval_commitment": False,
        "collectibility_ok": None,  # None=证据不足
    }
    gaps: list[str] = []
    notes: list[str] = []

    if not contract:
        gaps.append("缺销售合同")
    payment_terms = _f(contract, "paymentTerms", "settlementTerms") or _f(
        order, "paymentTerms", "settlementTerms"
    )
    rights_ok = _filled(payment_terms)
    # 条款测试 FAIL 且支付/对价维度出问题 → 权利付款不可识别
    cst = _contract_status(contract_res)
    report = (contract_res or {}).get("clarity_report") or (contract_res or {}).get("report") or {}
    issues = ((report.get("test_result") or {}).get("issues")) or []
    pay_fail = any(
        str(i.get("dimension") or "") in {"支付条款", "交易对价"}
        and _status_bucket((contract_res or {}).get("status")) == "FAIL"
        for i in issues
    ) or (
        cst == "FAIL"
        and any(str(i.get("dimension") or "") in {"支付条款", "交易对价"} for i in issues)
    )
    if pay_fail:
        rights_ok = False
        gaps.append("支付/对价条款不可执行（条款测试FAIL）")
    elif not rights_ok:
        gaps.append("未识别付款条件/对价权利（paymentTerms）")
    sub["rights_payment_identifiable"] = bool(rights_ok)

    substance = bool(_SUBSTANCE_RE.search(blob)) or bool(contract)
    # 有正式销售合同本身通常暗示购销商业实质；无合同则否
    if contract and not _SUBSTANCE_RE.search(blob):
        notes.append("未检出「商业实质」字样，已按销售合同存在作弱通过")
    sub["commercial_substance"] = substance
    if not substance:
        gaps.append("未见商业实质/购销安排证据")

    approval = bool(_APPROVAL_RE.search(blob))
    sub["approval_commitment"] = approval
    if not approval:
        gaps.append("未见签字/盖章/批准/授权代表等承诺履行证据")

    # 对价很可能收回：有回款单且金额>0 → 通过；正文明确无法收回 → 否；否则证据不足
    if payment and _filled(_f(payment, "totalAmount", "amount")):
        sub["collectibility_ok"] = True
        notes.append("存在回款单金额，支持「很可能收回」")
    elif _COLLECT_RISK_RE.search(blob):
        sub["collectibility_ok"] = False
        gaps.append("正文提示无法收回/信用风险")
    else:
        sub["collectibility_ok"] = None
        gaps.append("无回款/信用证据，无法认定「对价很可能收回」")

    # 条款整体 FAIL 且非仅履约边界 → 倾向 No
    hard_fail = cst == "FAIL"

    # 支付/对价维度的 WARNING 也视为权利不可唯一认定（训练集预期人工复核）
    pay_unclear = any(
        str(i.get("dimension") or "") in {"支付条款", "交易对价"}
        for i in issues
    ) and cst in {"WARNING", "FAIL"}
    if pay_unclear:
        rights_ok = False
        if "支付/对价条款不可执行（条款测试FAIL）" not in gaps and "支付/对价条款不清（条款测试WARNING）" not in gaps:
            gaps.append(
                "支付/对价条款不清（条款测试WARNING）"
                if cst == "WARNING"
                else "支付/对价条款不可执行（条款测试FAIL）"
            )
    sub["rights_payment_identifiable"] = bool(rights_ok)

    critical_pass = (
        sub["contract_present"]
        and sub["rights_payment_identifiable"]
        and sub["commercial_substance"]
        and sub["approval_commitment"]
        and sub["collectibility_ok"] is True
    )
    critical_fail = (
        hard_fail
        or sub["collectibility_ok"] is False
        or (not sub["contract_present"])
        or pay_fail
        # 支付条款 WARNING 只挡 Yes，不直接定性 No（对齐训练集：不自动账务错报）
    )

    if critical_fail and not critical_pass:
        verdict: Optional[bool] = False
    elif critical_pass and not hard_fail:
        verdict = True
    else:
        verdict = None  # 待复核

    # 条款 WARNING：一律不得自动 Yes（哪怕其它证据齐全）
    if cst == "WARNING" and verdict is True:
        notes.append("条款清晰性为WARNING，步骤1不得自动通过，请人工复核")
        verdict = None

    return {
        "verdict": verdict,
        "verdict_label": _yn(verdict),
        "sub_checks": sub,
        "gaps": gaps,
        "notes": notes,
        "source": "contract_terms+presence+text",
    }


def assert_step21_revenue_amount(
    *,
    docs: list[dict[str, Any]],
    amount: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """2.1：收入金额是否准确（以计价重算为主；多履约分摊另说明）。"""
    by = _by_type(docs)
    contract = by.get("contract")
    blob = _raw_blob(docs)
    st = _amount_status(amount)
    gaps: list[str] = []
    notes: list[str] = []

    multi_po = bool(
        re.search(r"(多项履约|多个履约义务|分别确认|交易价格分配|allocation)", blob, re.I)
    ) or _filled(_f(contract, "performanceObligations"))
    if multi_po:
        notes.append("可能存在多履约义务：系统已做计价重算，未做交易价格分摊测试")

    if st == "PASS":
        verdict: Optional[bool] = True
        if multi_po:
            notes.append("2.1 建议值=是（计价层面）；分摊需人工确认")
    elif st == "FAIL":
        verdict = False
        gaps.append(str((amount or {}).get("human_readable_summary") or "金额准确性未通过"))
    elif st == "WARNING":
        verdict = None
        gaps.append(str((amount or {}).get("human_readable_summary") or "金额需关注"))
    else:
        verdict = None
        gaps.append("未运行金额准确性测试")

    ar = (amount or {}).get("accuracy_report") or {}
    at = ar.get("amount_test") or {}
    return {
        "verdict": verdict,
        "verdict_label": _yn(verdict),
        "sub_checks": {
            "amount_status": st,
            "pricing_recalc_done": st in {"PASS", "FAIL", "WARNING"},
            "allocation_tested": False,
            "multi_performance_obligation_hint": multi_po,
            "issue_type": at.get("issue_type"),
        },
        "gaps": gaps,
        "notes": notes,
        "source": "amount_test",
    }


def assert_step22_control_transferred(
    *,
    docs: list[dict[str, Any]],
    three_way: Optional[dict[str, Any]],
    contract_res: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """2.2：控制权是否已转移（交付/签收证据 + 三单/截止）。"""
    by = _by_type(docs)
    receipt = by.get("receipt") or by.get("delivery")
    contract = by.get("contract")
    order = by.get("order")
    m_st, c_st = _three_way_pieces(three_way)
    gaps: list[str] = []
    notes: list[str] = []

    has_receipt = bool(by.get("receipt") or by.get("delivery"))
    receipt_date = _f(
        receipt, "acceptanceDate", "deliveryDate", "documentDate", "receiptDate"
    )
    control_terms = _f(contract, "controlTransferTerms", "transportTerms") or _f(
        order, "controlTransferTerms", "transportTerms"
    )

    # 控制权条款冲突（来自条款测试）
    report = (contract_res or {}).get("clarity_report") or (contract_res or {}).get("report") or {}
    issues = ((report.get("test_result") or {}).get("issues")) or []
    control_conflict = any(
        "CONTROL_TRANSFER" in str(i.get("issue_code") or "")
        or str(i.get("dimension") or "") == "运输及控制权转移"
        for i in issues
    )

    sub = {
        "delivery_evidence_present": has_receipt,
        "receipt_date_present": _filled(receipt_date),
        "control_terms_present": _filled(control_terms),
        "control_terms_conflict": control_conflict,
        "three_way_status": m_st,
        "cutoff_status": c_st,
    }

    if not has_receipt:
        gaps.append("缺发货/签收/验收单据")
    if not _filled(receipt_date):
        gaps.append("缺签收/验收/发货日期")
    if not _filled(control_terms):
        notes.append("未摘录控制权/运输条款（不单独否定）")
    if control_conflict:
        gaps.append("控制权转移条款冲突/不唯一")
    if m_st is None and c_st is None:
        gaps.append("未运行三单/截止测试")
    if m_st == "FAIL":
        gaps.append("三单匹配未通过")
    if c_st == "FAIL":
        gaps.append("截止性未通过（控制权转移与入账跨会计期间）")

    # Yes：有签收证据+日期，且三单/截止未 FAIL，截止优先 PASS 或三单 PASS
    if control_conflict or not has_receipt or not _filled(receipt_date):
        verdict: Optional[bool] = False if (control_conflict or not has_receipt) else None
        if not has_receipt:
            verdict = False
        elif not _filled(receipt_date):
            verdict = None
    elif m_st == "FAIL" or c_st == "FAIL":
        verdict = False
    elif c_st == "PASS" or (m_st == "PASS" and _filled(receipt_date)):
        verdict = True
        if c_st == "WARNING":
            notes.append("截止性WARNING：控制权日与入账日偏差需关注")
            verdict = None
        if m_st == "WARNING":
            notes.append("三单WARNING：数量/金额勾稽需关注")
            if verdict is True:
                verdict = None
    else:
        verdict = None

    return {
        "verdict": verdict,
        "verdict_label": _yn(verdict),
        "sub_checks": sub,
        "gaps": gaps,
        "notes": notes,
        "source": "three_way+cutoff+receipt_presence",
    }


def build_gospd_assertions(
    *,
    docs: list[dict[str, Any]],
    job: dict[str, Any],
    chain_id: str = "",
    apply_job_tests: bool = True,
) -> dict[str, Any]:
    """针对一笔业务链生成三列结论。优先使用 gospd_sample_results 中的分笔测试。"""
    per = {}
    raw_map = job.get("gospd_sample_results")
    if isinstance(raw_map, dict) and chain_id:
        per = raw_map.get(chain_id) or {}

    # 分笔完整测试结果优先
    contract_res = (
        per.get("contract_terms")
        if isinstance(per.get("contract_terms"), dict)
        else None
    )
    amount = per.get("amount_test") if isinstance(per.get("amount_test"), dict) else None
    three_way = per.get("three_way") if isinstance(per.get("three_way"), dict) else None

    has_per_tests = bool(contract_res or amount or three_way)

    if not has_per_tests and apply_job_tests:
        contract_res = (
            job.get("contract_terms")
            if isinstance(job.get("contract_terms"), dict)
            else None
        )
        amount = (
            job.get("amount_test") if isinstance(job.get("amount_test"), dict) else None
        )
        three_way = (
            job.get("three_way") if isinstance(job.get("three_way"), dict) else None
        )

    if not has_per_tests and not apply_job_tests:
        from src.audit.workpaper_notes import attach_workpaper_notes

        return attach_workpaper_notes(
            {
                "step1": {
                    "verdict": None,
                    "verdict_label": "",
                    "gaps": ["本行尚未单独跑测"],
                    "notes": [],
                    "sub_checks": {},
                },
                "step21": {
                    "verdict": None,
                    "verdict_label": "",
                    "gaps": ["本行尚未单独跑测"],
                    "notes": [],
                    "sub_checks": {},
                },
                "step22": {
                    "verdict": None,
                    "verdict_label": "",
                    "gaps": ["本行尚未单独跑测"],
                    "notes": [],
                    "sub_checks": {},
                },
                "all_ok": None,
                "all_ok_label": "",
                "exception": "本行尚未单独跑测；对该业务执行测试后重新导出可回填步骤结论",
            },
            job=job,
            chain_id=chain_id,
            empty_verdict_labels=["步骤1", "2.1", "2.2"],
        )

    s1 = assert_step1_enforceable(docs=docs, contract_res=contract_res)
    s21 = assert_step21_revenue_amount(docs=docs, amount=amount)
    s22 = assert_step22_control_transferred(
        docs=docs, three_way=three_way, contract_res=contract_res
    )
    if has_per_tests:
        for block in (s1, s21, s22):
            notes = list(block.get("notes") or [])
            notes.append("结论来自分笔独立测试")
            block["notes"] = notes

    verts = [s1.get("verdict"), s21.get("verdict"), s22.get("verdict")]
    if any(v is False for v in verts):
        all_ok: Optional[bool] = False
    elif all(v is True for v in verts):
        all_ok = True
    else:
        all_ok = None

    exc_parts: list[str] = []
    if per.get("exception"):
        exc_parts.append(str(per.get("exception")))
    for block, title in ((s1, "步骤1"), (s21, "2.1"), (s22, "2.2")):
        for g in block.get("gaps") or []:
            exc_parts.append(f"{title}:{g}")
        for n in block.get("notes") or []:
            if "分摊" in n or "WARNING" in n or "弱通过" in n or "人工" in n:
                exc_parts.append(f"{title}:{n}")

    empty_labels = []
    for block, title in ((s1, "步骤1"), (s21, "2.1"), (s22, "2.2")):
        if block.get("verdict") is None:
            empty_labels.append(title)

    from src.audit.workpaper_notes import attach_workpaper_notes

    return attach_workpaper_notes(
        {
            "step1": s1,
            "step21": s21,
            "step22": s22,
            "all_ok": all_ok,
            "all_ok_label": _yn(all_ok),
            "exception": "；".join(exc_parts),
        },
        job=job,
        chain_id=chain_id,
        contract_res=contract_res if isinstance(contract_res, dict) else None,
        amount=amount if isinstance(amount, dict) else None,
        empty_verdict_labels=empty_labels,
    )

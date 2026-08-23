"""GOSPD01010.3 交易价格抽凭断言。

程序步骤：获取管理层对合同交易价格的计算，检查合同及其他相关文件，
确定是否已适当确定交易价格。

结论列：
- K 合同交易价格是否需要计算？ → YES 是 / NO 否
- M 是否已适当确定交易价格？ → 「 YES 是」/ 模板长 NO 文案（对齐下拉）
- H 其他相关文件 → Applicable 适用 / Not applicable 不适用
- N 异常说明
"""

from __future__ import annotations

from typing import Any, Optional

from src.audit.gospd01010_2_assertions import assert_other_files_for_price
from src.audit.workpaper_notes import attach_workpaper_notes

# 与模板 W15/W16、W20/W21 一字不差（含 W20 前导空格）
LABEL_K_YES = "YES 是"
LABEL_K_NO = "NO 否"
LABEL_M_YES = " YES 是"
LABEL_M_NO = (
    "No Document the details of exception identified and further testing steps."
    "否,记录异常的详细信息和进一步测试"
)

_CONSIDERATION_CODES = frozenset(
    {
        "CONSIDERATION_FORMULA_AMBIGUOUS",
        "VARIABLE_CONSIDERATION_UNRESOLVED",
        "REBATE_TERM_AMBIGUOUS",
        "UNILATERAL_PRICE_ADJUSTMENT",
    }
)


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


def _issue_codes(contract_res: Optional[dict[str, Any]]) -> list[str]:
    if not isinstance(contract_res, dict):
        return []
    codes: list[str] = []
    extracted = contract_res.get("extracted") or {}
    if isinstance(extracted, dict):
        for x in extracted.get("issue_codes") or []:
            if x:
                codes.append(str(x))
    for it in contract_res.get("checks") or []:
        if isinstance(it, dict) and it.get("issue_code"):
            codes.append(str(it["issue_code"]))
    seen: set[str] = set()
    out: list[str] = []
    for c in codes:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _dim_status(contract_res: Optional[dict[str, Any]], dim: str) -> Optional[str]:
    if not isinstance(contract_res, dict):
        return None
    for src in (
        (contract_res.get("extracted") or {}),
        ((contract_res.get("clarity_report") or {}).get("extracted") or {}),
    ):
        if isinstance(src, dict):
            st = (src.get("dimension_statuses") or {}).get(dim)
            if st:
                return str(st).strip().upper()
    return None


def assert_needs_price_calculation(
    *,
    docs_by_type: dict[str, dict[str, Any]],
    contract_res: Optional[dict[str, Any]],
    amount: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """K：合同交易价格是否需要计算。"""
    from src.models.field_values import rule_readable_fields

    reasons: list[str] = []
    codes = _issue_codes(contract_res)
    cons_codes = [c for c in codes if c in _CONSIDERATION_CODES]

    for dtype in ("contract", "order", "invoice"):
        doc = docs_by_type.get(dtype)
        if not doc:
            continue
        fields = rule_readable_fields(doc)
        for key in ("discountRate", "discount", "rebate", "variableConsideration"):
            if fields.get(key) not in (None, "", 0, "0", "0%"):
                reasons.append(f"{dtype}.{key}={fields.get(key)}")

    ar = (amount or {}).get("accuracy_report") if isinstance(amount, dict) else None
    if isinstance(ar, dict):
        sv = ar.get("source_values") or {}
        if float(sv.get("discount_rate") or 0) > 0:
            reasons.append(f"金额测试折扣率={sv.get('discount_rate')}")
        formula = str((ar.get("recalculation") or {}).get("formula") or "")
        if "折扣" in formula or "discount" in formula.lower():
            reasons.append("重算含折扣")

    if cons_codes:
        reasons.append("交易对价问题码:" + "、".join(cons_codes))

    needs = bool(reasons)
    return {
        "needs_calculation": needs,
        "verdict_label": LABEL_K_YES if needs else LABEL_K_NO,
        "calc_method": (
            "；".join(reasons[:6])
            if needs
            else "固定对价/价税合计直取，无需复杂计算"
        ),
        "reasons": reasons,
    }


def assert_transaction_price_ok(
    *,
    has_contract: bool,
    contract_res: Optional[dict[str, Any]],
    amount: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """M：是否已适当确定交易价格（优先交易对价维度 + 金额测试）。"""
    gaps: list[str] = []
    notes: list[str] = []
    codes = _issue_codes(contract_res)
    cons_dim = _dim_status(contract_res, "交易对价")
    cons_codes = [c for c in codes if c in _CONSIDERATION_CODES]

    amt_status = None
    if isinstance(amount, dict):
        amt_status = _status_bucket(amount.get("status"))
        ar = amount.get("accuracy_report") or {}
        if isinstance(ar, dict):
            at = ar.get("amount_test") or {}
            if isinstance(at, dict) and at.get("test_status"):
                amt_status = _status_bucket(at.get("test_status")) or amt_status

    if not has_contract:
        return {
            "verdict": False,
            "verdict_label": LABEL_M_NO,
            "gaps": ["未取得销售合同，无法评价交易价格是否适当确定"],
            "notes": notes,
            "dimension_status": "MISSING",
        }

    if cons_dim == "AMBIGUOUS" or cons_codes:
        return {
            "verdict": False,
            "verdict_label": LABEL_M_NO,
            "gaps": [
                "交易对价条款不清："
                + ("、".join(cons_codes) if cons_codes else "见合同交易对价维度")
            ],
            "notes": notes,
            "dimension_status": "AMBIGUOUS",
        }

    if amt_status == "FAIL":
        return {
            "verdict": False,
            "verdict_label": LABEL_M_NO,
            "gaps": ["金额准确性测试未通过，交易价格可能未适当确定"],
            "notes": notes,
            "dimension_status": cons_dim or "CLEAR",
        }

    if amt_status == "WARNING":
        return {
            "verdict": None,
            "verdict_label": "",
            "gaps": ["金额测试 WARNING，交易价格结论待复核"],
            "notes": notes,
            "dimension_status": cons_dim,
        }

    if cons_dim == "CLEAR" or (cons_dim is None and amt_status == "PASS"):
        # 对价清晰，或无维度字段但金额 PASS
        if cons_dim is None and amt_status not in {"PASS", None}:
            return {
                "verdict": None,
                "verdict_label": "",
                "gaps": ["缺少交易对价维度状态且金额未明确通过"],
                "notes": notes,
                "dimension_status": None,
            }
        # 无金额结果时：对价 CLEAR 也可 Yes（本目标必做金额，通常有结果）
        if cons_dim == "CLEAR" and amt_status in {None, "PASS", "SKIPPED"}:
            return {
                "verdict": True,
                "verdict_label": LABEL_M_YES,
                "gaps": [],
                "notes": notes
                + (
                    ["金额未测或 SKIPPED，按交易对价 CLEAR 认定"]
                    if amt_status in {None, "SKIPPED"}
                    else []
                ),
                "dimension_status": "CLEAR",
            }
        if amt_status == "PASS":
            return {
                "verdict": True,
                "verdict_label": LABEL_M_YES,
                "gaps": [],
                "notes": notes,
                "dimension_status": cons_dim or "CLEAR",
            }

    return {
        "verdict": None,
        "verdict_label": "",
        "gaps": ["交易价格结论证据不足，待审计师判断"],
        "notes": notes,
        "dimension_status": cons_dim,
    }


def build_gospd01010_3_assertions(
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
        (contract.get("raw_text") or "").strip() or (contract.get("fields") or {})
    )

    contract_res = None
    amount = None
    if apply_job_tests:
        samples = (
            job.get("gospd_sample_results")
            if isinstance(job.get("gospd_sample_results"), dict)
            else {}
        )
        per = samples.get(chain_id) or {} if chain_id else {}
        if isinstance(per, dict):
            contract_res = per.get("contract_terms")
            amount = per.get("amount_test")
        if not isinstance(contract_res, dict):
            contract_res = (
                job.get("contract_terms")
                if isinstance(job.get("contract_terms"), dict)
                else None
            )
        if not isinstance(amount, dict):
            amount = (
                job.get("amount_test") if isinstance(job.get("amount_test"), dict) else None
            )

    other = assert_other_files_for_price(docs_by_type=by)
    needs = assert_needs_price_calculation(
        docs_by_type=by, contract_res=contract_res, amount=amount
    )
    price = assert_transaction_price_ok(
        has_contract=has_contract, contract_res=contract_res, amount=amount
    )

    gaps = list(price.get("gaps") or [])
    exception = "；".join(g for g in gaps if g)

    out: dict[str, Any] = {
        "other_files": other,
        "needs_calc": needs,
        "price_ok": price,
        "other_applicable": other.get("applicable_label") or "Not applicable 不适用",
        "other_file_type": other.get("file_type") or "",
        "other_file_index": other.get("file_index") or "",
        "needs_calc_label": needs.get("verdict_label") or LABEL_K_NO,
        "calc_method": needs.get("calc_method") or "",
        "price_ok_label": price.get("verdict_label") or "",
        "exception": exception,
        "all_ok": price.get("verdict") is True,
    }
    return attach_workpaper_notes(
        out,
        job=job,
        chain_id=chain_id,
        contract_res=contract_res if isinstance(contract_res, dict) else None,
        amount=amount if isinstance(amount, dict) else None,
        empty_verdict_labels=(
            ["交易价格适当性"] if not (price.get("verdict_label") or "").strip() else None
        ),
    )

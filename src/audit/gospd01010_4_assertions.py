"""GOSPD01010.4 交易价格分摊（SSP）抽凭断言。

程序（模板步骤2 + 底稿须知）：
- 获取分摊计算，核对单独售价(SSP)至相关文件
- 评价折扣/可变对价是否满足「仅分摊至一项或多项但非全部履约义务」标准
- 重算分摊并评估收入是否准确确认

系统落地口径（无管理层分摊底稿时）：
- 默认按「单一履约义务 / 不满足专项分摊标准」处理
- 交易价格适当性复用对价维度 + 金额测试
- 不虚构多履约义务拆分行
"""

from __future__ import annotations

from typing import Any, Optional

from src.audit.gospd01010_2_assertions import assert_other_files_for_price
from src.audit.gospd01010_3_assertions import assert_transaction_price_ok
from src.audit.workpaper_notes import attach_workpaper_notes

# 与「底稿须知」枚举一字不差（含模板拼写 spcify）
LABEL_O_NOT_MET = "Criteria not met不满足标准"
LABEL_O_MET = "Criteria met, please spcify in column X.满足标准，请在X列详述"
LABEL_P_APP = "Applicable 适用"
LABEL_P_NA = "Not Applicable 不适用"
LABEL_V_YES = "YES 是"
LABEL_V_NO = "No 否"
LABEL_V_NA = "Not applicable 不适用"
LABEL_W_YES = "YES 是"
LABEL_W_NO = (
    "No Document the details of exception identified and further testing steps."
    "否,记录异常的详细信息和进一步测试"
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


def _looks_multi_po(docs_by_type: dict[str, dict[str, Any]]) -> bool:
    """粗检是否可能存在多项履约义务（仅提示，不自动拆行）。"""
    contract = docs_by_type.get("contract") or {}
    blob = " ".join(
        [
            str(contract.get("raw_text") or ""),
            str((contract.get("fields") or {}).get("performanceObligations") or ""),
            str((contract.get("fields") or {}).get("controlTransferTerms") or ""),
        ]
    )
    goods = any(k in blob for k in ("销售", "交付", "商品", "货物", "产品"))
    services = any(
        k in blob for k in ("安装", "调试", "培训", "技术支持", "服务", "驻场", "维保")
    )
    return goods and services


def assert_discount_allocation_criteria(
    *,
    docs_by_type: dict[str, dict[str, Any]],
    contract_res: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """O 列：是否满足折扣/可变对价仅分摊至部分履约义务的标准。

    无管理层分摊证据 / 无多履约义务 → 不满足标准（按比例或单一义务）。
    """
    codes = []
    if isinstance(contract_res, dict):
        extracted = contract_res.get("extracted") or {}
        if isinstance(extracted, dict):
            codes = [str(x) for x in (extracted.get("issue_codes") or []) if x]

    multi = _looks_multi_po(docs_by_type)
    has_variable = any(
        c in codes
        for c in (
            "VARIABLE_CONSIDERATION_UNRESOLVED",
            "REBATE_TERM_AMBIGUOUS",
            "CONSIDERATION_FORMULA_AMBIGUOUS",
        )
    )

    # 系统无法核验 CAS14 三项「经常单独销售」证据 → 默认不满足专项分摊标准
    met = False
    notes = [
        "未取得管理层「折扣仅归属部分履约义务」的三项证据，默认不满足专项分摊标准"
    ]
    if multi:
        notes.append("合同表述可能含商品+服务多项义务，建议取得分摊计算底稿后人工确认")
    if has_variable:
        notes.append("存在可变对价/返利相关条款问题码，分摊需重点复核")

    return {
        "criteria_met": met,
        "verdict_label": LABEL_O_MET if met else LABEL_O_NOT_MET,
        "notes": notes,
        "multi_po_hint": multi,
    }


def assert_all_steps_ok(
    *,
    price_verdict: Optional[bool],
    amount_status: Optional[str],
    gaps: list[str],
) -> dict[str, Any]:
    """W 列：执行的所有测试步骤都没发现异常？"""
    if gaps or price_verdict is False or amount_status == "FAIL":
        return {
            "verdict": False,
            "verdict_label": LABEL_W_NO,
        }
    if price_verdict is True and amount_status in {"PASS", None, "SKIPPED"}:
        return {"verdict": True, "verdict_label": LABEL_W_YES}
    if price_verdict is None or amount_status == "WARNING":
        return {"verdict": None, "verdict_label": ""}
    return {"verdict": None, "verdict_label": ""}


def build_gospd01010_4_assertions(
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
    # 01010.4 的 P 列枚举是 Not Applicable（A 大写）
    if other.get("applicable_label") == "Applicable 适用":
        other_label = LABEL_P_APP
    else:
        other_label = LABEL_P_NA

    criteria = assert_discount_allocation_criteria(
        docs_by_type=by, contract_res=contract_res
    )
    price = assert_transaction_price_ok(
        has_contract=has_contract, contract_res=contract_res, amount=amount
    )

    # V 列：YES/No/Not applicable（01010.3 的 M 带前导空格，此处按须知用 YES 是）
    if price.get("verdict") is True:
        v_label = LABEL_V_YES
    elif price.get("verdict") is False:
        v_label = LABEL_V_NO
    else:
        v_label = ""

    amt_status = _status_bucket((amount or {}).get("status")) if isinstance(amount, dict) else None
    gaps = list(price.get("gaps") or []) + list(criteria.get("notes") or [])
    # 缺分摊底稿不自动构成 No，但写入异常/旁注
    gaps_for_w = list(price.get("gaps") or [])
    all_ok = assert_all_steps_ok(
        price_verdict=price.get("verdict"),
        amount_status=amt_status,
        gaps=gaps_for_w,
    )

    exception = "；".join(
        g for g in (list(price.get("gaps") or []) + criteria.get("notes", [])[:2]) if g
    )

    out: dict[str, Any] = {
        "other_files": other,
        "criteria": criteria,
        "price_ok": price,
        "all_ok": all_ok,
        "criteria_label": criteria.get("verdict_label") or LABEL_O_NOT_MET,
        "other_applicable": other_label,
        "other_file_type": other.get("file_type") or "",
        "other_file_index": other.get("file_index") or "",
        "price_ok_label": v_label,
        "all_ok_label": all_ok.get("verdict_label") or "",
        "exception": exception,
        "allocation_basis": "单独售价比例（单一履约义务时基础=1）",
        "comment": (
            "未取得管理层交易价格分摊计算底稿；本行按单一履约义务/不满足专项分摊标准处理。"
            "若存在多项义务，请补分摊表后人工改填 O/I/J/S/T。"
        ),
    }
    return attach_workpaper_notes(
        out,
        job=job,
        chain_id=chain_id,
        contract_res=contract_res if isinstance(contract_res, dict) else None,
        amount=amount if isinstance(amount, dict) else None,
        empty_verdict_labels=(
            ["交易价格适当性"] if not v_label else None
        ),
        extra_observations=criteria.get("notes"),
    )

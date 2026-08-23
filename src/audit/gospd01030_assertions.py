"""GOSPD01030 销售截止（期后）断言。

程序步骤（模板）：
1. 获取收入交易文件，确定控制权转移时点
2. 评估收入是否在控制权转移时确认、记入正确会计期间
3. 确定相关应收账款是否计入正确会计期间

底稿结论列：
- V 销售收入是否记录在正确的会计期间？（模板公式列，写入器不得覆盖）
- W 执行的所有测试步骤都没有发现异常？（须写数据验证允许值）
- X 异常的详细信息和进一步测试

标签口径（与改进模板 / 填制指引一致）：
- 期间独立判断用语对齐 V 公式选项：YES 是 / No 否
- W 列「否」在当前模板常为长文案，由 filler 读取数据验证后写入；
  本模块 all_ok_label 先给出语义标签，filler 再映射到精确允许值。
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional

# 与 V 列公式选项一致（独立判断 / 日志比对用）
LABEL_PERIOD_YES = "YES 是"
LABEL_PERIOD_NO = "No 否"
# W 列语义短标签；精确 DV 长文案由 filler 读取模板
LABEL_W_YES = "YES 是"
LABEL_W_NO = "No 否"


def _yn(v: Optional[bool]) -> str:
    """期间独立判断标签（对齐 V 公式选项）。"""
    if v is True:
        return LABEL_PERIOD_YES
    if v is False:
        return LABEL_PERIOD_NO
    return ""


def _yn_w(v: Optional[bool]) -> str:
    """W 列语义标签（filler 再映射到数据验证精确值）。"""
    if v is True:
        return LABEL_W_YES
    if v is False:
        return LABEL_W_NO
    return ""


def _parse_date(raw: Any) -> Optional[date]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    s = str(raw).strip()
    if not s:
        return None
    s = s.replace("年", "-").replace("月", "-").replace("日", "")
    s = s.replace("/", "-").replace(".", "-")
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if not m:
        # 03/01/2025 style already normalized above; try DD-MM-YYYY
        m2 = re.match(r"^(\d{1,2})-(\d{1,2})-(\d{4})$", s)
        if m2:
            d, mo, y = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
            try:
                return date(y, mo, d)
            except ValueError:
                return None
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def resolve_period_end(job: dict[str, Any]) -> Optional[date]:
    """解析报告期末日。未配置时返回 None（禁止静默默认某年 12-31）。"""
    raw = (
        job.get("period_end")
        or (job.get("plan") or {}).get("period_end")
        or job.get("cutoff_period_end")
    )
    return _parse_date(raw)


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


def _f(doc: Optional[dict[str, Any]], *keys: str) -> Any:
    if not doc:
        return None
    from src.models.field_values import rule_readable_fields

    fields = rule_readable_fields(doc)
    for k in keys:
        if fields.get(k) not in (None, ""):
            return fields.get(k)
        if doc.get(k) not in (None, ""):
            return doc.get(k)
    return None


def _by_type(docs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for d in docs or []:
        t = str(d.get("doc_type") or "")
        if t and t not in out:
            out[t] = d
    return out


def _three_way_cutoff_status(three_way: Optional[dict[str, Any]]) -> tuple[Optional[str], Optional[str]]:
    if not isinstance(three_way, dict):
        return None, None
    match = three_way.get("match_result") or {}
    if hasattr(match, "model_dump"):
        match = match.model_dump()
    cutoff = three_way.get("cutoff_result") or {}
    if hasattr(cutoff, "model_dump"):
        cutoff = cutoff.model_dump()
    # 三单结论只看三单状态，禁止用综合 overall_status（其中含截止失败）。
    m = _status_bucket(
        three_way.get("three_way_status")
        or (match.get("overall_status") if isinstance(match, dict) else None)
    )
    c = _status_bucket(
        three_way.get("cutoff_status")
        or (cutoff.get("测试状态") if isinstance(cutoff, dict) else None)
        or (cutoff.get("test_status") if isinstance(cutoff, dict) else None)
        or three_way.get("cutoff_test_status")
    )
    return m, c


def assert_correct_accounting_period(
    *,
    posting_date: Any,
    control_date: Any,
    period_end: Optional[date],
    cutoff_status: Optional[str],
) -> dict[str, Any]:
    """销售收入是否记录在正确的会计期间。

    期后样本口径：
    - 控制权日 ≤ 期末 且 过账日 > 期末 → No（应记上期）
    - 控制权日 > 期末 且 过账日 > 期末 → Yes（期后销售记期后）
    - 截止性 FAIL → No
    - 证据不足 → 空（待复核）
    """
    gaps: list[str] = []
    notes: list[str] = []
    d_post = _parse_date(posting_date)
    d_ctrl = _parse_date(control_date)
    pe = period_end

    if cutoff_status == "FAIL":
        return {
            "verdict": False,
            "verdict_label": _yn(False),
            "gaps": ["截止性未通过（入账与控制权转移时点不合理）"],
            "notes": notes,
            "evidence_status": "TESTED",
            "posting_date": str(d_post or ""),
            "control_date": str(d_ctrl or ""),
            "period_end": str(pe or ""),
        }

    if not d_post or not d_ctrl:
        if not d_post:
            gaps.append("缺过账/入账日期")
        if not d_ctrl:
            gaps.append("缺签收/控制权转移日期")
        return {
            "verdict": None,
            "verdict_label": _yn(None),
            "gaps": gaps,
            "notes": notes,
            "evidence_status": "INSUFFICIENT_EVIDENCE",
            "posting_date": str(d_post or ""),
            "control_date": str(d_ctrl or ""),
            "period_end": str(pe or ""),
        }

    if pe is None:
        # 无期末日时不得假装已完成期间判断
        return {
            "verdict": None,
            "verdict_label": _yn(None),
            "gaps": ["未配置报告期末日（period_end），期间结论 NOT_TESTED"],
            "notes": notes
            + (
                [f"截止性状态={cutoff_status}"]
                if cutoff_status
                else ["未配置期末且无截止结论"]
            ),
            "evidence_status": "NOT_TESTED",
            "posting_date": str(d_post),
            "control_date": str(d_ctrl),
            "period_end": "",
        }

    # 经典期后错报：控制权在期内，入账在期后
    if d_ctrl <= pe < d_post:
        return {
            "verdict": False,
            "verdict_label": _yn(False),
            "gaps": [
                f"控制权转移日 {d_ctrl} ≤ 期末 {pe}，但过账日 {d_post} 在期后，收入应记入上期"
            ],
            "notes": notes,
            "posting_date": str(d_post),
            "control_date": str(d_ctrl),
            "period_end": str(pe),
        }

    # 期后销售、期后入账
    if d_ctrl > pe and d_post > pe:
        verdict = True
        if cutoff_status == "WARNING":
            verdict = None
            notes.append("期间边界一致但截止性 WARNING，请人工复核")
        return {
            "verdict": verdict,
            "verdict_label": _yn(verdict),
            "gaps": gaps,
            "notes": notes,
            "posting_date": str(d_post),
            "control_date": str(d_ctrl),
            "period_end": str(pe),
        }

    # 均在期内：对本表「期后」样本非典型，若截止 PASS 仍可 Yes
    if d_post <= pe and d_ctrl <= pe:
        notes.append("过账与控制权均在期内，非典型期后样本")
        if cutoff_status == "PASS":
            return {
                "verdict": True,
                "verdict_label": _yn(True),
                "gaps": gaps,
                "notes": notes,
                "posting_date": str(d_post),
                "control_date": str(d_ctrl),
                "period_end": str(pe),
            }
        return {
            "verdict": None,
            "verdict_label": _yn(None),
            "gaps": gaps or ["期内样本且截止结论不明确"],
            "notes": notes,
            "posting_date": str(d_post),
            "control_date": str(d_ctrl),
            "period_end": str(pe),
        }

    # 控制权在期后、入账在期内 → 提前确认
    if d_post <= pe < d_ctrl:
        return {
            "verdict": False,
            "verdict_label": _yn(False),
            "gaps": [
                f"过账日 {d_post} ≤ 期末 {pe}，但控制权转移日 {d_ctrl} 在期后，存在提前确认风险"
            ],
            "notes": notes,
            "posting_date": str(d_post),
            "control_date": str(d_ctrl),
            "period_end": str(pe),
        }

    return {
        "verdict": None,
        "verdict_label": _yn(None),
        "gaps": ["期间关系无法归类，待复核"],
        "notes": notes,
        "posting_date": str(d_post),
        "control_date": str(d_ctrl),
        "period_end": str(pe or ""),
    }


def assert_ar_correct_period(
    *,
    posting_date: Any,
    control_date: Any,
    period_end: Optional[date],
    cutoff_status: Optional[str],
    revenue_period_verdict: Optional[bool] = None,
) -> dict[str, Any]:
    """模板步骤3：相关应收账款是否计入正确会计期间。

    当前证据模型通常只有发票/账过账日，与收入期间共用同一入账日：
    - 收入期间已明确否 → 应收亦否；
    - 收入期间已明确是 → 应收亦是（注明同源）；
    - 否则独立按控制权日/过账日/期末重判。
    """
    notes: list[str] = []
    if revenue_period_verdict is False:
        return {
            "verdict": False,
            "verdict_label": _yn(False),
            "gaps": ["收入未记入正确会计期间，相关应收账款期间一并存疑"],
            "notes": notes,
            "evidence_status": "TESTED",
            "posting_date": str(_parse_date(posting_date) or ""),
            "control_date": str(_parse_date(control_date) or ""),
            "period_end": str(period_end or ""),
        }
    if revenue_period_verdict is True:
        return {
            "verdict": True,
            "verdict_label": _yn(True),
            "gaps": [],
            "notes": ["与收入期间结论一致（共用发票/账过账日；非独立函证）"],
            "evidence_status": "TESTED",
            "posting_date": str(_parse_date(posting_date) or ""),
            "control_date": str(_parse_date(control_date) or ""),
            "period_end": str(period_end or ""),
        }

    base = assert_correct_accounting_period(
        posting_date=posting_date,
        control_date=control_date,
        period_end=period_end,
        cutoff_status=cutoff_status,
    )
    gaps = [f"应收：{g}" for g in (base.get("gaps") or [])]
    notes = list(base.get("notes") or [])
    notes.append("应收账款期间按控制权日/过账日/期末独立复核")
    return {
        "verdict": base.get("verdict"),
        "verdict_label": base.get("verdict_label") or "",
        "gaps": gaps,
        "notes": notes,
        "evidence_status": base.get("evidence_status") or "",
        "posting_date": base.get("posting_date") or "",
        "control_date": base.get("control_date") or "",
        "period_end": base.get("period_end") or "",
    }


def build_gospd01030_assertions(
    *,
    docs: list[dict[str, Any]],
    job: dict[str, Any],
    three_way: Optional[dict[str, Any]] = None,
    chain_id: str = "",
    control_date_override: Optional[str] = None,
) -> dict[str, Any]:
    by = _by_type(docs)
    invoice = by.get("invoice")
    receipt = by.get("receipt") or by.get("delivery")
    order = by.get("order")
    contract = by.get("contract")

    posting = (
        (invoice or {}).get("ledger_posting_date")
        or _f(invoice, "postingDate")
    )
    # 外销装船日等可由 filler/贸易模式桥覆盖；显式传空串表示「不得用签收日冒充」
    if control_date_override is not None:
        control = str(control_date_override).strip() or None
    else:
        control = _f(
            receipt, "acceptanceDate", "deliveryDate", "documentDate", "receiptDate"
        ) or _f(by.get("delivery"), "deliveryDate", "documentDate")

    m_st, c_st = _three_way_cutoff_status(three_way)
    pe = resolve_period_end(job)
    period = assert_correct_accounting_period(
        posting_date=posting,
        control_date=control,
        period_end=pe,
        cutoff_status=c_st,
    )
    ar_period = assert_ar_correct_period(
        posting_date=posting,
        control_date=control,
        period_end=pe,
        cutoff_status=c_st,
        revenue_period_verdict=period.get("verdict"),  # type: ignore[arg-type]
    )

    gaps = list(period.get("gaps") or [])
    notes = list(period.get("notes") or [])
    gaps.extend(g for g in (ar_period.get("gaps") or []) if g not in gaps)
    notes.extend(n for n in (ar_period.get("notes") or []) if n not in notes)

    if not (by.get("receipt") or by.get("delivery")):
        gaps.append("缺发货/签收/验收单据（步骤1证据不足）")
    if not invoice and not posting:
        gaps.append("缺发票/过账日，无法评价应收账款期间（步骤3）")
    if not contract and not order:
        notes.append("未取得合同/订单；运输条款可能不完整")
    if m_st is None and c_st is None:
        gaps.append("未运行三单匹配/截止测试")
    if m_st == "FAIL":
        gaps.append("三单匹配未通过")

    period_ok = period.get("verdict")
    ar_ok = ar_period.get("verdict")
    no_exception = (
        period_ok is True
        and ar_ok is not False
        and m_st != "FAIL"
        and c_st != "FAIL"
        and not any("缺" in g for g in gaps)
    )
    if m_st == "WARNING" or c_st == "WARNING":
        no_exception = False
        notes.append("三单或截止存在 WARNING")

    if period_ok is False or ar_ok is False:
        all_ok: Optional[bool] = False
    elif no_exception:
        all_ok = True
    else:
        all_ok = None

    exception_bits = [g for g in gaps if g]
    exception_bits.extend(n for n in notes if "WARNING" in n or "风险" in n)

    empty_labels = []
    if period_ok is None:
        empty_labels.append("正确会计期间")
    if ar_ok is None:
        empty_labels.append("应收账款期间")
    if all_ok is None:
        empty_labels.append("有无异常")

    evidence_status = str((period or {}).get("evidence_status") or "")
    if not evidence_status:
        if any("缺" in g for g in gaps) or m_st is None:
            evidence_status = "INSUFFICIENT_EVIDENCE"
        elif all_ok is None and period_ok is None:
            evidence_status = "NOT_TESTED"
        else:
            evidence_status = "TESTED"

    from src.audit.workpaper_notes import attach_workpaper_notes

    # 优先使用行级 chain_id（filler 传入），否则从单据弱推断
    chain_hint = str(chain_id or "").strip()
    if not chain_hint:
        for d in docs or []:
            fields = d.get("fields") or {}
            for k in ("orderNo", "salesOrderNo", "documentNo", "contractNo"):
                if fields.get(k):
                    chain_hint = str(fields.get(k))
                    break
            if chain_hint:
                break

    # 分笔测试优先
    samples = job.get("gospd_sample_results") if isinstance(job.get("gospd_sample_results"), dict) else {}
    per = samples.get(chain_hint) or {} if chain_hint else {}
    contract_res = (
        per.get("contract_terms")
        if isinstance(per.get("contract_terms"), dict)
        else (job.get("contract_terms") if isinstance(job.get("contract_terms"), dict) else None)
    )
    amount = (
        per.get("amount_test")
        if isinstance(per.get("amount_test"), dict)
        else (job.get("amount_test") if isinstance(job.get("amount_test"), dict) else None)
    )

    return attach_workpaper_notes(
        {
            "period": period,
            "period_label": period.get("verdict_label") or "",
            "ar_period": ar_period,
            "ar_period_label": ar_period.get("verdict_label") or "",
            "all_ok": all_ok,
            "all_ok_label": _yn_w(all_ok),
            "exception": "；".join(exception_bits),
            "match_status": m_st,
            "cutoff_status": c_st,
            "gaps": gaps,
            "notes": notes,
            "evidence_status": evidence_status,
            # 禁止空值默认「签收确认」；正式 E13/M 由贸易模式桥或合同字段投影
            "transport": _f(contract, "transportTerms")
            or _f(order, "transportTerms")
            or "",
        },
        job=job,
        chain_id=chain_hint,
        contract_res=contract_res,
        amount=amount,
        empty_verdict_labels=empty_labels,
    )

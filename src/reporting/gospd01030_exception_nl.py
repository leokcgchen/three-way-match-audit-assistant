"""GOSPD01030 异常说明（X 列）自然语言化。

把规则引擎状态码/公式冲突改写成审计师可读的具体差异说明；
不编造数字——只陈述已有证据（数量、金额、日期）。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional


def _parse_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _fmt_date(d: Optional[date]) -> str:
    return d.isoformat() if d else "（缺）"


def _fmt_num(v: Any) -> str:
    if v is None or v == "":
        return "（缺）"
    try:
        f = float(v)
        if abs(f - round(f)) < 1e-9:
            return str(int(round(f)))
        return f"{f:g}"
    except (TypeError, ValueError):
        return str(v)


def _three_way_blob(three_way: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(three_way, dict):
        return {}
    # 新拆分视图优先
    twm = three_way.get("field_consistency") and three_way
    if three_way.get("three_way_match") and isinstance(three_way.get("three_way_match"), dict):
        return three_way
    return three_way


def _explain_three_way_fail(
    three_way: Optional[dict[str, Any]],
    *,
    qty_book: Any,
    qty_doc: Any,
    amt_book: Any,
    invoice_amt: Any,
) -> list[str]:
    tw = three_way if isinstance(three_way, dict) else {}
    twm = tw.get("three_way_match") if isinstance(tw.get("three_way_match"), dict) else tw
    lines: list[str] = []

    summary = str(
        twm.get("summary")
        or tw.get("three_way_summary")
        or tw.get("human_readable_summary")
        or ""
    ).strip()
    # 摘要里若已含数量/金额差异，优先用人话复述关键句
    roles = (
        twm.get("quantity_roles")
        or tw.get("quantity_roles")
        or {}
    )
    if isinstance(roles, dict) and any(roles.get(k) is not None for k in ("ordered_qty", "received_qty", "invoiced_qty")):
        o = roles.get("ordered_qty")
        r = roles.get("received_qty")
        i = roles.get("invoiced_qty")
        bits = []
        if o is not None and r is not None and float(o) != float(r):
            bits.append(f"订单数量 {_fmt_num(o)} 与签收/验收数量 {_fmt_num(r)} 不一致")
        if o is not None and i is not None and float(o) != float(i):
            bits.append(f"订单数量 {_fmt_num(o)} 与发票数量 {_fmt_num(i)} 不一致")
        if r is not None and i is not None and float(r) != float(i):
            bits.append(f"签收/验收数量 {_fmt_num(r)} 与发票数量 {_fmt_num(i)} 不一致")
        if bits:
            lines.append("三单数量勾稽未通过：" + "；".join(bits) + "。")

    if qty_book is not None and qty_doc is not None:
        try:
            if abs(float(qty_book) - float(qty_doc)) > 1e-9:
                lines.append(
                    f"账面/入账数量为 {_fmt_num(qty_book)}，交货/签收数量为 {_fmt_num(qty_doc)}，二者不一致。"
                )
        except (TypeError, ValueError):
            pass

    if amt_book is not None and invoice_amt is not None:
        try:
            if abs(float(amt_book) - float(invoice_amt)) > 0.02:
                lines.append(
                    f"账面金额 {_fmt_num(amt_book)} 与发票金额 {_fmt_num(invoice_amt)} 不一致。"
                )
        except (TypeError, ValueError):
            pass

    fc = twm.get("field_consistency") or tw.get("field_consistency") or {}
    comps = []
    if isinstance(fc, dict):
        comps = fc.get("comparisons") or fc.get("fields") or []
    if isinstance(comps, list):
        for c in comps:
            if not isinstance(c, dict):
                continue
            if c.get("is_consistent") is False or str(c.get("status") or "").upper() == "FAIL":
                name = str(c.get("field_name_cn") or c.get("field_name") or "字段")
                msg = str(c.get("message") or c.get("detail") or "").strip()
                if msg:
                    lines.append(f"三单「{name}」未通过：{msg}")
                else:
                    lines.append(f"三单「{name}」勾稽未通过。")

    if not lines and summary:
        # 去掉emoji/状态前缀，保留可读句
        clean = summary.replace("✅", "").replace("❌", "").replace("⚠️", "").strip()
        if "三单" in clean or "数量" in clean or "金额" in clean or "客户" in clean:
            lines.append(f"三单匹配未通过：{clean}")
        else:
            lines.append(f"三单匹配未通过（{clean}）。")
    if not lines:
        lines.append("三单匹配未通过：订单、签收/验收与发票之间的数量、金额或购方名称存在不一致，请对照原件复核。")
    return lines


def _explain_cutoff_fail(
    *,
    posting_date: Any,
    control_date: Any,
    period_end: Any,
    cutoff_blob: Optional[dict[str, Any]] = None,
) -> list[str]:
    d_post = _parse_date(posting_date)
    d_ctrl = _parse_date(control_date)
    pe = _parse_date(period_end)
    lines: list[str] = []

    cut = cutoff_blob if isinstance(cutoff_blob, dict) else {}
    issue = str(
        cut.get("issue_description")
        or (cut.get("result") or {}).get("问题描述")
        or cut.get("summary")
        or ""
    ).strip()

    if d_ctrl and d_post:
        delta = (d_post - d_ctrl).days
        abs_d = abs(delta)
        lines.append(
            f"控制权转移日（签收/验收或装船）为 {_fmt_date(d_ctrl)}，"
            f"序时账入账日为 {_fmt_date(d_post)}，相差 {abs_d} 天。"
        )
        if pe:
            ctrl_side = "期内" if d_ctrl <= pe else "期后"
            post_side = "期内" if d_post <= pe else "期后"
            lines.append(
                f"相对报告期末 {_fmt_date(pe)}：控制权属{ctrl_side}、入账属{post_side}。"
            )
            if d_ctrl <= pe < d_post:
                lines.append(
                    "不合理之处在于：控制权已在本期转移，收入却记到下期，相当于当期少记收入（延后确认）。"
                )
            elif d_post <= pe < d_ctrl:
                lines.append(
                    "不合理之处在于：控制权尚未转移（或在期后才转移），收入却已记入本期，相当于当期多记收入（提前确认）。"
                )
            elif ctrl_side != post_side:
                lines.append("控制权日与入账日分属报告期末两侧，截止认定存在跨期风险。")
        else:
            if delta > 0:
                lines.append(
                    "入账晚于控制权转移，存在延后确认收入的风险（在未配置报告期末时，先按自然月/日差提示）。"
                )
            elif delta < 0:
                lines.append(
                    "入账早于控制权转移，存在提前确认收入的风险（在未配置报告期末时，先按自然月/日差提示）。"
                )
    elif issue:
        lines.append(f"截止性未通过：{issue}")
    else:
        lines.append("截止性未通过：入账日与控制权转移日关系不合理，请核对签收/验收（或装船）日期与序时账过账日。")
    return lines


def _explain_formula_conflict(conflict: str, *, receipt_date: Any, period_end: Any) -> list[str]:
    text = str(conflict or "")
    if "FORMULA_LOGIC_CONFLICT" not in text:
        return []
    pe = _fmt_date(_parse_date(period_end))
    rd = _fmt_date(_parse_date(receipt_date))
    if "独立期间判断=YES" in text or "独立期间判断=YES 是" in text:
        return [
            f"系统按「入账日相对控制权日/报告期末」判断收入期间为正确（是），"
            f"但底稿 V 列公式按「控制权日({rd})是否晚于报告期末({pe})」另算为否；"
            f"两边口径不一致，故保留公式、本行交审计师复核。"
        ]
    if "独立期间判断=No" in text or "独立期间判断=No 否" in text:
        if "M5" in text or "period_end" in text or "期间截止日" in text:
            return [
                "系统已判断收入未记入正确会计期间（否），但报告期末日（M5）未配置，"
                "无法与底稿 V 列公式对齐；请先填写报告期末后再复核。"
            ]
        return [
            f"系统按入账与控制权日判断收入期间不正确（否），"
            f"但底稿 V 列公式按「控制权日({rd})是否晚于报告期末({pe})」另算为是；"
            f"两边口径不一致，故保留公式、本行交审计师复核。"
        ]
    return ["底稿 V 列公式与系统期间判断不一致，保留公式，本行交审计师复核。"]


def _explain_missing(text: str) -> list[str]:
    raw = str(text or "")
    lines: list[str] = []
    if "carrier_received" in raw or "货交承运人" in raw:
        lines.append(
            "外销业务应以「货交承运人/装船」日期作为控制权证据，当前资料缺少该日期，无法按装船日做截止判断。"
        )
    if "缺少控制权候选事件日期证据" in raw and not lines:
        lines.append("缺少可用于判断控制权转移的关键日期证据（签收、验收或装船日），相关期间结论暂无法确定。")
    if "未配置报告期末日" in raw or "period_end" in raw and "未配置" in raw:
        lines.append("未配置报告期末日，期间相关结论尚未正式测试；请先填写期末日后再看是否跨期。")
    if "运输条款证据不足" in raw:
        lines.append("运输/交付条款证据不足，底稿运输条款列留空（系统不会默认写成「签收确认」）。")
    if "缺发货/签收/验收" in raw:
        lines.append("缺少发货、签收或验收单据，步骤1证据不足。")
    if "缺发票" in raw or "缺过账" in raw:
        lines.append("缺少发票或过账日，无法完整评价应收账款所属期间。")
    return lines


def build_gospd01030_exception_nl(
    *,
    assertions: Optional[dict[str, Any]] = None,
    three_way: Optional[dict[str, Any]] = None,
    qty_book: Any = None,
    qty_doc: Any = None,
    amt_book: Any = None,
    invoice_amt: Any = None,
    posting_date: Any = None,
    receipt_date: Any = None,
    period_end: Any = None,
    formula_conflict: str = "",
    raw_exception: str = "",
    transport: str = "",
) -> str:
    """生成写入 X 列的自然语言说明（多句、中文）。"""
    assertions = assertions or {}
    tw = three_way if isinstance(three_way, dict) else {}
    # 样本级可能拆成 three_way_match / cutoff_test
    if not tw.get("three_way_status") and isinstance(assertions.get("match_status"), str):
        pass

    m_st = str(assertions.get("match_status") or "").upper()
    c_st = str(assertions.get("cutoff_status") or "").upper()
    period = assertions.get("period") if isinstance(assertions.get("period"), dict) else {}
    raw = str(raw_exception or assertions.get("exception") or "")

    cut_blob = tw.get("cutoff_test") if isinstance(tw.get("cutoff_test"), dict) else None
    if not cut_blob and isinstance(tw.get("cutoff_result"), dict):
        cut_blob = {"result": tw.get("cutoff_result"), "summary": tw.get("cutoff_summary")}

    parts: list[str] = []
    parts.extend(_explain_formula_conflict(formula_conflict or raw, receipt_date=receipt_date, period_end=period_end))

    if m_st == "FAIL" or "三单匹配未通过" in raw:
        parts.extend(
            _explain_three_way_fail(
                tw,
                qty_book=qty_book,
                qty_doc=qty_doc,
                amt_book=amt_book,
                invoice_amt=invoice_amt,
            )
        )

    if c_st == "FAIL" or "截止性未通过" in raw or (period.get("verdict") is False and ("截止" in raw or "会计期间" in raw)):
        ctrl = period.get("control_date") or receipt_date
        post = period.get("posting_date") or posting_date
        pe = period.get("period_end") or period_end
        parts.extend(
            _explain_cutoff_fail(
                posting_date=post,
                control_date=ctrl,
                period_end=pe,
                cutoff_blob=cut_blob,
            )
        )
    elif period.get("verdict") is False and "收入未记入正确会计期间" in raw:
        parts.extend(
            _explain_cutoff_fail(
                posting_date=period.get("posting_date") or posting_date,
                control_date=period.get("control_date") or receipt_date,
                period_end=period.get("period_end") or period_end,
                cutoff_blob=cut_blob,
            )
        )

    parts.extend(_explain_missing(raw))

    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        p = str(p or "").strip()
        if not p or p in seen:
            continue
        seen.add(p)
        out.append(p)

    if not out and raw:
        # 最后兜底：去掉模板噪音标签，保留可读碎片
        cleaned = raw
        for mark in (
            "【规则发现】",
            "—— 以下为系统观察/待判断，非审计师最终结论 ——",
            "待审计师判断：",
            "系统观察：",
        ):
            cleaned = cleaned.replace(mark, "")
        cleaned = "；".join(
            x.strip("；、- \n")
            for x in cleaned.replace("\n", "；").split("；")
            if x.strip() and "FORMULA_LOGIC" not in x
        )
        if cleaned:
            out.append(cleaned[:500])

    if transport and "外销" in str(transport) and any("装船" in x or "承运" in x for x in out):
        pass  # 已含外销说明

    return "\n".join(out)

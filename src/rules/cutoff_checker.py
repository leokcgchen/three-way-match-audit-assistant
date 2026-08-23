"""截止性测试核心计算逻辑。

收入确认时点 = 商品控制权转移日（默认取验收/签收日），
与序时账过账日比对；**主判据为会计期间是否一致**（默认自然月），
而非「±N 天」日差容差。付款账期不参与应确认日计算。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.models.contract_models import CutoffTestResult


class CutoffChecker:
    """根据控制权转移日与入账日判断收入确认截止性是否合理。"""

    # 保留常量仅作文档兼容；不再作为 PASS/FAIL 主判据
    TOLERANCE_DAYS = 0

    def check(
        self,
        contract_payment_days: Optional[int],
        receipt_date: Optional[str],
        entry_date: Optional[str],
        *,
        period_end: Optional[str] = None,
        calendar_mode: Optional[str] = None,
        fiscal_year_start: Optional[str] = None,
    ) -> CutoffTestResult:
        """
        执行截止性测试。

        Args:
            contract_payment_days: 合同付款账期天数（仅记录，不参与应确认日）
            receipt_date: 控制权转移日 YYYY-MM-DD
            entry_date: 序时账过账/入账日期 YYYY-MM-DD
            period_end: 可选报告期末日 YYYY-MM-DD；有值时参与主判断（期内/期后同侧）
            calendar_mode: natural_month | fiscal_445 | period_end_only
            fiscal_year_start: 4-4-5 财年起点 YYYY-MM-DD
        """
        self._calendar_mode = calendar_mode or "natural_month"
        self._fiscal_year_start = fiscal_year_start
        self._period_end = period_end
        trail: List[Dict[str, Any]] = []

        parsed_receipt = self._parse_date(receipt_date) if receipt_date else None
        step1: Dict[str, Any] = {
            "step": 1,
            "action": "解析控制权转移日（签收/验收完成日）",
            "input": receipt_date if receipt_date is not None else None,
        }
        if not receipt_date:
            step1["output"] = None
            step1["error"] = "签收/验收日期为空"
        elif parsed_receipt is None:
            step1["output"] = None
            step1["error"] = f"无法解析签收/验收日期: {receipt_date}"
        else:
            step1["output"] = self._format_date(parsed_receipt)
        trail.append(step1)

        parsed_entry = self._parse_date(entry_date) if entry_date else None
        step2: Dict[str, Any] = {
            "step": 2,
            "action": "解析入账日期（序时账过账日）",
            "input": entry_date if entry_date is not None else None,
        }
        if not entry_date:
            step2["output"] = None
            step2["error"] = "入账日期为空"
        elif parsed_entry is None:
            step2["output"] = None
            step2["error"] = f"无法解析入账日期: {entry_date}"
        else:
            step2["output"] = self._format_date(parsed_entry)
        trail.append(step2)

        if contract_payment_days is not None:
            payment_note = (
                f"付款账期{int(contract_payment_days)}日"
                "（结算条款，不影响收入确认时点；已保留供后续测试）"
            )
        else:
            payment_note = "未提供付款账期（截止性不依赖账期）"
        trail.append(
            {
                "step": 3,
                "action": "记录付款账期（截止性忽略）",
                "input": payment_note,
                "output": (
                    int(contract_payment_days)
                    if contract_payment_days is not None
                    else None
                ),
            }
        )

        pe = self._parse_date(period_end) if period_end else None
        if pe is not None:
            trail.append(
                {
                    "step": 3.5,
                    "action": "记录报告期末日（辅助）",
                    "input": period_end,
                    "output": self._format_date(pe),
                }
            )

        if parsed_receipt is None or parsed_entry is None:
            trail.append(
                {
                    "step": 4,
                    "action": "映射会计期间",
                    "formula": None,
                    "output": None,
                    "error": "前置日期解析失败，跳过计算",
                }
            )
            trail.append(
                {
                    "step": 5,
                    "action": "计算偏差天数",
                    "formula": None,
                    "output": None,
                    "error": "前置日期解析失败，跳过计算",
                }
            )
            trail.append(
                {
                    "step": 6,
                    "action": "判断合规性",
                    "rule": (
                        "控制权转移日与入账日须映射至同一会计期间（默认自然月）；"
                        "跨期提前/延后确认均为 FAIL；同期间日差仅记操作性偏差"
                    ),
                    "output": "WARNING（缺少有效日期）",
                    "error": "缺少签收日期或入账日期，无法执行截止性测试",
                }
            )
            return CutoffTestResult(
                test_status="WARNING",
                expected_revenue_date=None,
                actual_entry_date=self._format_date(parsed_entry)
                if parsed_entry
                else entry_date,
                deviation_days=None,
                issue_description="缺少签收日期或入账日期，无法执行截止性测试",
                calculation_basis=(
                    f"control_transfer_date={receipt_date or '空'}, "
                    f"entry_date={entry_date or '空'}"
                ),
                calculation_trail=trail,
            )

        expected_dt = parsed_receipt
        expected_str = self._format_date(expected_dt)
        receipt_str = expected_str
        entry_str = self._format_date(parsed_entry)
        ctrl_period = self._period_key(parsed_receipt)
        entry_period = self._period_key(parsed_entry)

        trail.append(
            {
                "step": 4,
                "action": "映射会计期间并确定应确认日期",
                "formula": (
                    "应确认日=控制权转移日；会计期间=YYYY-MM（自然月）；"
                    "付款账期不参与"
                ),
                "output": {
                    "expected_revenue_date": expected_str,
                    "control_period": ctrl_period,
                    "entry_period": entry_period,
                },
            }
        )

        deviation_days = (parsed_entry.date() - expected_dt.date()).days
        trail.append(
            {
                "step": 5,
                "action": "计算偏差天数（辅助说明，非主判据）",
                "formula": f"{entry_str} - {expected_str}",
                "output": deviation_days,
            }
        )

        same_period = ctrl_period == entry_period

        # 报告期末日：控制权日与入账日须同侧（期内/期后），否则跨期末 FAIL
        pe_boundary_fail = False
        pe_note = ""
        if pe is not None:
            pe_d = pe.date()
            ctrl_side = "期内" if parsed_receipt.date() <= pe_d else "期后"
            entry_side = "期内" if parsed_entry.date() <= pe_d else "期后"
            pe_note = (
                f"；报告期末日 {self._format_date(pe)}："
                f"控制权属{ctrl_side}、入账属{entry_side}"
            )
            trail.append(
                {
                    "step": 4.5,
                    "action": "按报告期末日划分期内/期后",
                    "input": period_end,
                    "output": {
                        "period_end": self._format_date(pe),
                        "control_side": ctrl_side,
                        "entry_side": entry_side,
                    },
                }
            )
            if ctrl_side != entry_side:
                pe_boundary_fail = True
                same_period = False

        calculation_basis = (
            f"控制权转移日（签收/验收）{receipt_str} 属期间 {ctrl_period}；"
            f"实际入账{entry_str} 属期间 {entry_period}；"
            f"主判据=会计期间一致"
            f"{' + 报告期末边界' if pe is not None else ''}；"
            f"付款账期不参与截止判断"
            f"{pe_note}"
        )

        if pe_boundary_fail:
            status = "FAIL"
            if parsed_entry.date() < parsed_receipt.date():
                issue = (
                    f"相对报告期末日 {self._format_date(pe)}："
                    f"控制权于期后转移，入账却记在期内，跨期末提前确认"
                )
                verdict = "FAIL（跨报告期末：期后控制权 / 期内入账）"
            else:
                issue = (
                    f"相对报告期末日 {self._format_date(pe)}："
                    f"控制权已在期内转移，入账记在期后，跨期末延后确认"
                )
                verdict = "FAIL（跨报告期末：期内控制权 / 期后入账）"
        elif same_period:
            status = "PASS"
            if deviation_days == 0:
                issue = "入账日期与控制权转移日同日，属同一会计期间"
                verdict = "PASS（同期间且同日）"
            else:
                issue = (
                    f"入账日与控制权转移日相差{deviation_days}天，"
                    f"但同属会计期间 {ctrl_period}："
                    f"记操作性偏差，不构成跨期错报"
                )
                verdict = f"PASS（同期间，日差{deviation_days}天）"
        elif deviation_days < 0:
            status = "FAIL"
            issue = (
                f"商品控制权于{receipt_str}（期间{ctrl_period}）才转移，"
                f"收入应记入该期间；账务于{entry_str}（期间{entry_period}）入账，"
                f"提前确认收入，截止认定存在错报"
            )
            verdict = f"FAIL（跨期提前，期间 {entry_period}→{ctrl_period}）"
        else:
            status = "FAIL"
            issue = (
                f"控制权已于{receipt_str}（期间{ctrl_period}）转移，"
                f"账务于{entry_str}（期间{entry_period}）入账，"
                f"延后确认收入，截止认定存在错报"
            )
            verdict = f"FAIL（跨期延后，期间 {ctrl_period}→{entry_period}）"

        trail.append(
            {
                "step": 6,
                "action": "判断合规性",
                "rule": (
                    "同一会计期间→PASS（日差仅操作性偏差）；"
                    "跨期提前或延后→FAIL；"
                    "若提供报告期末日，控制权与入账须同属期内或同属期后"
                ),
                "output": verdict,
            }
        )

        return CutoffTestResult(
            test_status=status,  # type: ignore[arg-type]
            expected_revenue_date=expected_str,
            actual_entry_date=entry_str,
            deviation_days=deviation_days,
            issue_description=issue,
            calculation_basis=calculation_basis,
            calculation_trail=trail,
        )

    def _period_key(self, dt: datetime) -> str:
        """会计期间键：默认自然月；可切换 4-4-5 / 仅期末边界。"""
        from src.audit.accounting_calendar import period_key

        return period_key(
            dt.date(),
            mode=getattr(self, "_calendar_mode", None) or "natural_month",
            fiscal_year_start=getattr(self, "_fiscal_year_start", None),
            period_end=getattr(self, "_period_end", None),
        )

    @staticmethod
    def _parse_date(date_str: str) -> Optional[datetime]:
        if not date_str or not str(date_str).strip():
            return None
        try:
            return datetime.strptime(str(date_str).strip(), "%Y-%m-%d")
        except ValueError:
            return None

    @staticmethod
    def _format_date(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%d")

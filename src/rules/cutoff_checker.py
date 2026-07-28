"""截止性测试核心计算逻辑。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.models.contract_models import CutoffTestResult


class CutoffChecker:
    """根据签收日、账期与入账日判断收入确认截止性是否合理。"""

    TOLERANCE_DAYS = 2

    def check(
        self,
        contract_payment_days: Optional[int],
        receipt_date: Optional[str],
        entry_date: Optional[str],
    ) -> CutoffTestResult:
        """
        执行截止性测试。

        Args:
            contract_payment_days: 合同约定账期天数（签收后N日）；为空按0处理
            receipt_date: 签收/验收日期 YYYY-MM-DD
            entry_date: 序时账入账日期 YYYY-MM-DD
        """
        trail: List[Dict[str, Any]] = []

        # Step 1: 解析签收日期
        parsed_receipt = self._parse_date(receipt_date) if receipt_date else None
        step1: Dict[str, Any] = {
            "step": 1,
            "action": "解析签收日期",
            "input": receipt_date if receipt_date is not None else None,
        }
        if not receipt_date:
            step1["output"] = None
            step1["error"] = "签收日期为空"
        elif parsed_receipt is None:
            step1["output"] = None
            step1["error"] = f"无法解析签收日期: {receipt_date}"
        else:
            step1["output"] = self._format_date(parsed_receipt)
        trail.append(step1)

        # Step 2: 解析入账日期
        parsed_entry = self._parse_date(entry_date) if entry_date else None
        step2: Dict[str, Any] = {
            "step": 2,
            "action": "解析入账日期",
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

        # Step 3: 读取账期
        payment_days = 0 if contract_payment_days is None else int(contract_payment_days)
        trail.append(
            {
                "step": 3,
                "action": "读取账期",
                "input": (
                    f"合同约定签收后{payment_days}日"
                    if contract_payment_days is not None
                    else "账期未提供，按0日处理"
                ),
                "output": payment_days,
            }
        )

        if parsed_receipt is None or parsed_entry is None:
            trail.append(
                {
                    "step": 4,
                    "action": "计算应确认日期",
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
                    "rule": f"偏差在±{self.TOLERANCE_DAYS}天内为PASS",
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
                    f"receipt_date={receipt_date or '空'}, "
                    f"entry_date={entry_date or '空'}"
                ),
                calculation_trail=trail,
            )

        # Step 4: 应确认日期
        expected_dt = parsed_receipt + timedelta(days=payment_days)
        expected_str = self._format_date(expected_dt)
        receipt_str = self._format_date(parsed_receipt)
        entry_str = self._format_date(parsed_entry)
        trail.append(
            {
                "step": 4,
                "action": "计算应确认日期",
                "formula": f"{receipt_str} + {payment_days}天",
                "output": expected_str,
            }
        )

        # Step 5: 偏差天数
        deviation_days = (parsed_entry.date() - expected_dt.date()).days
        trail.append(
            {
                "step": 5,
                "action": "计算偏差天数",
                "formula": f"{entry_str} - {expected_str}",
                "output": deviation_days,
            }
        )

        calculation_basis = (
            f"签收日{receipt_str} + 账期{payment_days}日 = 应确认{expected_str}；"
            f"实际入账{entry_str}"
        )

        # Step 6: 判断
        if -self.TOLERANCE_DAYS <= deviation_days <= self.TOLERANCE_DAYS:
            status = "PASS"
            issue = (
                f"入账日期与应确认日期偏差{deviation_days}天，"
                f"在±{self.TOLERANCE_DAYS}天容差内"
            )
            verdict = f"PASS（偏差{deviation_days}天）"
        elif deviation_days < -self.TOLERANCE_DAYS:
            status = "FAIL"
            issue = f"提前{abs(deviation_days)}天确认收入，存在跨期风险"
            verdict = f"FAIL（提前{abs(deviation_days)}天）"
        else:
            status = "WARNING"
            issue = f"延迟{deviation_days}天确认收入，建议核实是否截期"
            verdict = f"WARNING（延迟{deviation_days}天）"

        trail.append(
            {
                "step": 6,
                "action": "判断合规性",
                "rule": f"偏差在±{self.TOLERANCE_DAYS}天内为PASS",
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

    @staticmethod
    def _parse_date(date_str: str) -> Optional[datetime]:
        """统一解析 YYYY-MM-DD；失败返回 None。"""
        if not date_str or not str(date_str).strip():
            return None
        try:
            return datetime.strptime(str(date_str).strip(), "%Y-%m-%d")
        except ValueError:
            return None

    @staticmethod
    def _format_date(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%d")

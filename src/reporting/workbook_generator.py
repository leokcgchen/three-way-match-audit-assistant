"""GOSPD01010 底稿抽样表 CSV 生成器。"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, List, Mapping

from src.models.schemas import CutoffResponse

# GOSPD01010 抽样表列顺序（固定 15 列）
WORKBOOK_COLUMNS: List[str] = [
    "业务编号",
    "凭证号",
    "客户名称",
    "合同编号",
    "入账日期",
    "入账金额",
    "签收日期",
    "合同账期（天）",
    "应确认日期",
    "偏差天数",
    "测试状态",
    "风险等级",
    "问题描述",
    "计算依据",
    "审计结论",
]


def _cell(value: Any) -> str:
    """None → 空字符串，其余转 str（保留列位）。"""
    if value is None:
        return ""
    return str(value)


class WorkbookGenerator:
    """将截止性测试结果汇总/追加为 GOSPD01010 底稿 CSV。"""

    COLUMNS = WORKBOOK_COLUMNS

    @classmethod
    def _fill(cls, response: CutoffResponse) -> Mapping[str, Any]:
        fill = response.底稿回填 or {}
        return fill if isinstance(fill, Mapping) else {}

    @classmethod
    def response_to_row(cls, response: CutoffResponse) -> dict[str, str]:
        """将 CutoffResponse 映射为底稿一行（缺值保留空列）。"""
        fill = cls._fill(response)
        payment_days = fill.get("合同账期（天）")
        if payment_days is None:
            payment_days = fill.get("合同账期天数")
        return {
            "业务编号": _cell(response.业务编号),
            "凭证号": _cell(fill.get("凭证号")),
            "客户名称": _cell(fill.get("客户名称")),
            "合同编号": _cell(fill.get("合同编号")),
            "入账日期": _cell(fill.get("入账日期")),
            "入账金额": _cell(fill.get("入账金额")),
            "签收日期": _cell(fill.get("签收日期")),
            "合同账期（天）": _cell(payment_days),
            "应确认日期": _cell(response.应确认日期),
            "偏差天数": _cell(response.偏差天数),
            "测试状态": _cell(response.测试状态),
            "风险等级": _cell(response.风险等级),
            "问题描述": _cell(response.问题描述),
            "计算依据": _cell(response.计算依据),
            "审计结论": _cell(fill.get("审计结论")),
        }

    @classmethod
    def generate_from_responses(
        cls, responses: List[CutoffResponse], output_path: str
    ) -> str:
        """批量汇总为底稿 CSV，覆盖写入；返回输出路径。"""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=cls.COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for response in responses:
                writer.writerow(cls.response_to_row(response))
        return str(path)

    @classmethod
    def append_to_workbook(cls, response: CutoffResponse, output_path: str) -> None:
        """追加一行到底稿 CSV；文件不存在时自动创建并写表头。"""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not path.exists() or path.stat().st_size == 0
        with path.open("a", encoding="utf-8-sig", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=cls.COLUMNS, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            writer.writerow(cls.response_to_row(response))

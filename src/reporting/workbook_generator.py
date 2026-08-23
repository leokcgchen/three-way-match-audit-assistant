"""GOSPD01010 底稿抽样表 CSV 生成器（含三单匹配扩展列）。"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence, Union

from pydantic import BaseModel

from config.settings import settings
from src.models.schemas import CutoffResponse
from src.three_way_match.models import ThreeWayMatchRequest, ThreeWayMatchResponse

# GOSPD01010 抽样表列顺序（固定 22 列）
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
    # 三单匹配扩展（无匹配结果时留空）
    "三单匹配状态",
    "供应商一致性",
    "订单金额（万元）",
    "入库金额（万元）",
    "发票金额（万元）",
    "金额差异率（%）",
    "三单决策",
]


class WorkbookRecord(BaseModel):
    """底稿一行完整数据源：截止性结果 + 可选三单匹配。"""

    cutoff_response: Optional[CutoffResponse] = None
    match_result: Optional[ThreeWayMatchResponse] = None
    match_request: Optional[ThreeWayMatchRequest] = None


def _cell(value: Any) -> str:
    """None → 空字符串，其余转 str（保留列位）。"""
    if value is None:
        return ""
    return str(value)


def _amount_diff_rate(order_amt: float, receipt_amt: float, invoice_amt: float) -> str:
    vals = [float(order_amt), float(receipt_amt), float(invoice_amt)]
    peak = max(abs(v) for v in vals)
    if peak == 0:
        return "0"
    rate = (max(vals) - min(vals)) / peak * 100.0
    return f"{rate:.4g}"


def _cmp_map(match: ThreeWayMatchResponse) -> dict[str, Any]:
    return {c.field_name: c for c in match.comparisons}


class WorkbookGenerator:
    """将截止性/三单匹配结果汇总/追加为 GOSPD01010 底稿 CSV。"""

    COLUMNS = WORKBOOK_COLUMNS

    @classmethod
    def default_output_path(cls) -> Path:
        path = settings.get_workbook_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def _fill(cls, response: Optional[CutoffResponse]) -> Mapping[str, Any]:
        if response is None:
            return {}
        fill = response.底稿回填 or {}
        return fill if isinstance(fill, Mapping) else {}

    @classmethod
    def _match_extra_columns(
        cls,
        match: Optional[ThreeWayMatchResponse],
        match_request: Optional[ThreeWayMatchRequest],
    ) -> dict[str, str]:
        empty = {
            "三单匹配状态": "",
            "供应商一致性": "",
            "订单金额（万元）": "",
            "入库金额（万元）": "",
            "发票金额（万元）": "",
            "金额差异率（%）": "",
            "三单决策": "",
        }
        if match is None:
            return empty

        cmps = _cmp_map(match)
        supplier = cmps.get("supplier_name")
        amount = cmps.get("total_amount")

        if match_request is not None:
            order_amt = match_request.order.total_amount
            receipt_amt = match_request.warehouse_receipt.total_amount
            invoice_amt = match_request.invoice.total_amount
        elif amount is not None:
            order_amt = float(amount.order_value)
            receipt_amt = float(amount.receipt_value)
            invoice_amt = float(amount.invoice_value)
        else:
            order_amt = receipt_amt = invoice_amt = None

        supplier_label = ""
        if supplier is not None:
            supplier_label = "一致" if supplier.is_consistent else "不一致"

        return {
            "三单匹配状态": _cell(match.overall_status),
            "供应商一致性": supplier_label,
            "订单金额（万元）": _cell(order_amt),
            "入库金额（万元）": _cell(receipt_amt),
            "发票金额（万元）": _cell(invoice_amt),
            "金额差异率（%）": (
                _amount_diff_rate(order_amt, receipt_amt, invoice_amt)
                if order_amt is not None
                else ""
            ),
            "三单决策": _cell(getattr(match, "decision", None) or match.overall_status),
        }

    @classmethod
    def record_to_row(cls, record: WorkbookRecord) -> dict[str, str]:
        """将完整记录映射为底稿一行（缺值保留空列）。"""
        response = record.cutoff_response
        fill = cls._fill(response)
        payment_days = fill.get("合同账期（天）")
        if payment_days is None:
            payment_days = fill.get("合同账期天数")

        # 无截止性时，尽量从三单请求补业务主键与基础字段
        req = record.match_request
        biz = (
            response.业务编号
            if response is not None
            else (req.order.order_no if req else "")
        )
        row = {
            "业务编号": _cell(biz),
            "凭证号": _cell(fill.get("凭证号")),
            "客户名称": _cell(
                fill.get("客户名称")
                or (req.order.supplier_name if req else None)
            ),
            "合同编号": _cell(
                fill.get("合同编号") or (req.order.contract_no if req else None)
            ),
            "入账日期": _cell(
                fill.get("入账日期")
                or (req.invoice.posting_date if req else None)
            ),
            "入账金额": _cell(
                fill.get("入账金额")
                or (req.invoice.total_amount if req else None)
            ),
            "签收日期": _cell(
                fill.get("签收日期")
                or (req.warehouse_receipt.receipt_date if req else None)
            ),
            "合同账期（天）": _cell(payment_days),
            "应确认日期": _cell(response.应确认日期 if response else None),
            "偏差天数": _cell(response.偏差天数 if response else None),
            "测试状态": _cell(response.测试状态 if response else None),
            "风险等级": _cell(response.风险等级 if response else None),
            "问题描述": _cell(response.问题描述 if response else None),
            "计算依据": _cell(response.计算依据 if response else None),
            "审计结论": _cell(fill.get("审计结论")),
        }
        row.update(cls._match_extra_columns(record.match_result, record.match_request))
        return row

    @classmethod
    def response_to_row(cls, response: CutoffResponse) -> dict[str, str]:
        """兼容：仅截止性结果（三单扩展列为空）。"""
        return cls.record_to_row(WorkbookRecord(cutoff_response=response))

    @classmethod
    def _normalize(
        cls,
        item: Union[WorkbookRecord, CutoffResponse, Mapping[str, Any]],
    ) -> WorkbookRecord:
        if isinstance(item, WorkbookRecord):
            return item
        if isinstance(item, CutoffResponse):
            return WorkbookRecord(cutoff_response=item)
        if isinstance(item, Mapping):
            return WorkbookRecord.model_validate(item)
        raise TypeError(f"不支持的底稿记录类型: {type(item)!r}")

    @classmethod
    def generate_from_responses(
        cls,
        responses: Sequence[Union[WorkbookRecord, CutoffResponse, Mapping[str, Any]]],
        output_path: Optional[str] = None,
    ) -> str:
        """批量汇总为底稿 CSV，覆盖写入；返回输出路径。"""
        path = Path(output_path) if output_path else cls.default_output_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=cls.COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for item in responses:
                writer.writerow(cls.record_to_row(cls._normalize(item)))
        return str(path)

    @classmethod
    def append_to_workbook(
        cls,
        response: Union[
            WorkbookRecord, CutoffResponse, Mapping[str, Any], None
        ] = None,
        output_path: Optional[str] = None,
        *,
        cutoff_response: Optional[CutoffResponse] = None,
        match_result: Optional[ThreeWayMatchResponse] = None,
        match_request: Optional[ThreeWayMatchRequest] = None,
    ) -> Path:
        """追加一行到底稿 CSV；文件不存在时自动创建并写表头。"""
        if response is not None:
            record = cls._normalize(response)
        else:
            record = WorkbookRecord(
                cutoff_response=cutoff_response,
                match_result=match_result,
                match_request=match_request,
            )
        path = Path(output_path) if output_path else cls.default_output_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not path.exists() or path.stat().st_size == 0
        with path.open("a", encoding="utf-8-sig", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=cls.COLUMNS, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            writer.writerow(cls.record_to_row(record))
        return path

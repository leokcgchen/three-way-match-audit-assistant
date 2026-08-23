"""三单匹配门面：对外统一入口，可联动截止性 Agent。"""

from __future__ import annotations

from typing import Any, Literal, Optional

import requests

from src.models.schemas import CutoffRequest, CutoffResponse
from src.reporting.workbook_generator import WorkbookGenerator, WorkbookRecord
from src.three_way_match.engine import run_match
from src.three_way_match.models import (
    Invoice,
    Order,
    ThreeWayMatchRequest,
    ThreeWayMatchResponse,
    WarehouseReceipt,
)
from src.three_way_match.summary import (
    build_cutoff_summary,
    build_human_readable_summary,
    build_three_way_summary,
)
from src.utils.date_extractor import extract_days_from_description, pick_receipt_date_from_fields
from src.utils.logger import logger

Status = Literal["PASS", "WARNING", "FAIL"]
_STATUS_RANK: dict[str, int] = {"PASS": 0, "WARNING": 1, "FAIL": 2}

SKIP_REASON_MISSING_POSTING = "入账日期缺失，无法执行截止性测试"
SKIP_REASON_MISSING_RECEIPT = "控制权转移/签收日期缺失，无法执行截止性测试"


def _to_yuan_float(
    value: Any,
    default: float = 0.0,
    *,
    amount_unit: str | None = None,
) -> float:
    """解析 OCR 金额为元。若字段标明万元则还原；禁止数量走此函数。"""
    from src.legacy_ocr.amount_resolve import WAN_YUAN_THRESHOLD, _parse_number

    if value is None:
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        num = float(value)
    else:
        parsed = _parse_number(value)
        if parsed is None:
            return default
        num = parsed
    if num <= 0:
        return default
    unit = str(amount_unit or "").strip()
    if unit in {"万元", "wan", "WAN"}:
        return round(num * WAN_YUAN_THRESHOLD, 2)
    return round(num, 2)


def _to_qty_float(value: Any, default: float = 0.0) -> float:
    """数量独立解析，禁止走金额万元换算。缺省返回 default（调用方应优先用 pick）。"""
    from src.legacy_ocr.amount_resolve import _parse_number

    if value is None:
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        num = float(value)
    else:
        parsed = _parse_number(value)
        if parsed is None:
            return default
        num = parsed
    if num <= 0:
        return default
    return float(num)


def _qty_from_fields(fields: dict[str, Any]) -> tuple[float, bool]:
    """返回 (数量, 是否抽到)。未抽到时数量为 0 且 found=False，摘要勿报成 100% 业务差异。"""
    from src.legacy_ocr.field_aliases import pick_quantity_value

    q = pick_quantity_value(fields)
    if q is None:
        return 0.0, False
    return float(q), True


def _first_yuan_amount(*candidates: Any, amount_unit: str | None = None) -> float:
    for val in candidates:
        amt = _to_yuan_float(val, default=0.0, amount_unit=amount_unit)
        if amt > 0:
            return amt
    return 0.0


def _coalesce_three_way_amounts(
    order_amt: float,
    receipt_amt: float,
    invoice_amt: float,
) -> tuple[float, float, float]:
    """禁止用其他单据金额填补缺失（审计规范）；缺多少报多少。"""
    return order_amt, receipt_amt, invoice_amt


def _to_float(value: Any, default: float = 0.0) -> float:
    """兼容旧调用：解析为元。"""
    return _to_yuan_float(value, default=default)


# 旧名兼容
_to_wan_float = _to_yuan_float
_first_wan_amount = _first_yuan_amount


def _pick_order_no(*candidates: Any) -> str:
    for item in candidates:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            return text
    return "UNKNOWN"


def _pick_customer_name(*field_maps: dict[str, Any]) -> str:
    """销售收入客户名：优先 customer*/buyer*，兼容历史 supplierName。"""
    keys = (
        "customerName",
        "customer_name",
        "buyerName",
        "clientName",
        "supplierName",
        "supplier_name",
    )
    for fields in field_maps:
        for key in keys:
            raw = fields.get(key)
            if raw is None:
                continue
            text = str(raw).strip()
            if text:
                return text
    return ""


def build_request_from_ocr_fields(
    order_fields: dict[str, Any],
    receipt_fields: dict[str, Any],
    invoice_fields: dict[str, Any],
) -> ThreeWayMatchRequest:
    """将 OCR 提取字段组装为 ThreeWayMatchRequest。"""
    order_no = _pick_order_no(
        order_fields.get("documentNo"),
        receipt_fields.get("remarks"),
        invoice_fields.get("remarks"),
    )
    supplier = _pick_customer_name(order_fields, receipt_fields, invoice_fields)
    receipt_date = str(
        pick_receipt_date_from_fields(receipt_fields)
        or receipt_fields.get("documentDate")
        or receipt_fields.get("deliveryDate")
        or ""
    ).strip()
    # 缺签收/控制权日时留空，由 match_and_cutoff 跳过截止（不再用 1970-01-01 冒充）
    if not receipt_date or receipt_date in {"1970-01-01", "None", "null"}:
        receipt_date = ""
    posting_raw = invoice_fields.get("postingDate")
    posting_date = str(posting_raw).strip() if posting_raw else None

    order_amt = _first_yuan_amount(
        order_fields.get("totalAmount"),
        order_fields.get("amount"),
        amount_unit=str(order_fields.get("_amountUnit") or ""),
    )
    receipt_amt = _first_yuan_amount(
        receipt_fields.get("totalAmount"),
        receipt_fields.get("amount"),
        amount_unit=str(receipt_fields.get("_amountUnit") or ""),
    )
    invoice_amt = _first_yuan_amount(
        invoice_fields.get("totalAmount"),
        invoice_fields.get("amount"),
        amount_unit=str(invoice_fields.get("_amountUnit") or ""),
    )
    order_amt, receipt_amt, invoice_amt = _coalesce_three_way_amounts(
        order_amt, receipt_amt, invoice_amt
    )

    order_qty, _ = _qty_from_fields(order_fields)
    receipt_qty, _ = _qty_from_fields(receipt_fields)
    invoice_qty, _ = _qty_from_fields(invoice_fields)

    # 缺客户名留空，引擎比对会 FAIL；禁止「未知供应商」冒充有效名称
    return ThreeWayMatchRequest(
        order=Order(
            order_no=order_no,
            supplier_name=supplier,
            total_amount=order_amt,
            quantity=order_qty,
            unit=order_fields.get("unit"),
            order_date=order_fields.get("documentDate"),
            payment_terms=order_fields.get("paymentTerms"),
            contract_no=order_fields.get("contractNo"),
        ),
        warehouse_receipt=WarehouseReceipt(
            receipt_no=_pick_order_no(receipt_fields.get("documentNo"), "RC-UNKNOWN"),
            order_no=_pick_order_no(receipt_fields.get("remarks"), order_no),
            supplier_name=str(
                _pick_customer_name(receipt_fields) or supplier or ""
            ),
            total_amount=receipt_amt,
            quantity=receipt_qty,
            receipt_date=receipt_date or "UNRESOLVED",
            receiver=None,
        ),
        invoice=Invoice(
            invoice_no=_pick_order_no(
                invoice_fields.get("invoiceNo"),
                invoice_fields.get("documentNo"),
                "INV-UNKNOWN",
            ),
            order_no=_pick_order_no(invoice_fields.get("remarks"), order_no),
            supplier_name=str(
                _pick_customer_name(invoice_fields) or supplier or ""
            ),
            total_amount=invoice_amt,
            quantity=invoice_qty,
            invoice_date=invoice_fields.get("documentDate"),
            posting_date=posting_date,
        ),
    )


def merge_overall_status(*statuses: Optional[str]) -> Status:
    """合并多路结论：FAIL > WARNING > PASS。"""
    best: Status = "PASS"
    for status in statuses:
        if status is None:
            continue
        key = str(status).upper()
        if key not in _STATUS_RANK:
            continue
        if _STATUS_RANK[key] > _STATUS_RANK[best]:
            best = key  # type: ignore[assignment]
    return best


class ThreeWayMatcher:
    """三单匹配适配器门面。"""

    def __init__(self) -> None:
        self._workbook = WorkbookGenerator()

    def match_from_legacy_ocr(
        self,
        file_paths: dict[str, str],
        cutoff_agent_url: str = "http://localhost:8000/api/v1/cutoff",
        *,
        inprocess: bool = True,
    ) -> dict[str, Any]:
        """
        从三张单据图片/PDF 走 Legacy OCR → 组装请求 → 三单匹配 + 截止性。

        file_paths 示例：
        {"order": ".../po.jpg", "receipt": ".../wr.jpg", "invoice": ".../inv.jpg"}
        """
        from src.legacy_ocr import LegacyOcrAdapter

        required = ("order", "receipt", "invoice")
        missing = [k for k in required if not file_paths.get(k)]
        if missing:
            raise ValueError(f"file_paths 缺少键: {missing}")

        adapter = LegacyOcrAdapter()
        order_ocr = adapter.recognize_and_extract(file_paths["order"], "purchase_order")
        receipt_ocr = adapter.recognize_and_extract(
            file_paths["receipt"], "warehouse_receipt"
        )
        invoice_ocr = adapter.recognize_and_extract(file_paths["invoice"], "invoice")

        request = build_request_from_ocr_fields(
            order_ocr.get("extractedFields") or {},
            receipt_ocr.get("extractedFields") or {},
            invoice_ocr.get("extractedFields") or {},
        )
        logger.info(
            "match_from_legacy_ocr assembled order_no={} sources={}",
            request.order.order_no,
            {
                "order": order_ocr.get("source"),
                "receipt": receipt_ocr.get("source"),
                "invoice": invoice_ocr.get("source"),
            },
        )
        result = self.match_and_cutoff(
            request, cutoff_agent_url=cutoff_agent_url, inprocess=inprocess
        )
        result["ocr_meta"] = {
            "order": order_ocr,
            "receipt": receipt_ocr,
            "invoice": invoice_ocr,
        }
        return result

    def match(self, request: ThreeWayMatchRequest) -> ThreeWayMatchResponse:
        """执行三单匹配并记录日志。"""
        logger.info(
            "three-way match start order_no={} supplier={}",
            request.order.order_no,
            request.order.supplier_name,
        )
        result = run_match(request)
        if result.overall_status == "PASS":
            logger.info(
                "three-way match PASS order_no={} decision={}",
                result.order_no,
                result.decision,
            )
        elif result.overall_status == "WARNING":
            logger.warning(
                "three-way match WARNING order_no={} decision={} risks={}",
                result.order_no,
                result.decision,
                result.risks,
            )
        else:
            logger.warning(
                "three-way match FAIL order_no={} decision={} risks={}",
                result.order_no,
                result.decision,
                result.risks,
            )
        return result

    def build_cutoff_payload(
        self,
        request: ThreeWayMatchRequest,
        *,
        period_end: str | None = None,
    ) -> dict[str, Any]:
        """从三单请求组装截止性 Agent 所需 JSON（中文字段）。

        付款账期仍传入并写入底稿，供后续收款等测试；截止公式用签收日、入账日，
        以及可选报告期末日。
        """
        payment_terms = request.order.payment_terms
        payment_days = (
            extract_days_from_description(payment_terms) if payment_terms else None
        )
        payload: dict[str, Any] = {
            "业务编号": request.order.order_no,
            "合同编号": request.order.contract_no,
            "客户名称": request.order.supplier_name,
            "合同账期描述": payment_terms,
            "合同账期天数": payment_days,
            "签收日期": request.warehouse_receipt.receipt_date,
            "入账日期": request.invoice.posting_date,
            "入账金额": float(request.invoice.total_amount),
        }
        pe = (period_end or "").strip()
        if pe:
            payload["报告期末日"] = pe
        return payload

    def _skip_cutoff_missing_posting(
        self,
        request: ThreeWayMatchRequest,
        match_result: ThreeWayMatchResponse,
    ) -> dict[str, Any]:
        """入账日期缺失：不调用截止性，不编造默认值。"""
        logger.warning(
            "cutoff skipped: missing posting_date order_no={}",
            request.order.order_no,
        )
        match_result = match_result.model_copy(
            update={
                "cutoff_available": False,
                "cutoff_skipped_reason": SKIP_REASON_MISSING_POSTING,
                "cutoff_test_status": "SKIPPED",
            }
        )
        return self._finalize(
            request,
            match_result,
            overall_status=match_result.overall_status,
            cutoff_result=None,
            cutoff_available=False,
            cutoff_skipped_reason=SKIP_REASON_MISSING_POSTING,
        )

    def _skip_cutoff_missing_receipt(
        self,
        request: ThreeWayMatchRequest,
        match_result: ThreeWayMatchResponse,
    ) -> dict[str, Any]:
        """控制权/签收日缺失：不调用截止性。"""
        logger.warning(
            "cutoff skipped: missing receipt/control date order_no={}",
            request.order.order_no,
        )
        match_result = match_result.model_copy(
            update={
                "cutoff_available": False,
                "cutoff_skipped_reason": SKIP_REASON_MISSING_RECEIPT,
                "cutoff_test_status": "SKIPPED",
            }
        )
        return self._finalize(
            request,
            match_result,
            overall_status=match_result.overall_status,
            cutoff_result=None,
            cutoff_available=False,
            cutoff_skipped_reason=SKIP_REASON_MISSING_RECEIPT,
        )

    def _write_workbook_row(
        self,
        request: ThreeWayMatchRequest,
        match_result: ThreeWayMatchResponse,
        cutoff_result: Optional[CutoffResponse],
    ) -> str:
        """写入含三单匹配扩展列的底稿行；返回相对路径。"""
        from config.settings import settings

        path = self._workbook.append_to_workbook(
            WorkbookRecord(
                cutoff_response=cutoff_result,
                match_result=match_result,
                match_request=request,
            )
        )
        rel = settings.get_workbook_relative_path()
        logger.info("three-way workbook appended path={} rel={}", path, rel)
        return rel

    def _call_cutoff_http(
        self, payload: dict[str, Any], cutoff_agent_url: str
    ) -> CutoffResponse:
        response = requests.post(
            cutoff_agent_url,
            json=payload,
            timeout=30,
            headers={"Content-Type": "application/json"},
        )
        if response.status_code != 200:
            raise RuntimeError(f"截止性Agent返回HTTP {response.status_code}")
        return CutoffResponse.model_validate(response.json())

    def _call_cutoff_inprocess(
        self,
        payload: dict[str, Any],
        *,
        calendar_mode: str | None = None,
        fiscal_year_start: str | None = None,
    ) -> CutoffResponse:
        """同进程调用；不写底稿（由 match_and_cutoff 统一写完整行）。"""
        from src.api.cutoff_runner import perform_cutoff

        req = CutoffRequest.model_validate(payload)
        return perform_cutoff(
            req,
            write_workbook=False,
            calendar_mode=calendar_mode,
            fiscal_year_start=fiscal_year_start,
        )

    def _attach_summary(
        self,
        request: ThreeWayMatchRequest,
        match_result: ThreeWayMatchResponse,
        *,
        overall_status: str,
        cutoff_result: Optional[CutoffResponse] = None,
        cutoff_available: bool = True,
        cutoff_skipped_reason: Optional[str] = None,
    ) -> ThreeWayMatchResponse:
        summary = build_human_readable_summary(
            request,
            match_result,
            overall_status=overall_status,
            cutoff_result=cutoff_result,
            cutoff_available=cutoff_available,
            cutoff_skipped_reason=cutoff_skipped_reason,
        )
        return match_result.model_copy(update={"human_readable_summary": summary})

    def _finalize(
        self,
        request: ThreeWayMatchRequest,
        match_result: ThreeWayMatchResponse,
        *,
        overall_status: str,
        cutoff_result: Optional[CutoffResponse] = None,
        cutoff_available: bool = True,
        cutoff_skipped_reason: Optional[str] = None,
        cutoff_error: Optional[str] = None,
        write_workbook: bool = False,
    ) -> dict[str, Any]:
        match_result = self._attach_summary(
            request,
            match_result,
            overall_status=overall_status,
            cutoff_result=cutoff_result,
            cutoff_available=cutoff_available,
            cutoff_skipped_reason=cutoff_skipped_reason,
        )
        workbook_path = None
        if write_workbook:
            workbook_path = self._write_workbook_row(
                request, match_result, cutoff_result
            )
            if cutoff_result is not None and cutoff_result.底稿文件路径 is None:
                cutoff_result = cutoff_result.model_copy(
                    update={"底稿文件路径": workbook_path}
                )
        return {
            "match_result": match_result,
            "match_request": request,
            "cutoff_result": cutoff_result,
            # 独立状态供新工作流使用；overall_status 仅为旧接口兼容的聚合值。
            "three_way_status": match_result.overall_status,
            "cutoff_status": (
                cutoff_result.测试状态
                if cutoff_result is not None
                else ("SKIPPED" if not cutoff_available else "NOT_TESTED")
            ),
            "three_way_summary": build_three_way_summary(request, match_result),
            "cutoff_summary": build_cutoff_summary(
                request,
                cutoff_result,
                cutoff_available=cutoff_available,
                cutoff_skipped_reason=cutoff_skipped_reason,
            ),
            "cutoff_available": cutoff_available,
            "cutoff_skipped_reason": cutoff_skipped_reason,
            "overall_status": overall_status,
            "cutoff_error": cutoff_error,
            "底稿文件路径": workbook_path,
            "human_readable_summary": match_result.human_readable_summary,
            "decision": match_result.decision,
            "decision_reasons": list(match_result.decision_reasons or []),
            "hold_reason_code": match_result.hold_reason_code,
            "quantity_roles": dict(match_result.quantity_roles or {}),
            "slot_reasons": dict(match_result.slot_reasons or {}),
            "erp_review": dict(match_result.erp_review or {}),
        }

    def match_and_cutoff(
        self,
        request: ThreeWayMatchRequest,
        cutoff_agent_url: str = "http://localhost:8000/api/v1/cutoff",
        *,
        inprocess: bool = False,
        period_end: str | None = None,
        calendar_mode: str | None = None,
        fiscal_year_start: str | None = None,
    ) -> dict[str, Any]:
        """
        执行三单匹配 + 自动触发截止性测试。

        - inprocess=True：同进程调用 CutoffRunner（API 路由默认，防死锁）
        - inprocess=False：requests.post 调用 cutoff_agent_url
        - 默认不写底稿文件；请在调试台「查看底稿」菜单手动生成 xlsx
        - 入账日期或控制权/签收日缺失时跳过截止性
        - period_end：报告期末日，传入截止引擎参与主判断
        - calendar_mode / fiscal_year_start：会计日历（自然月 / 4-4-5）
        """
        match_result = self.match(request)

        posting_date = (request.invoice.posting_date or "").strip()
        if not posting_date:
            return self._skip_cutoff_missing_posting(request, match_result)

        receipt_date = (request.warehouse_receipt.receipt_date or "").strip()
        if not receipt_date or receipt_date in {
            "UNRESOLVED",
            "1970-01-01",
            "None",
            "null",
        }:
            return self._skip_cutoff_missing_receipt(request, match_result)

        payload = self.build_cutoff_payload(request, period_end=period_end)
        if not (payload.get("入账日期") or "").strip():
            return self._skip_cutoff_missing_posting(request, match_result)
        if not (payload.get("签收日期") or "").strip() or payload.get("签收日期") in {
            "UNRESOLVED",
            "1970-01-01",
        }:
            return self._skip_cutoff_missing_receipt(request, match_result)

        try:
            if inprocess:
                cutoff_result = self._call_cutoff_inprocess(
                    payload,
                    calendar_mode=calendar_mode,
                    fiscal_year_start=fiscal_year_start,
                )
            else:
                cutoff_result = self._call_cutoff_http(payload, cutoff_agent_url)
        except requests.exceptions.RequestException as exc:
            logger.warning(
                "cutoff agent unreachable order_no={} err={}",
                request.order.order_no,
                exc,
            )
            reason = "截止性Agent未响应，请检查服务是否启动"
            match_result = match_result.model_copy(
                update={"cutoff_available": False, "cutoff_skipped_reason": reason}
            )
            return self._finalize(
                request,
                match_result,
                overall_status=merge_overall_status(
                    match_result.overall_status, "WARNING"
                ),
                cutoff_result=None,
                cutoff_available=False,
                cutoff_skipped_reason=reason,
                cutoff_error=reason,
            )
        except Exception as exc:
            logger.warning(
                "cutoff call failed order_no={} err={}",
                request.order.order_no,
                exc,
            )
            reason = (
                str(exc)
                if str(exc).startswith("截止性Agent")
                else f"截止性Agent调用失败: {exc}"
            )
            match_result = match_result.model_copy(
                update={"cutoff_available": False, "cutoff_skipped_reason": reason}
            )
            return self._finalize(
                request,
                match_result,
                overall_status=merge_overall_status(
                    match_result.overall_status, "WARNING"
                ),
                cutoff_result=None,
                cutoff_available=False,
                cutoff_skipped_reason=reason,
                cutoff_error=reason,
            )

        overall = merge_overall_status(
            match_result.overall_status, cutoff_result.测试状态
        )
        match_result = match_result.model_copy(
            update={
                "cutoff_available": True,
                "cutoff_skipped_reason": None,
                "cutoff_test_status": cutoff_result.测试状态,
            }
        )
        result = self._finalize(
            request,
            match_result,
            overall_status=overall,
            cutoff_result=cutoff_result,
            cutoff_available=True,
            cutoff_skipped_reason=None,
        )
        logger.info(
            "match_and_cutoff done order_no={} match={} cutoff={} overall={}",
            request.order.order_no,
            match_result.overall_status,
            cutoff_result.测试状态,
            overall,
        )
        return result

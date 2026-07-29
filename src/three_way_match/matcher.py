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
from src.three_way_match.summary import build_human_readable_summary
from src.utils.date_extractor import extract_days_from_description, pick_receipt_date_from_fields
from src.utils.logger import logger

Status = Literal["PASS", "WARNING", "FAIL"]
_STATUS_RANK: dict[str, int] = {"PASS": 0, "WARNING": 1, "FAIL": 2}

SKIP_REASON_MISSING_POSTING = "入账日期缺失，无法执行截止性测试"


def _to_wan_float(value: Any, default: float = 0.0) -> float:
    """解析 OCR 金额并统一为万元（>10000 视为元）。"""
    from src.legacy_ocr.amount_resolve import _parse_number, to_wan_yuan

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
    return to_wan_yuan(num)


def _first_wan_amount(*candidates: Any) -> float:
    for val in candidates:
        amt = _to_wan_float(val, default=0.0)
        if amt > 0:
            return amt
    return 0.0


def _coalesce_three_way_amounts(
    order_amt: float,
    receipt_amt: float,
    invoice_amt: float,
) -> tuple[float, float, float]:
    """
    三单金额对齐：入库/发票未识别时默认与订单一致（同笔业务常见同价）。
    """
    if order_amt > 0:
        if receipt_amt <= 0:
            receipt_amt = order_amt
        if invoice_amt <= 0:
            invoice_amt = order_amt
    elif invoice_amt > 0:
        if order_amt <= 0:
            order_amt = invoice_amt
        if receipt_amt <= 0:
            receipt_amt = invoice_amt
    elif receipt_amt > 0:
        order_amt = receipt_amt
        invoice_amt = receipt_amt
    return order_amt, receipt_amt, invoice_amt


def _to_float(value: Any, default: float = 0.0) -> float:
    """兼容旧调用：解析为万元浮点。"""
    return _to_wan_float(value, default=default)


def _pick_order_no(*candidates: Any) -> str:
    for item in candidates:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            return text
    return "UNKNOWN"


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
    supplier = (
        str(
            order_fields.get("supplierName")
            or receipt_fields.get("supplierName")
            or invoice_fields.get("supplierName")
            or ""
        ).strip()
    )
    receipt_date = str(
        pick_receipt_date_from_fields(receipt_fields)
        or receipt_fields.get("documentDate")
        or receipt_fields.get("deliveryDate")
        or ""
    ).strip() or "1970-01-01"
    posting_raw = invoice_fields.get("postingDate")
    posting_date = str(posting_raw).strip() if posting_raw else None

    order_amt = _first_wan_amount(
        order_fields.get("totalAmount"), order_fields.get("amount")
    )
    receipt_amt = _first_wan_amount(
        receipt_fields.get("totalAmount"), receipt_fields.get("amount")
    )
    invoice_amt = _first_wan_amount(
        invoice_fields.get("totalAmount"), invoice_fields.get("amount")
    )
    order_amt, receipt_amt, invoice_amt = _coalesce_three_way_amounts(
        order_amt, receipt_amt, invoice_amt
    )

    return ThreeWayMatchRequest(
        order=Order(
            order_no=order_no,
            supplier_name=supplier or "未知供应商",
            total_amount=order_amt,
            quantity=_to_wan_float(order_fields.get("quantity")),
            unit=order_fields.get("unit"),
            order_date=order_fields.get("documentDate"),
            payment_terms=order_fields.get("paymentTerms"),
            contract_no=order_fields.get("contractNo"),
        ),
        warehouse_receipt=WarehouseReceipt(
            receipt_no=_pick_order_no(receipt_fields.get("documentNo"), "WR-UNKNOWN"),
            order_no=_pick_order_no(receipt_fields.get("remarks"), order_no),
            supplier_name=str(receipt_fields.get("supplierName") or supplier or "未知供应商"),
            total_amount=receipt_amt,
            quantity=_to_wan_float(receipt_fields.get("quantity")),
            receipt_date=receipt_date,
            receiver=None,
        ),
        invoice=Invoice(
            invoice_no=_pick_order_no(
                invoice_fields.get("invoiceNo"),
                invoice_fields.get("documentNo"),
                "INV-UNKNOWN",
            ),
            order_no=_pick_order_no(invoice_fields.get("remarks"), order_no),
            supplier_name=str(invoice_fields.get("supplierName") or supplier or "未知供应商"),
            total_amount=invoice_amt,
            quantity=_to_wan_float(invoice_fields.get("quantity")),
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
                "three-way match PASS order_no={} score={}",
                result.order_no,
                result.match_score,
            )
        elif result.overall_status == "WARNING":
            logger.warning(
                "three-way match WARNING order_no={} score={} risks={}",
                result.order_no,
                result.match_score,
                result.risks,
            )
        else:
            logger.warning(
                "three-way match FAIL order_no={} score={} risks={}",
                result.order_no,
                result.match_score,
                result.risks,
            )
        return result

    def build_cutoff_payload(self, request: ThreeWayMatchRequest) -> dict[str, Any]:
        """从三单请求组装截止性 Agent 所需 JSON（中文字段）。

        付款账期仍传入并写入底稿，供后续收款等测试；截止公式仅用签收日与入账日。
        """
        payment_terms = request.order.payment_terms
        payment_days = (
            extract_days_from_description(payment_terms) if payment_terms else None
        )
        return {
            "业务编号": request.order.order_no,
            "合同编号": request.order.contract_no,
            "客户名称": request.order.supplier_name,
            "合同账期描述": payment_terms,
            "合同账期天数": payment_days,
            "签收日期": request.warehouse_receipt.receipt_date,
            "入账日期": request.invoice.posting_date,
            "入账金额": float(request.invoice.total_amount),
        }

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

    def _call_cutoff_inprocess(self, payload: dict[str, Any]) -> CutoffResponse:
        """同进程调用；不写底稿（由 match_and_cutoff 统一写完整行）。"""
        from src.api.cutoff_runner import perform_cutoff

        req = CutoffRequest.model_validate(payload)
        return perform_cutoff(req, write_workbook=False)

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
        write_workbook: bool = True,
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
            "cutoff_available": cutoff_available,
            "cutoff_skipped_reason": cutoff_skipped_reason,
            "overall_status": overall_status,
            "cutoff_error": cutoff_error,
            "底稿文件路径": workbook_path,
            "human_readable_summary": match_result.human_readable_summary,
        }

    def match_and_cutoff(
        self,
        request: ThreeWayMatchRequest,
        cutoff_agent_url: str = "http://localhost:8000/api/v1/cutoff",
        *,
        inprocess: bool = False,
    ) -> dict[str, Any]:
        """
        执行三单匹配 + 自动触发截止性测试，并写入完整底稿行。

        - inprocess=True：同进程调用 CutoffRunner（API 路由默认，防死锁）
        - inprocess=False：requests.post 调用 cutoff_agent_url
        - 入账日期（posting_date）缺失时跳过截止性，仍写入三单匹配列
        """
        match_result = self.match(request)

        posting_date = (request.invoice.posting_date or "").strip()
        if not posting_date:
            return self._skip_cutoff_missing_posting(request, match_result)

        payload = self.build_cutoff_payload(request)
        if not (payload.get("入账日期") or "").strip():
            return self._skip_cutoff_missing_posting(request, match_result)

        try:
            if inprocess:
                cutoff_result = self._call_cutoff_inprocess(payload)
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

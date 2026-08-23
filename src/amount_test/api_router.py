"""金额测试 API。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from src.amount_test.calculator import AmountTestResult, run_amount_test
from src.amount_test.models import AmountAccuracyReport, AmountBatchResult, LedgerValues
from src.amount_test.runner import run_amount_accuracy_test, run_amount_batch_from_ledger
from src.api.hitl_gate import (
    enforce_fields_confirmed_header,
    enforce_matching_confirmed_header,
)

router = APIRouter(
    tags=["amount-test"],
    dependencies=[
        Depends(enforce_fields_confirmed_header),
        Depends(enforce_matching_confirmed_header),
    ],
)


class AmountDocIn(BaseModel):
    file_name: str = ""
    doc_type: str
    fields: Dict[str, Any] = Field(default_factory=dict)
    raw_text: str = ""


class AmountTestRequest(BaseModel):
    documents: List[AmountDocIn]
    ledger_amount: Optional[float] = Field(
        default=None, description="序时账金额（元）"
    )
    sales_order_no: str = ""
    voucher_no: str = ""
    customer_name: str = ""


class AmountAccuracyRequest(BaseModel):
    documents: List[AmountDocIn]
    ledger: LedgerValues
    business_id: str = ""
    tolerance: float = 0.02


class AmountBatchRequest(BaseModel):
    ledger_path: str
    vouchers_root: str
    sheet_name: str = "SAP序时账"
    only_sales_orders: Optional[List[str]] = None
    tolerance: float = 0.02


@router.post(
    "/api/v1/amount-test",
    response_model=AmountTestResult,
    summary="金额测试（工作流兼容）",
)
def api_amount_test(request: AmountTestRequest) -> AmountTestResult:
    docs = [
        {
            "file_name": d.file_name,
            "doc_type": d.doc_type,
            "fields": d.fields,
            "raw_text": d.raw_text,
        }
        for d in request.documents
    ]
    return run_amount_test(
        docs,
        ledger_amount=request.ledger_amount,
        sales_order_no=request.sales_order_no,
        voucher_no=request.voucher_no,
        customer_name=request.customer_name,
    )


@router.post(
    "/api/v1/amount-accuracy",
    response_model=AmountAccuracyReport,
    summary="交易金额准确性测试（手册 §12 报告）",
)
def api_amount_accuracy(request: AmountAccuracyRequest) -> AmountAccuracyReport:
    docs = [
        {
            "file_name": d.file_name,
            "doc_type": d.doc_type,
            "fields": d.fields,
            "raw_text": d.raw_text,
        }
        for d in request.documents
    ]
    return run_amount_accuracy_test(
        documents=docs,
        ledger=request.ledger,
        business_id=request.business_id,
        tolerance=request.tolerance,
    )


@router.post(
    "/api/v1/amount-accuracy/batch",
    response_model=AmountBatchResult,
    summary="从序时账批测金额准确性",
)
def api_amount_batch(request: AmountBatchRequest) -> AmountBatchResult:
    return run_amount_batch_from_ledger(
        request.ledger_path,
        request.vouchers_root,
        sheet_name=request.sheet_name,
        only_sales_orders=request.only_sales_orders,
        tolerance=request.tolerance,
    )

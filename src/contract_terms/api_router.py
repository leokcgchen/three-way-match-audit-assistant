"""合同条款测试 API。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from src.api.hitl_gate import enforce_fields_confirmed_header
from src.contract_terms.checker import ContractTermsResult, run_contract_terms_test
from src.contract_terms.models import ContractClarityBatchResult, ContractClarityReport
from src.contract_terms.runner import run_contract_clarity_batch, run_contract_clarity_test

router = APIRouter(
    tags=["contract-terms"],
    dependencies=[Depends(enforce_fields_confirmed_header)],
)


class ContractDocIn(BaseModel):
    file_name: str = ""
    doc_type: str
    fields: Dict[str, Any] = Field(default_factory=dict)
    raw_text: str = ""


class ContractTermsRequest(BaseModel):
    documents: List[ContractDocIn]
    business_id: str = ""
    voucher_no: str = ""
    customer_name: str = ""


class ContractClarityBatchRequest(BaseModel):
    ledger_path: str
    vouchers_root: str
    sheet_name: Union[str, int] = "SAP序时账"
    only_sales_orders: Optional[List[str]] = None


@router.post(
    "/api/v1/contract-terms",
    response_model=ContractTermsResult,
    summary="合同条款清晰性测试",
)
def api_contract_terms(request: ContractTermsRequest) -> ContractTermsResult:
    docs = [
        {
            "file_name": d.file_name,
            "doc_type": d.doc_type,
            "fields": d.fields,
            "raw_text": d.raw_text,
        }
        for d in request.documents
    ]
    return run_contract_terms_test(
        docs,
        business_id=request.business_id,
        voucher_no=request.voucher_no,
        customer_name=request.customer_name,
    )


@router.post(
    "/api/v1/contract-clarity",
    response_model=ContractClarityReport,
    summary="合同条款清晰性测试（手册 §10 报告）",
)
def api_contract_clarity(request: ContractTermsRequest) -> ContractClarityReport:
    docs = [
        {
            "file_name": d.file_name,
            "doc_type": d.doc_type,
            "fields": d.fields,
            "raw_text": d.raw_text,
        }
        for d in request.documents
    ]
    return run_contract_clarity_test(
        documents=docs,
        business_id=request.business_id,
        voucher_no=request.voucher_no,
        customer_name=request.customer_name,
    )


@router.post(
    "/api/v1/contract-clarity/batch",
    response_model=ContractClarityBatchResult,
    summary="从序时账批测合同条款清晰性",
)
def api_contract_clarity_batch(
    request: ContractClarityBatchRequest,
) -> ContractClarityBatchResult:
    return run_contract_clarity_batch(
        request.ledger_path,
        request.vouchers_root,
        sheet_name=request.sheet_name,
        only_sales_orders=request.only_sales_orders,
    )

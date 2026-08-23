"""证据匹配 API。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from src.api.hitl_gate import enforce_fields_confirmed_header
from src.evidence_match.linker import EvidenceMatchResult, build_evidence_chain

router = APIRouter(
    tags=["evidence-match"],
    dependencies=[Depends(enforce_fields_confirmed_header)],
)


class EvidenceDocIn(BaseModel):
    file_name: str = ""
    doc_type: str
    fields: Dict[str, Any] = Field(default_factory=dict)
    raw_text: str = ""


class EvidenceMatchRequest(BaseModel):
    documents: List[EvidenceDocIn]
    ledger_matched_biz_id: Optional[str] = None
    ledger_posting_date: Optional[str] = None
    require_delivery: bool = False
    require_payment: bool = False
    with_llm_disambiguation: bool = False


@router.post(
    "/api/v1/evidence-match",
    response_model=EvidenceMatchResult,
    summary="证据匹配：按业务编号串联合同/订单/发货/签收/发票/回款/序时账",
)
def run_evidence_match(request: EvidenceMatchRequest) -> EvidenceMatchResult:
    classified = [
        {
            "file_name": d.file_name,
            "doc_type": d.doc_type,
            "fields": d.fields,
            "raw_text": getattr(d, "raw_text", "") or "",
        }
        for d in request.documents
    ]
    result = build_evidence_chain(
        classified,
        ledger_matched_biz_id=request.ledger_matched_biz_id,
        ledger_posting_date=request.ledger_posting_date,
        require_delivery=request.require_delivery,
        require_payment=request.require_payment,
    )
    if request.with_llm_disambiguation:
        from src.evidence_match.disambiguation import llm_matching_disambiguation

        payload = result.model_dump()
        result.llm_disambiguation = llm_matching_disambiguation(classified, payload)
    return result

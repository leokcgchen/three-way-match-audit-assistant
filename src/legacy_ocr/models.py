"""老三单 OCR 输出数据模型（精简版）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class OcrDocument(BaseModel):
    """单张单据结构化字段（对齐老系统 ExtractedDocumentFields）。"""

    documentType: str = Field(description="purchase_order / warehouse_receipt / invoice …")
    documentNo: Optional[str] = None
    documentDate: Optional[str] = None
    amount: Optional[str] = None
    taxAmount: Optional[str] = None
    totalAmount: Optional[str] = None
    quantity: Optional[str] = None
    unit: Optional[str] = None
    supplierName: Optional[str] = None
    supplierTaxId: Optional[str] = None
    buyerName: Optional[str] = None
    buyerTaxId: Optional[str] = None
    paymentTerms: Optional[str] = None
    deliveryDate: Optional[str] = None
    acceptanceDate: Optional[str] = Field(
        default=None, description="验收完成/期限届满日（截止性测试优先）"
    )
    receiptDateForCutoff: Optional[str] = Field(
        default=None, description="截止性测试用签收日"
    )
    warehouseNo: Optional[str] = None
    projectName: Optional[str] = None
    remarks: Optional[str] = None
    invoiceCode: Optional[str] = None
    invoiceNo: Optional[str] = None
    contractNo: Optional[str] = None
    postingDate: Optional[str] = Field(
        default=None, description="财务入账日期（可人工/联查补录）"
    )
    items: Optional[List[Dict[str, Any]]] = None

    def to_extracted_dict(self) -> Dict[str, Any]:
        return self.model_dump(exclude_none=True)


class OcrResult(BaseModel):
    """OCR 识别结果。"""

    rawText: str = ""
    extractedFields: Dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    documentType: Optional[str] = None
    source: str = Field(default="unknown", description="paddleocr / heuristic / mock")

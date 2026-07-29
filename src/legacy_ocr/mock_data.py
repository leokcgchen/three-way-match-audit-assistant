"""OCR Mock 数据（无 API Key / 离线调试时使用）。"""

from __future__ import annotations

from typing import Any, Dict


MOCK_BY_TYPE: Dict[str, Dict[str, Any]] = {
    "contract": {
        "documentNo": "HT-MOCK-001",
        "contractNo": "HT-MOCK-001",
        "documentDate": "2026-05-01",
        "supplierName": "甲供应商",
        "buyerName": "乙采购方",
        "paymentTerms": "签收后10日",
        "totalAmount": "500",
    },
    "purchase_order": {
        "documentNo": "PO-MOCK-001",
        "documentDate": "2026-05-20",
        "supplierName": "甲供应商",
        "totalAmount": "500",
        "quantity": "100",
        "paymentTerms": "签收后10日",
        "contractNo": "HT-MOCK-001",
    },
    "warehouse_receipt": {
        "documentNo": "WR-MOCK-001",
        "documentDate": "2026-06-01",
        "deliveryDate": "2026-06-01",
        "supplierName": "甲供应商",
        "totalAmount": "500",
        "quantity": "100",
        "remarks": "PO-MOCK-001",
    },
    "invoice": {
        "documentNo": "INV-MOCK-001",
        "documentDate": "2026-06-08",
        "supplierName": "甲供应商",
        "totalAmount": "500",
        "quantity": "100",
        "invoiceNo": "INV-MOCK-001",
    },
}


def mock_raw_text(document_type: str) -> str:
    fields = MOCK_BY_TYPE.get(document_type, {})
    lines = [f"[{document_type}] MOCK OCR TEXT"]
    for k, v in fields.items():
        lines.append(f"{k}: {v}")
    return "\n".join(lines)


def mock_fields(document_type: str) -> Dict[str, Any]:
    return dict(MOCK_BY_TYPE.get(document_type, {}))

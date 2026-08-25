from __future__ import annotations

from src.models.field_values import get_verified_value, seed_field_meta
from src.workflow.field_resolution.evidence_gate import (
    accept_system_verified_fields,
    evaluate_candidate,
)


def test_unlocated_auto_field_is_not_accepted() -> None:
    doc = {
        "file_name": "scan.pdf",
        "doc_type": "invoice",
        "raw_text": "",
        "fields": {"totalAmount": 113000},
    }
    seed_field_meta(doc, source="ocr")

    accepted = accept_system_verified_fields(doc)

    assert accepted == []
    assert get_verified_value(doc, "totalAmount") is None
    assert doc["_field_meta"]["totalAmount"]["verification_status"] == "UNLOCATED"


def test_receipt_order_number_cannot_verify_as_own_document_number() -> None:
    doc = {
        "file_name": "receipt.pdf",
        "doc_type": "receipt",
        "raw_text": "关联订单号 SO-251209-7214",
        "fields": {"documentNo": "SO-251209-7214"},
    }
    seed_field_meta(doc, source="ocr")

    decision = evaluate_candidate(doc, "documentNo")

    assert decision.status == "ROLE_CONFLICT"
    assert decision.reason_code == "DOCUMENT_NUMBER_LABEL_ROLE_CONFLICT"


def test_positioned_receipt_number_can_be_system_verified() -> None:
    doc = {
        "file_name": "receipt.pdf",
        "doc_type": "receipt",
        "raw_text": "验收单号 YS-260102-005",
        "text_blocks": [
            {
                "text": "YS-260102-005",
                "page": 0,
                "bbox": [100, 20, 180, 36],
                "source": "native_pdf_word",
            }
        ],
        "fields": {"documentNo": "YS-260102-005"},
    }
    seed_field_meta(doc, source="ocr")

    accepted = accept_system_verified_fields(doc)

    assert accepted == ["documentNo"]
    assert get_verified_value(doc, "documentNo") == "YS-260102-005"
    assert doc["_field_meta"]["documentNo"]["verification_status"] == "SYSTEM_VERIFIED"


def test_generic_number_without_field_label_requires_review() -> None:
    doc = {
        "file_name": "order.pdf",
        "doc_type": "order",
        "raw_text": "内部流水第20号；字段尚未标注。",
        "fields": {"quantity": 20},
    }
    seed_field_meta(doc, source="ocr")

    decision = evaluate_candidate(doc, "quantity")

    assert decision.status == "NEEDS_REVIEW"
    assert decision.reason_code == "AMBIGUOUS_TEXT_ONLY_ANCHOR"


def test_invoice_number_with_no_prefix_can_be_system_verified() -> None:
    doc = {
        "file_name": "invoice.pdf",
        "doc_type": "invoice",
        "raw_text": "No FP-260102-8305\n增值税专用发票",
        "text_blocks": [
            {
                "text": "FP-260102-8305",
                "page": 0,
                "bbox": [1, 2, 3, 4],
                "source": "native_pdf_word",
            }
        ],
        "fields": {"invoiceNo": "FP-260102-8305"},
    }
    seed_field_meta(doc, source="ocr")

    decision = evaluate_candidate(doc, "invoiceNo")

    assert decision.status == "SYSTEM_VERIFIED"


def test_aggregate_quantity_is_verified_from_positioned_line_items() -> None:
    doc = {
        "file_name": "order.pdf",
        "doc_type": "order",
        "raw_text": "数量 10\n数量 15\n数量 20",
        "text_blocks": [
            {"text": "10", "page": 0, "bbox": [1, 1, 2, 2], "source": "native_pdf_word"},
            {"text": "15", "page": 0, "bbox": [1, 3, 2, 4], "source": "native_pdf_word"},
            {"text": "20", "page": 0, "bbox": [1, 5, 2, 6], "source": "native_pdf_word"},
        ],
        "fields": {
            "quantity": 45,
            "items": [{"quantity": 10}, {"quantity": 15}, {"quantity": 20}],
        },
    }
    seed_field_meta(doc, source="ocr")

    decision = evaluate_candidate(doc, "quantity")

    assert decision.status == "SYSTEM_VERIFIED"
    assert decision.reason_code == "DERIVED_FROM_VERIFIED_LINE_ITEMS"
    assert len(decision.evidence_ids) == 3

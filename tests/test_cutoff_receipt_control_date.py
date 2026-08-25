from src.rules.cutoff_checker import CutoffChecker
from src.three_way_match.matcher import build_request_from_ocr_fields


def test_cutoff_uses_acceptance_date_before_receipt_document_date() -> None:
    request = build_request_from_ocr_fields(
        {"documentNo": "PO-1", "documentDate": "2025-12-01", "totalAmount": 100, "quantity": 1},
        {
            "documentNo": "RC-1",
            "receiptDateForCutoff": "2025-12-30",
            "acceptanceDate": "2026-01-02",
            "documentDate": "2025-12-30",
            "totalAmount": 100,
            "quantity": 1,
        },
        {"invoiceNo": "INV-1", "documentDate": "2026-01-03", "postingDate": "2025-12-31", "totalAmount": 100, "quantity": 1},
    )

    result = CutoffChecker().check(
        contract_payment_days=None,
        receipt_date=request.warehouse_receipt.receipt_date,
        entry_date=request.invoice.posting_date,
        period_end="2025-12-31",
    )

    assert request.warehouse_receipt.receipt_date == "2026-01-02"
    assert result.test_status == "FAIL"


def test_cutoff_falls_back_to_receipt_document_date_and_passes_when_same_side_of_year_end() -> None:
    request = build_request_from_ocr_fields(
        {"documentNo": "PO-1", "documentDate": "2025-12-01", "totalAmount": 100, "quantity": 1},
        {"documentNo": "RC-1", "documentDate": "2025-12-30", "totalAmount": 100, "quantity": 1},
        {"invoiceNo": "INV-1", "documentDate": "2026-01-03", "postingDate": "2025-12-31", "totalAmount": 100, "quantity": 1},
    )

    result = CutoffChecker().check(
        contract_payment_days=None,
        receipt_date=request.warehouse_receipt.receipt_date,
        entry_date=request.invoice.posting_date,
        period_end="2025-12-31",
    )

    assert request.warehouse_receipt.receipt_date == "2025-12-30"
    assert result.test_status == "PASS"

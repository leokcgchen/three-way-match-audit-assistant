"""签收日/验收日语义解析测试。"""

from __future__ import annotations

from src.utils.date_extractor import (
    pick_receipt_date_from_fields,
    resolve_receipt_dates,
)


def test_resolve_receipt_dates_prefers_acceptance_over_arrival():
    text = (
        "本单同时记录到货事实及合同约定的3日验收期。"
        "2025年12月30日为实物到货日，2026年01月02日为验收完成 / 期限届满日；"
        "两类日期分别保留，不相互替代。"
    )
    result = resolve_receipt_dates(text, payment_terms="3日验收期")
    assert result["deliveryDate"] == "2025-12-30"
    assert result["acceptanceDate"] == "2026-01-02"
    assert result["receiptDateForCutoff"] == "2026-01-02"
    assert result["_receiptDateSource"] == "acceptance_completion"


def test_resolve_receipt_dates_arrival_plus_inspection_period():
    text = "2025年12月30日为实物到货日，合同约定3日验收期。"
    result = resolve_receipt_dates(text)
    assert result["deliveryDate"] == "2025-12-30"
    assert result["receiptDateForCutoff"] == "2026-01-02"
    assert result["_receiptDateSource"] == "arrival_plus_inspection_period"


def test_pick_receipt_date_from_fields_priority():
    fields = {
        "deliveryDate": "2025-12-30",
        "acceptanceDate": "2026-01-02",
        "receiptDateForCutoff": "2026-01-02",
    }
    assert pick_receipt_date_from_fields(fields) == "2026-01-02"


def test_heuristic_receipt_on_user_sample():
    from src.legacy_ocr.ocr_adapter import extract_fields_heuristically

    text = (
        "签收及验收记录\n"
        "记录事项：本单同时记录到货事实及合同约定的3日验收期。"
        "2025年12月30日为实物到货日，2026年01月02日为验收完成 / 期限届满日"
    )
    fields = extract_fields_heuristically(text)
    assert fields.get("receiptDateForCutoff") == "2026-01-02"
    assert fields.get("deliveryDate") == "2025-12-30"
    assert pick_receipt_date_from_fields(fields) == "2026-01-02"


if __name__ == "__main__":
    test_resolve_receipt_dates_prefers_acceptance_over_arrival()
    test_resolve_receipt_dates_arrival_plus_inspection_period()
    test_pick_receipt_date_from_fields_priority()
    test_heuristic_receipt_on_user_sample()
    print("test_date_extractor: PASS")

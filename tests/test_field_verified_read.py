"""规则/底稿只读已确认字段。"""

from __future__ import annotations

from src.models.field_values import (
    accept_field,
    get_verified_value,
    rule_readable_fields,
    seed_field_meta,
    set_candidate,
)


def test_unaccepted_candidate_not_verified():
    item = {"fields": {}}
    seed_field_meta(item, fields={"receiptDate": "2025-01-01"}, source="ocr")
    set_candidate(item, "receiptDate", "2025-12-31", source="llm")
    assert get_verified_value(item, "receiptDate") is None
    assert "receiptDate" not in rule_readable_fields(item)


def test_accepted_value_readable_for_rules():
    item = {"fields": {"receiptDate": "2025-01-01"}}
    seed_field_meta(item, source="ocr")
    accept_field(item, "receiptDate", "2025-01-08", source="manual")
    assert get_verified_value(item, "receiptDate") == "2025-01-08"
    assert rule_readable_fields(item)["receiptDate"] == "2025-01-08"


def test_legacy_without_meta_still_readable():
    item = {"fields": {"orderNo": "SO25-0281"}}
    assert rule_readable_fields(item)["orderNo"] == "SO25-0281"

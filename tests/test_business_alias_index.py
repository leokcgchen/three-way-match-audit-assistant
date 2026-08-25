from __future__ import annotations

from src.workflow.business_alias_index import (
    build_alias_index,
    normalize_alias,
    resolve_document_business,
)


def _population(business_id: str, order_numbers: list[str]) -> dict:
    return {
        "business_ids": [business_id],
        "rows": [
            {
                "business_id": business_id,
                "order_numbers": order_numbers,
            }
        ],
    }


def _two_business_population() -> dict:
    return {
        "business_ids": ["YW-2025-3962", "YW-2025-3971"],
        "rows": [
            {
                "business_id": "YW-2025-3962",
                "order_numbers": ["SO-251209-7214"],
            },
            {
                "business_id": "YW-2025-3971",
                "order_numbers": ["SO-251212-7259"],
            },
        ],
    }


def test_normalize_alias_ignores_case_spaces_hyphens_and_underscores() -> None:
    assert normalize_alias(" so_251209 7214 ") == "SO2512097214"


def test_business_or_unique_order_alias_resolves_same_business() -> None:
    population = _population("YW-2025-3962", ["SO-251209-7214"])

    by_business = resolve_document_business(
        {"file_name": "YW-2025-3962_发票.pdf", "fields": {}}, population
    )
    by_order = resolve_document_business(
        {"file_name": "销售订单_SO-251209-7214.pdf", "fields": {}}, population
    )

    assert by_business["status"] == "MATCHED"
    assert by_order["status"] == "MATCHED"
    assert by_business["business_id"] == by_order["business_id"] == "YW-2025-3962"
    assert by_business["evidence"][0]["type"] == "business_id"
    assert by_order["evidence"][0]["type"] == "order_number"


def test_business_and_order_aliases_for_same_business_are_highest_confidence() -> None:
    result = resolve_document_business(
        {
            "file_name": "YW-2025-3962_销售订单_SO-251209-7214.pdf",
            "fields": {},
        },
        _population("YW-2025-3962", ["SO-251209-7214"]),
    )

    assert result["status"] == "MATCHED"
    assert result["business_id"] == "YW-2025-3962"
    assert result["confidence"] == "highest"
    assert {item["type"] for item in result["evidence"]} == {
        "business_id",
        "order_number",
    }


def test_conflicting_strong_aliases_never_auto_resolve() -> None:
    result = resolve_document_business(
        {
            "file_name": "YW-2025-3962_SO-251212-7259.pdf",
            "fields": {},
        },
        _two_business_population(),
    )

    assert result["status"] == "CONFLICT"
    assert result["business_id"] is None
    assert result["candidate_business_ids"] == ["YW-2025-3962", "YW-2025-3971"]


def test_duplicate_order_alias_never_auto_resolves() -> None:
    population = {
        "business_ids": ["YW-1", "YW-2"],
        "rows": [
            {"business_id": "YW-1", "order_numbers": ["SO-1"]},
            {"business_id": "YW-2", "order_numbers": ["SO-1"]},
        ],
    }

    index = build_alias_index(population)
    result = resolve_document_business(
        {"file_name": "销售订单_SO-1.pdf", "fields": {}}, population
    )

    assert index["aliases"]["SO1"]["business_ids"] == ["YW-1", "YW-2"]
    assert result["status"] == "AMBIGUOUS_ALIAS"
    assert result["business_id"] is None


def test_similar_digits_are_candidates_not_matches() -> None:
    result = resolve_document_business(
        {"file_name": "YW-2025-3992_扫描件.pdf", "fields": {}},
        _population("YW-2025-3962", []),
    )

    assert result["status"] == "SIMILAR_CANDIDATE"
    assert result["business_id"] is None
    assert result["candidate_business_ids"] == ["YW-2025-3962"]


def test_ocr_order_field_is_used_when_filename_has_no_index() -> None:
    result = resolve_document_business(
        {
            "file_name": "签收单扫描件.pdf",
            "fields": {"orderNo": "SO-251209-7214"},
        },
        _population("YW-2025-3962", ["SO-251209-7214"]),
    )

    assert result["status"] == "MATCHED"
    assert result["business_id"] == "YW-2025-3962"
    assert result["evidence"][0]["source"] == "ocr_field:orderNo"


def test_raw_text_exact_order_resolves_when_filename_is_meaningless() -> None:
    result = resolve_document_business(
        {
            "file_name": "扫描件_最终版.pdf",
            "raw_text": "Sales Order No.: SO 251209 7214\nAmount: CNY 113,000.00",
            "fields": {},
        },
        _population("YW-2025-3962", ["SO-251209-7214"]),
    )

    assert result["status"] == "MATCHED"
    assert result["business_id"] == "YW-2025-3962"
    evidence = result["evidence"][0]
    assert evidence["source"] == "raw_text"
    assert evidence["type"] == "order_number"
    assert evidence["char_start"] < evidence["char_end"]


def test_raw_text_invoice_number_is_a_unique_strong_alias() -> None:
    population = _population("YW-2025-3962", ["SO-251209-7214"])
    population["rows"][0]["invoice_numbers"] = ["FP-260102-8305"]

    result = resolve_document_business(
        {
            "file_name": "invoice.pdf",
            "raw_text": "Invoice No. FP_260102_8305",
            "fields": {},
        },
        population,
    )

    assert result["status"] == "MATCHED"
    assert result["business_id"] == "YW-2025-3962"
    assert result["evidence"][0]["type"] == "invoice_number"


def test_one_character_ocr_error_in_raw_text_never_auto_matches() -> None:
    result = resolve_document_business(
        {
            "file_name": "扫描件.pdf",
            "raw_text": "Order No. SO-251209-721S",
            "fields": {},
        },
        _population("YW-2025-3962", ["SO-251209-7214"]),
    )

    assert result["status"] == "SIMILAR_CANDIDATE"
    assert result["business_id"] is None


def test_exact_body_order_is_not_overridden_by_amount_or_date_conflict() -> None:
    population = _population("YW-2025-3962", ["SO-251209-7214"])
    population["rows"][0].update(
        {"book_amount": 113000, "book_date": "2025-12-31", "customer": "甲公司"}
    )

    result = resolve_document_business(
        {
            "file_name": "random.pdf",
            "raw_text": "SO-251209-7214\nAmount 999.00\nDate 2026-02-01",
            "fields": {"totalAmount": 999, "documentDate": "2026-02-01"},
        },
        population,
    )

    assert result["status"] == "MATCHED"
    assert result["business_id"] == "YW-2025-3962"


def test_customer_amount_and_date_are_never_strong_enough_to_auto_assign() -> None:
    population = _two_business_population()
    for row in population["rows"]:
        row.update({"customer": "同一客户", "book_amount": 1000, "book_date": "2025-12-31"})

    result = resolve_document_business(
        {
            "file_name": "random.pdf",
            "raw_text": "同一客户 金额 1,000.00 日期 2025-12-31",
            "fields": {"buyerName": "同一客户", "totalAmount": 1000},
        },
        population,
    )

    assert result["status"] == "UNASSIGNED"
    assert result["business_id"] is None


def test_shorter_identifier_does_not_match_inside_longer_identifier() -> None:
    result = resolve_document_business(
        {"file_name": "random.pdf", "raw_text": "Order SO-1234", "fields": {}},
        _population("YW-1", ["SO-123"]),
    )

    assert result["status"] != "MATCHED"


def test_filename_and_body_pointing_to_different_businesses_is_a_conflict() -> None:
    result = resolve_document_business(
        {
            "file_name": "YW-2025-3962_扫描件.pdf",
            "raw_text": "Sales Order SO-251212-7259",
            "fields": {},
        },
        _two_business_population(),
    )

    assert result["status"] == "CONFLICT"
    assert result["business_id"] is None
    assert result["candidate_business_ids"] == ["YW-2025-3962", "YW-2025-3971"]


def test_short_numeric_business_key_is_not_matched_from_an_amount_in_body() -> None:
    result = resolve_document_business(
        {"file_name": "扫描件.pdf", "raw_text": "数量 1 金额 1.00", "fields": {}},
        {"business_ids": ["1"], "rows": [{"business_id": "1", "order_numbers": []}]},
    )

    assert result["status"] == "UNASSIGNED"


def test_long_numeric_invoice_alias_can_match_body_exactly() -> None:
    population = _population("YW-2025-3962", [])
    population["rows"][0]["invoice_numbers"] = ["25322025000000002811"]

    result = resolve_document_business(
        {
            "file_name": "发票.pdf",
            "raw_text": "发票号码 25322025000000002811",
            "fields": {},
        },
        population,
    )

    assert result["status"] == "MATCHED"
    assert result["business_id"] == "YW-2025-3962"

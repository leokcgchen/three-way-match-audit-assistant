"""一对多纸面履约：所有同组签收/发票必须逐行累计。"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from src.three_way_match.one_to_many import run_one_to_many


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "one_to_many"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_complete_fixture_accumulates_all_three_receipts() -> None:
    """回归：不得再由“最新一张签收=30”替代三张累计数量 100。"""

    pack = _load_fixture("classified_complete.json")

    result = run_one_to_many(pack["classified"])

    row = result["rows"][0]
    assert row["ordered_qty"] == "100"
    assert row["received_qty"] == "100"
    assert row["invoiced_qty"] == "100"
    assert row["light"] == "GREEN"
    assert result["role_files"]["receipt"] == [
        "GRN_POD001.pdf",
        "GRN_POD002.pdf",
        "GRN_POD003.pdf",
    ]


def test_partial_is_yellow_until_auditor_claims_complete() -> None:
    """当前只上传部分发票时应提示补充，不能直接判红。"""

    pack = _load_fixture("classified_partial.json")

    row = run_one_to_many(pack["classified"], complete_set=False)["rows"][0]

    assert row["light"] == "YELLOW"
    assert "PARTIAL_INVOICE" in row["flags"]
    assert "SET_CLAIMED_INCOMPLETE" not in row["flags"]


def test_partial_claimed_complete_is_red() -> None:
    """审计师声明齐套后仍少开票，必须由黄转红。"""

    pack = _load_fixture("classified_partial.json")

    row = run_one_to_many(pack["classified"], complete_set=True)["rows"][0]

    assert row["light"] == "RED"
    assert "PARTIAL_INVOICE" in row["flags"]
    assert "SET_CLAIMED_INCOMPLETE" in row["flags"]


def test_over_receipt_is_red_with_exact_difference() -> None:
    """累计签收超过订单时应显示明确差额并判红。"""

    pack = _load_fixture("classified_complete.json")
    docs = deepcopy(pack["classified"])
    docs[3]["fields"]["items"][0]["quantity"] = "40"

    row = run_one_to_many(docs)["rows"][0]

    assert row["received_qty"] == "110"
    assert row["diffs"]["received_minus_ordered"] == "10"
    assert row["light"] == "RED"
    assert "OVER_RECEIPT" in row["flags"]


def test_duplicate_source_line_is_not_counted_twice() -> None:
    """同一来源行重复进入批次时不得重复累计。"""

    pack = _load_fixture("classified_complete.json")
    docs = deepcopy(pack["classified"])
    docs.insert(2, deepcopy(docs[1]))

    result = run_one_to_many(docs)

    assert result["rows"][0]["received_qty"] == "100"
    assert any(
        allocation["rejected_reason"] == "DUPLICATE_SOURCE_LINE"
        for allocation in result["allocations"]
    )


def test_ambiguous_line_binding_requires_review() -> None:
    """两条订单行均无唯一锚点时，不能贪心选择第一条。"""

    docs = [
        {
            "file_name": "order.pdf",
            "doc_type": "order",
            "fields": {
                "orderNo": "SO-1",
                "items": [
                    {"lineNo": "10", "itemCode": "MAT-1", "quantity": "50"},
                    {"lineNo": "20", "itemCode": "MAT-2", "quantity": "50"},
                ],
            },
        },
        {
            "file_name": "receipt.pdf",
            "doc_type": "receipt",
            "fields": {"orderNo": "SO-1", "items": [{"quantity": "40"}]},
        },
    ]

    result = run_one_to_many(docs)

    [allocation] = result["allocations"]
    assert allocation["order_line_id"] is None
    assert allocation["review_status"] == "REQUIRES_REVIEW"
    assert allocation["bind_status"] == "AMBIGUOUS"
    assert "AMBIGUOUS_LINK" in result["flags"]
    assert result["light"] == "YELLOW"


def test_manual_business_group_overrides_different_order_numbers() -> None:
    """人工框内编号不一致仍保持同组，但差异必须留在可审计结果中。"""

    pack = _load_fixture("classified_complete.json")
    docs = deepcopy(pack["classified"])
    for index, doc in enumerate(docs):
        doc["business_group_id"] = "BOX-9"
        doc["fields"]["orderNo"] = f"SO-SPLIT-{index}"

    result = run_one_to_many(docs, business_group_id="BOX-9")

    assert result["quantity_roles"]["received_qty"] == 100.0
    assert result["role_files"]["receipt"] == [
        "GRN_POD001.pdf",
        "GRN_POD002.pdf",
        "GRN_POD003.pdf",
    ]
    assert "HEADER_REFERENCE_CONFLICT" in result["flags"]


def test_invoice_amount_overage_is_red_even_when_quantities_match() -> None:
    """数量完全一致也不能掩盖累计开票金额超过订单金额。"""

    pack = _load_fixture("classified_complete.json")
    docs = deepcopy(pack["classified"])
    docs[-1]["fields"]["items"][0]["amount"] = "1100"

    result = run_one_to_many(docs)
    row = result["rows"][0]

    assert row["ordered_qty"] == row["invoiced_qty"] == "100"
    assert row["invoiced_amount"] == "1100"
    assert row["diffs"]["invoiced_amount_minus_order"] == "100"
    assert "OVER_INVOICE_AMT" in row["flags"]
    assert row["light"] == "RED"
    assert result["amount_roles"] == {
        "ordered_amount": 1000.0,
        "received_amount": 1000.0,
        "invoiced_amount": 1100.0,
    }


def test_header_order_reference_disambiguates_same_line_number() -> None:
    """同框两张订单行号相同时，应先按凭证头部订单号缩小候选。"""

    docs = [
        {
            "file_name": "order-a.pdf",
            "doc_type": "order",
            "fields": {"orderNo": "SO-A", "items": [{"lineNo": "10", "quantity": "50"}]},
        },
        {
            "file_name": "order-b.pdf",
            "doc_type": "order",
            "fields": {"orderNo": "SO-B", "items": [{"lineNo": "10", "quantity": "40"}]},
        },
        {
            "file_name": "receipt-b.pdf",
            "doc_type": "receipt",
            "fields": {"orderNo": "SO-B", "items": [{"lineNo": "10", "quantity": "40"}]},
        },
    ]

    result = run_one_to_many(docs)
    allocation = result["allocations"][0]

    assert allocation["bind_status"] == "UNIQUE"
    assert allocation["order_line_id"] == "order-b.pdf:10"
    assert "订单号精确一致" in allocation["basis"]


def test_zero_receipt_claimed_complete_is_red() -> None:
    """完全未签收也属于部分履约；一旦声明齐套必须转红。"""

    docs = [{
        "file_name": "order.pdf",
        "doc_type": "order",
        "fields": {"orderNo": "SO-1", "quantity": "100"},
    }]

    result = run_one_to_many(docs, complete_set=True)

    assert result["light"] == "RED"
    assert "PARTIAL_FULFILLMENT" in result["flags"]
    assert "SET_CLAIMED_INCOMPLETE" in result["flags"]


def test_no_order_lines_is_not_treated_as_partial_fulfillment() -> None:
    """没有数量/明细证据时应为未测，不得用 0 制造部分履约。"""

    docs = [
        {"file_name": "order.pdf", "doc_type": "order", "fields": {"orderNo": "SO25-0001"}},
        {"file_name": "receipt.pdf", "doc_type": "receipt", "fields": {"orderNo": "SO25-0001"}},
        {"file_name": "invoice.pdf", "doc_type": "invoice", "fields": {"orderNo": "SO25-0001"}},
    ]

    result = run_one_to_many(docs)

    assert result["rows"] == []
    assert result["light"] == "NOT_TESTED"
    assert result["quantity_roles"] == {}
    assert result["amount_roles"] == {}
    assert "部分履约" not in result["summary"]


def test_corroborating_receipts_for_same_event_are_counted_once() -> None:
    """不同文件证明同一验收事件时保留追溯，但不得重复累计数量。"""

    docs = [
        {
            "file_name": "order.pdf",
            "doc_type": "order",
            "fields": {
                "orderNo": "SO25-0296",
                "documentNo": "SO25-0296",
                "documentDate": "2025-12-10",
                "items": [{"lineNo": "10", "itemCode": "MAT-1", "quantity": "912"}],
            },
        },
        {
            "file_name": "customer-acceptance.pdf",
            "doc_type": "receipt",
            "fields": {
                "documentNo": "YS25-0296",
                "orderNo": "SO25-0296",
                "acceptanceDate": "2025-12-27",
                "items": [{"lineNo": "10", "itemCode": "MAT-1", "quantity": "912"}],
            },
        },
        {
            "file_name": "product-acceptance.pdf",
            "doc_type": "receipt",
            "fields": {
                "documentNo": "YS25-0296",
                "orderNo": "SO25-0296",
                "acceptanceDate": "2025-12-27",
                "items": [{"lineNo": "10", "itemCode": "MAT-1", "quantity": "912"}],
            },
        },
        {
            "file_name": "invoice.pdf",
            "doc_type": "invoice",
            "fields": {
                "invoiceNo": "VAT25-0296",
                "orderNo": "SO25-0296",
                "documentDate": "2025-12-28",
                "items": [{"lineNo": "10", "itemCode": "MAT-1", "quantity": "912"}],
            },
        },
    ]

    result = run_one_to_many(docs)

    assert result["quantity_roles"] == {
        "ordered_qty": 912.0,
        "received_qty": 912.0,
        "invoiced_qty": 912.0,
    }
    assert result["light"] == "GREEN"
    assert result["duplicate_evidence_files"] == [
        {
            "role": "receipt",
            "primary_file": "customer-acceptance.pdf",
            "duplicate_file": "product-acceptance.pdf",
            "document_no": "YS25-0296",
        }
    ]


def test_multiple_invoices_use_gross_amount_before_aggregation() -> None:
    """多张发票必须按同一含税口径加总，不能把未税 amount 当价税合计。"""

    docs = [
        {
            "file_name": "order.pdf",
            "doc_type": "order",
            "fields": {
                "orderNo": "SO25-0100",
                "documentDate": "2025-12-01",
                "items": [{
                    "lineNo": "10", "itemCode": "MAT-1", "quantity": "100",
                    "amount": "1000", "taxAmount": "130", "totalAmount": "1130",
                }],
            },
        },
        {
            "file_name": "receipt.pdf",
            "doc_type": "receipt",
            "fields": {
                "documentNo": "YS25-0100", "orderNo": "SO25-0100",
                "acceptanceDate": "2025-12-10",
                "items": [{"lineNo": "10", "itemCode": "MAT-1", "quantity": "100"}],
            },
        },
        {
            "file_name": "invoice-1.pdf",
            "doc_type": "invoice",
            "fields": {
                "invoiceNo": "VAT25-0101", "orderNo": "SO25-0100",
                "documentDate": "2025-12-11",
                "items": [{
                    "lineNo": "10", "itemCode": "MAT-1", "quantity": "60",
                    "amount": "600", "taxAmount": "78", "totalAmount": "678",
                }],
            },
        },
        {
            "file_name": "invoice-2.pdf",
            "doc_type": "invoice",
            "fields": {
                "invoiceNo": "VAT25-0102", "orderNo": "SO25-0100",
                "documentDate": "2025-12-12",
                "items": [{
                    "lineNo": "10", "itemCode": "MAT-1", "quantity": "40",
                    "amount": "400", "taxAmount": "52", "totalAmount": "452",
                }],
            },
        },
    ]

    result = run_one_to_many(docs)

    assert result["quantity_roles"] == {
        "ordered_qty": 100.0,
        "received_qty": 100.0,
        "invoiced_qty": 100.0,
    }
    assert result["amount_roles"] == {
        "ordered_amount": 1130.0,
        "received_amount": 0.0,
        "invoiced_amount": 1130.0,
    }
    assert result["amount_basis"] == {
        "order": ["gross_total"],
        "receipt": [],
        "invoice": ["gross_total"],
    }
    assert result["light"] == "GREEN"


def test_existing_line_with_missing_quantity_is_unknown_not_zero() -> None:
    docs = [
        {
            "file_name": "order.pdf",
            "doc_type": "order",
            "fields": {"orderNo": "SO-1", "items": [{"model": "SM-1", "quantity": "20"}]},
        },
        {
            "file_name": "receipt.pdf",
            "doc_type": "receipt",
            "fields": {"orderNo": "SO-1", "items": [{"model": "SM-1"}]},
        },
        {
            "file_name": "invoice.pdf",
            "doc_type": "invoice",
            "fields": {"orderNo": "SO-1", "items": [{"model": "SM-1", "quantity": "20"}]},
        },
    ]

    result = run_one_to_many(docs)

    assert result["rows"][0]["received_qty"] is None
    assert "QUANTITY_EVIDENCE_MISSING" in result["rows"][0]["flags"]
    assert result["light"] == "YELLOW"


def test_one_to_many_model_conflict_does_not_fallback_to_unique_order_line() -> None:
    docs = [
        {
            "file_name": "order.pdf",
            "doc_type": "order",
            "fields": {"orderNo": "SO-1", "items": [{"model": "MVC-300", "goodsName": "控制器", "quantity": "2"}]},
        },
        {
            "file_name": "receipt.pdf",
            "doc_type": "receipt",
            "fields": {"orderNo": "SO-1", "items": [{"model": "MC-300", "goodsName": "控制器", "quantity": "2"}]},
        },
    ]

    result = run_one_to_many(docs)

    assert result["allocations"][0]["order_line_id"] is None
    assert "STRONG_MODEL_CONFLICT" in result["flags"]
    assert result["light"] == "YELLOW"

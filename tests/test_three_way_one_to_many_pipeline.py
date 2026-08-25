"""一对多履约必须进入现有三单工作流，而非停留在孤立算法层。"""

from __future__ import annotations

import json
from pathlib import Path

from src.workflow.pipeline import run_three_way
from src.workflow.three_way_persist import three_way_sample_patch


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "one_to_many"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_pipeline_uses_all_receipts_in_the_business_group() -> None:
    """回归：工作流数量角色不得继续使用“最新一张签收=30”。"""

    pack = _load_fixture("classified_complete.json")

    result = run_three_way(pack["classified"])

    assert result["match_result"]["quantity_roles"] == {
        "ordered_qty": 100.0,
        "received_qty": 100.0,
        "invoiced_qty": 100.0,
    }
    assert result["fulfillment"]["light"] == "GREEN"
    assert result["fulfillment"]["role_files"]["receipt"] == [
        "GRN_POD001.pdf",
        "GRN_POD002.pdf",
        "GRN_POD003.pdf",
    ]


def test_pre_scoped_chain_is_not_filtered_empty_by_external_chain_id() -> None:
    """API 已按当前链裁剪单据时，缺少组元数据不得再次把整链筛空。"""

    pack = _load_fixture("classified_complete.json")

    result = run_three_way(
        pack["classified"], business_group_id="SO25-0296"
    )

    assert result["fulfillment"]["rows"]
    assert result["match_result"]["quantity_roles"] == {
        "ordered_qty": 100.0,
        "received_qty": 100.0,
        "invoiced_qty": 100.0,
    }
    assert result["match_result"]["decision"] == "AUTO_PASS"


def test_partial_upload_is_warning_hold_until_more_documents_arrive() -> None:
    """部分开票是“待补充”黄灯，旧金额比较不得把它升级成明确红灯。"""

    pack = _load_fixture("classified_partial.json")

    result = run_three_way(pack["classified"], complete_set=False)

    assert result["fulfillment"]["light"] == "YELLOW"
    assert result["match_result"]["overall_status"] == "WARNING"
    assert result["match_result"]["decision"] == "HOLD_REVIEW"
    assert result["match_result"]["hold_reason_code"] == "PARTIAL_SET"
    assert "当前资料部分开票" in result["match_result"]["decision_reasons"]


def test_partial_claimed_complete_is_fail_hold() -> None:
    """同一部分夹具在审计师声明齐套后必须转成明确红灯。"""

    pack = _load_fixture("classified_partial.json")

    result = run_three_way(pack["classified"], complete_set=True)

    assert result["fulfillment"]["light"] == "RED"
    assert result["match_result"]["overall_status"] == "FAIL"
    assert result["match_result"]["decision"] == "HOLD_REVIEW"
    assert result["match_result"]["hold_reason_code"] == "PARTIAL_SET"
    assert "已声明齐套但资料仍不完整" in result["match_result"]["decision_reasons"]


def test_fulfillment_is_persisted_in_split_three_way_view() -> None:
    """结论页读取的 split view 必须携带一对多分配证据。"""

    pack = _load_fixture("classified_complete.json")
    result = run_three_way(pack["classified"])

    patch = three_way_sample_patch(result)

    fulfillment = patch["three_way_match"]["fulfillment"]
    assert fulfillment["light"] == "GREEN"
    assert fulfillment["rows"][0]["received_qty"] == "100"
    assert len(fulfillment["allocations"]) == 4


def test_empty_fulfillment_does_not_downgrade_scalar_match() -> None:
    """客户和订单↔发票金额已通过时，空履约累计不得制造 PARTIAL_SET。"""

    docs = [
        {
            "file_name": "order.pdf",
            "doc_type": "order",
            "fields": {
                "orderNo": "SO25-0001",
                "remarks": "SO25-0001",
                "buyerName": "客户甲",
                "totalAmount": "100",
            },
        },
        {
            "file_name": "receipt.pdf",
            "doc_type": "receipt",
            "fields": {
                "documentNo": "YS25-0001",
                "orderNo": "SO25-0001",
                "remarks": "SO25-0001",
                "buyerName": "客户甲",
                "acceptanceDate": "2025-12-20",
            },
        },
        {
            "file_name": "invoice.pdf",
            "doc_type": "invoice",
            "fields": {
                "invoiceNo": "VAT25-0001",
                "orderNo": "SO25-0001",
                "remarks": "SO25-0001",
                "buyerName": "客户甲",
                "totalAmount": "100",
                "postingDate": "2025-12-20",
            },
        },
    ]

    result = run_three_way(docs, period_end="2025-12-31")

    assert result["fulfillment"]["light"] == "NOT_TESTED"
    assert result["match_result"]["decision"] == "AUTO_PASS"
    assert result["match_result"]["hold_reason_code"] is None
    assert not any(
        "部分履约" in reason
        for reason in result["match_result"]["decision_reasons"]
    )


def test_unique_qualified_contract_can_be_the_order_anchor() -> None:
    """无订单时，唯一且经济字段充分、跨单据引用一致的合同可作替代锚点。"""

    docs = [
        {
            "file_name": "contract.pdf",
            "doc_type": "contract",
            "fields": {
                "contractNo": "HT25-0001", "documentNo": "HT25-0001",
                "documentDate": "2025-12-01", "buyerName": "客户甲",
                "items": [{
                    "lineNo": "10", "itemCode": "MAT-1", "quantity": "100",
                    "totalAmount": "1130",
                }],
            },
        },
        {
            "file_name": "receipt.pdf",
            "doc_type": "receipt",
            "fields": {
                "documentNo": "YS25-0001", "contractNo": "HT25-0001",
                "remarks": "合同 HT25-0001", "buyerName": "客户甲",
                "acceptanceDate": "2025-12-20",
                "items": [{"lineNo": "10", "itemCode": "MAT-1", "quantity": "100"}],
            },
        },
        {
            "file_name": "invoice.pdf",
            "doc_type": "invoice",
            "fields": {
                "invoiceNo": "VAT25-0001", "contractNo": "HT25-0001",
                "remarks": "合同 HT25-0001", "buyerName": "客户甲",
                "documentDate": "2025-12-21", "postingDate": "2025-12-21",
                "items": [{
                    "lineNo": "10", "itemCode": "MAT-1", "quantity": "100",
                    "totalAmount": "1130",
                }],
            },
        },
    ]

    result = run_three_way(docs, period_end="2025-12-31")

    assert result["anchor_source"] == "CONTRACT_AS_ORDER_ANCHOR"
    assert result["match_result"]["decision"] == "AUTO_PASS"
    assert result["fulfillment"]["quantity_roles"] == {
        "ordered_qty": 100.0,
        "received_qty": 100.0,
        "invoiced_qty": 100.0,
    }
    assert result["document_binding"]["status"] == "PASS"


def test_contract_without_economic_fields_cannot_replace_an_order() -> None:
    """合同缺少可复算数量和金额时仍应要求订单或人工复核。"""

    docs = [
        {
            "file_name": "contract.pdf", "doc_type": "contract",
            "fields": {"contractNo": "HT25-0001", "buyerName": "客户甲"},
        },
        {
            "file_name": "receipt.pdf", "doc_type": "receipt",
            "fields": {"contractNo": "HT25-0001", "quantity": "100"},
        },
        {
            "file_name": "invoice.pdf", "doc_type": "invoice",
            "fields": {"contractNo": "HT25-0001", "quantity": "100", "totalAmount": "1130"},
        },
    ]

    result = run_three_way(docs)

    assert result["decision"] == "HOLD_REVIEW"
    assert result["document_binding"]["reason_code"] == "REQUIRED_DOCUMENT_MISSING"
    assert "anchor_source" not in result


def _two_invoice_chain(*, second_customer: str = "客户甲", second_posting: str = "2025-12-30") -> list[dict]:
    return [
        {
            "file_name": "order.pdf", "doc_type": "order",
            "fields": {
                "orderNo": "SO25-0100", "documentNo": "SO25-0100",
                "buyerName": "客户甲", "documentDate": "2025-12-01",
                "items": [{
                    "lineNo": "10", "itemCode": "MAT-1", "quantity": "100",
                    "totalAmount": "1130",
                }],
            },
        },
        {
            "file_name": "receipt.pdf", "doc_type": "receipt",
            "fields": {
                "orderNo": "SO25-0100", "documentNo": "YS25-0100",
                "buyerName": "客户甲", "acceptanceDate": "2025-12-30",
                "items": [{"lineNo": "10", "itemCode": "MAT-1", "quantity": "100"}],
            },
        },
        {
            "file_name": "invoice-1.pdf", "doc_type": "invoice",
            "fields": {
                "orderNo": "SO25-0100", "invoiceNo": "VAT25-0100-1",
                "buyerName": "客户甲", "documentDate": "2025-12-30",
                "postingDate": "2025-12-30",
                "items": [{
                    "lineNo": "10", "itemCode": "MAT-1", "quantity": "50",
                    "amount": "500", "taxAmount": "65",
                }],
            },
        },
        {
            "file_name": "invoice-2.pdf", "doc_type": "invoice",
            "fields": {
                "orderNo": "SO25-0100", "invoiceNo": "VAT25-0100-2",
                "buyerName": second_customer, "documentDate": "2025-12-31",
                "postingDate": second_posting,
                "items": [{
                    "lineNo": "10", "itemCode": "MAT-1", "quantity": "50",
                    "amount": "500", "taxAmount": "65",
                }],
            },
        },
    ]


def test_each_invoice_customer_is_checked_before_aggregate_auto_pass() -> None:
    """总额相等不能掩盖其中一张发票购方错误。"""

    result = run_three_way(
        _two_invoice_chain(second_customer="客户乙"),
        period_end="2025-12-31",
    )

    assert result["fulfillment"]["amount_roles"] == {
        "ordered_amount": 1130.0,
        "received_amount": 0.0,
        "invoiced_amount": 1130.0,
    }
    assert [item["customer_status"] for item in result["invoice_checks"]] == [
        "PASS", "FAIL",
    ]
    assert result["decision"] == "HOLD_REVIEW"
    assert result["hold_reason_code"] == "PAPER_FIELD"


def test_each_invoice_cutoff_is_retained_and_worst_status_is_aggregated() -> None:
    """两张发票分别跑截止性；一张跨期时，三单仍可通过而截止性单独失败。"""

    result = run_three_way(
        _two_invoice_chain(second_posting="2026-01-02"),
        period_end="2025-12-31",
    )

    assert result["match_result"]["decision"] == "AUTO_PASS"
    assert result["decision"] == "AUTO_PASS"
    assert [item["cutoff_status"] for item in result["invoice_checks"]] == [
        "PASS", "FAIL",
    ]
    assert result["invoice_cutoff_status"] == "FAIL"
    assert result["cutoff_status"] == "FAIL"

    patch = three_way_sample_patch(result)
    assert len(patch["three_way_match"]["invoice_checks"]) == 2
    assert patch["cutoff_test"]["invoice_checks"][1]["cutoff_status"] == "FAIL"


def test_ledger_posting_date_metadata_drives_cutoff_when_field_is_absent() -> None:
    """序时账回写的权威入账日必须进入截止性，不得因 fields 中无 postingDate 而跳过。"""
    docs = [
        {
            "file_name": "order.pdf",
            "doc_type": "order",
            "fields": {
                "orderNo": "SO25-0200",
                "buyerName": "客户甲",
                "quantity": "1",
                "totalAmount": "100",
            },
        },
        {
            "file_name": "receipt.pdf",
            "doc_type": "receipt",
            "fields": {
                "orderNo": "SO25-0200",
                "buyerName": "客户甲",
                "acceptanceDate": "2025-12-30",
                "quantity": "1",
                "totalAmount": "100",
            },
        },
        {
            "file_name": "invoice.pdf",
            "doc_type": "invoice",
            "ledger_posting_date": "2026-01-02",
            "ledger_matched_biz_id": "YW-2025-0200",
            "fields": {
                "orderNo": "SO25-0200",
                "invoiceNo": "FP25-0200",
                "buyerName": "客户甲",
                "documentDate": "2025-12-30",
                "quantity": "1",
                "totalAmount": "100",
            },
        },
    ]

    result = run_three_way(docs, period_end="2025-12-31")

    assert result["invoice_checks"][0]["cutoff_status"] == "FAIL"
    assert result["invoice_cutoff_status"] == "FAIL"

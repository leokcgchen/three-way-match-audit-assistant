"""Phase J：金额提取与截止性跳过测试。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.legacy_ocr.amount_resolve import resolve_total_amount_wan, to_wan_yuan
from src.three_way_match import ThreeWayMatcher, ThreeWayMatchRequest
from src.three_way_match.engine import run_match
from src.three_way_match.models import Invoice, Order, WarehouseReceipt
from src.utils.supplier_normalize import suppliers_are_consistent
from tests.test_integration import _sample_request


def test_yuan_to_wan_conversion():
    assert to_wan_yuan(10942.90) == 1.09429
    assert to_wan_yuan(500.0) == 500.0


def test_resolve_total_from_tax_inclusive_label():
    fields = {"totalAmount": "10942.90"}
    amount, rule = resolve_total_amount_wan(fields, "价税合计：10942.90")
    assert amount == 1.09429
    assert rule is not None


def test_resolve_total_from_net_and_tax_rate():
    fields = {"amount": "9683.98", "taxRate": "13"}
    amount, rule = resolve_total_amount_wan(fields, "税率13%")
    assert amount is not None
    assert abs(amount - to_wan_yuan(9683.98 * 1.13)) < 0.001
    assert rule == "calc:未税×(1+税率)"


def test_resolve_total_from_qty_unit_price():
    fields = {
        "quantity": "357",
        "unitPrice": "27.40",
        "items": [{"quantity": "357", "unitPrice": "27.40", "amount": "9781.80"}],
    }
    amount, rule = resolve_total_amount_wan(fields, "")
    assert amount is not None
    assert rule.startswith("calc:")


def test_resolve_missing_amount():
    amount, rule = resolve_total_amount_wan({}, "")
    assert amount is None
    assert rule is None


def test_supplier_strip_parenthetical():
    a = "华曜汽车零部件制造有限公司"
    b = "华曜汽车零部件制造有限公司（91320594MA7X8Q2N6L）"
    assert suppliers_are_consistent(a, b, a)


def test_cutoff_skipped_status():
    matcher = ThreeWayMatcher()
    req = _sample_request()
    req.invoice.posting_date = None
    req.invoice.invoice_date = "2026-06-08"
    result = matcher.match_and_cutoff(req, inprocess=True)
    assert result["cutoff_available"] is False
    assert result["match_result"].cutoff_test_status == "SKIPPED"
    assert result["cutoff_skipped_reason"] == "入账日期缺失，无法执行截止性测试"


def test_no_invoice_date_fallback_for_posting():
    """开票日不能替代入账日触发截止性。"""
    from src.three_way_match.matcher import build_request_from_ocr_fields

    req = build_request_from_ocr_fields(
        {"documentNo": "PO1", "supplierName": "甲", "totalAmount": "500"},
        {"documentNo": "WR1", "documentDate": "2026-06-01", "totalAmount": "500"},
        {"documentNo": "INV1", "documentDate": "2026-06-08", "totalAmount": "500"},
    )
    assert req.invoice.posting_date is None


if __name__ == "__main__":
    test_yuan_to_wan_conversion()
    test_resolve_total_from_tax_inclusive_label()
    test_resolve_total_from_net_and_tax_rate()
    test_resolve_total_from_qty_unit_price()
    test_resolve_missing_amount()
    test_supplier_strip_parenthetical()
    test_cutoff_skipped_status()
    test_no_invoice_date_fallback_for_posting()
    print("Phase J tests: PASS")

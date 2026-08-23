"""三单匹配适配器单元测试。"""

from __future__ import annotations

from src.three_way_match import (
    Invoice,
    Order,
    ThreeWayMatcher,
    ThreeWayMatchRequest,
    WarehouseReceipt,
)


def _base_order(**kwargs) -> Order:
    data = dict(
        order_no="PO-001",
        supplier_name="甲供应商",
        total_amount=500.0,
        quantity=100.0,
        unit="吨",
        order_date="2026-06-01",
        payment_terms="票到30天",
        contract_no="HT-001",
    )
    data.update(kwargs)
    return Order(**data)


def _base_receipt(**kwargs) -> WarehouseReceipt:
    data = dict(
        receipt_no="WR-001",
        order_no="PO-001",
        supplier_name="甲供应商",
        total_amount=500.0,
        quantity=100.0,
        receipt_date="2026-06-05",
        receiver="张三",
    )
    data.update(kwargs)
    return WarehouseReceipt(**data)


def _base_invoice(**kwargs) -> Invoice:
    data = dict(
        invoice_no="INV-001",
        order_no="PO-001",
        supplier_name="甲供应商",
        total_amount=500.0,
        quantity=100.0,
        invoice_date="2026-06-08",
        posting_date="2026-06-11",
    )
    data.update(kwargs)
    return Invoice(**data)


def _run(order: Order, receipt: WarehouseReceipt, invoice: Invoice):
    matcher = ThreeWayMatcher()
    return matcher.match(
        ThreeWayMatchRequest(
            order=order, warehouse_receipt=receipt, invoice=invoice
        )
    )


def test_amount_deviation_warning():
    """订单500 / 入库490 / 发票495：金额在±2%内但非完全一致 → PASS_WITH_WARNING。"""
    result = _run(
        _base_order(total_amount=500.0),
        _base_receipt(total_amount=490.0),
        _base_invoice(total_amount=495.0),
    )
    assert result.overall_status == "WARNING"
    assert result.decision == "PASS_WITH_WARNING"
    assert result.hold_reason_code is None
    amount = next(c for c in result.comparisons if c.field_name == "total_amount")
    assert amount.is_consistent is True
    assert amount.auditor_explain


def test_perfect_match_pass():
    result = _run(_base_order(), _base_receipt(), _base_invoice())
    assert result.overall_status == "PASS"
    assert result.decision == "AUTO_PASS"
    assert result.hold_reason_code is None
    assert all(c.is_consistent for c in result.comparisons)
    assert result.quantity_roles == {
        "ordered_qty": 100.0,
        "received_qty": 100.0,
        "invoiced_qty": 100.0,
    }
    assert "total_amount" in result.slot_reasons


def test_supplier_mismatch_hold_not_score():
    result = _run(
        _base_order(supplier_name="甲供应商"),
        _base_receipt(supplier_name="乙供应商"),
        _base_invoice(supplier_name="甲供应商"),
    )
    assert result.overall_status == "FAIL"
    assert result.decision == "HOLD_REVIEW"
    assert result.hold_reason_code == "PAPER_FIELD"
    assert any("名称" in r for r in result.decision_reasons)
    supplier = next(c for c in result.comparisons if c.field_name == "supplier_name")
    assert supplier.is_consistent is False
    assert any(("供应商" in r) or ("客户" in r) for r in result.risks)


def test_quantity_large_deviation_fail():
    """订单100 / 入库80 / 发票100：数量超出±1% → HOLD_REVIEW。"""
    result = _run(
        _base_order(quantity=100.0),
        _base_receipt(quantity=80.0),
        _base_invoice(quantity=100.0),
    )
    assert result.overall_status == "FAIL"
    assert result.decision == "HOLD_REVIEW"
    assert result.hold_reason_code == "PAPER_FIELD"
    qty = next(c for c in result.comparisons if c.field_name == "quantity")
    assert qty.is_consistent is False


def test_receipt_missing_amount_uses_order_invoice_only():
    """签收无金额（0）：不因 0 vs 订单金额虚报 FAIL；订单=发票则金额维通过。"""
    result = _run(
        _base_order(total_amount=52904.58),
        _base_receipt(total_amount=0.0),
        _base_invoice(total_amount=52904.58),
    )
    assert result.overall_status == "PASS"
    amount = next(c for c in result.comparisons if c.field_name == "total_amount")
    assert amount.is_consistent is True
    assert "签收" in (amount.diff_description or "")
    assert "未测" in (amount.diff_description or "") or "不适用" in (
        amount.diff_description or ""
    )


def test_receipt_missing_amount_but_order_invoice_mismatch_still_fail():
    result = _run(
        _base_order(total_amount=500.0),
        _base_receipt(total_amount=0.0),
        _base_invoice(total_amount=400.0),
    )
    assert result.overall_status == "FAIL"
    amount = next(c for c in result.comparisons if c.field_name == "total_amount")
    assert amount.is_consistent is False


def test_different_document_dates_still_pass():
    """订单日/开票日/签收日/入账日不同，不作为三单失败。"""
    result = _run(
        _base_order(order_date="2026-06-01"),
        _base_receipt(receipt_date="2026-06-20"),
        _base_invoice(invoice_date="2026-07-01", posting_date="2026-07-08"),
    )
    assert result.overall_status == "PASS"
    assert not any("日期" in (c.field_name or "") for c in result.comparisons)


def test_invoice_quantity_zero_not_fail_when_order_receipt_match():
    result = _run(
        _base_order(quantity=100.0),
        _base_receipt(quantity=100.0),
        _base_invoice(quantity=0.0),
    )
    assert result.overall_status == "PASS"
    qty = next(c for c in result.comparisons if c.field_name == "quantity")
    assert qty.is_consistent is True
    assert "未测" in (qty.diff_description or "")


if __name__ == "__main__":
    test_perfect_match_pass()
    print("test_perfect_match_pass: PASS")
    test_amount_deviation_warning()
    print("test_amount_deviation_warning: PASS")
    test_supplier_mismatch_fail()
    print("test_supplier_mismatch_fail: PASS")
    test_quantity_large_deviation_fail()
    print("test_quantity_large_deviation_fail: PASS")
    test_receipt_missing_amount_uses_order_invoice_only()
    print("test_receipt_missing_amount_uses_order_invoice_only: PASS")
    test_receipt_missing_amount_but_order_invoice_mismatch_still_fail()
    print("test_receipt_missing_amount_but_order_invoice_mismatch_still_fail: PASS")
    print("全部测试通过：three_way_match 正常。")

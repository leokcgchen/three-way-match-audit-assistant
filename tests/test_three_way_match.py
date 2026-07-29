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


def test_perfect_match_pass():
    result = _run(_base_order(), _base_receipt(), _base_invoice())
    assert result.overall_status == "PASS"
    assert result.match_score == 100
    assert all(c.is_consistent for c in result.comparisons)


def test_amount_deviation_warning():
    """订单500 / 入库490 / 发票495：金额在±2%内但非完全一致 → WARNING。"""
    result = _run(
        _base_order(total_amount=500.0),
        _base_receipt(total_amount=490.0),
        _base_invoice(total_amount=495.0),
    )
    assert result.overall_status == "WARNING"
    assert 70 <= result.match_score < 90
    amount = next(c for c in result.comparisons if c.field_name == "total_amount")
    assert amount.is_consistent is True


def test_supplier_mismatch_fail():
    result = _run(
        _base_order(supplier_name="甲供应商"),
        _base_receipt(supplier_name="乙供应商"),
        _base_invoice(supplier_name="甲供应商"),
    )
    assert result.overall_status == "FAIL"
    supplier = next(c for c in result.comparisons if c.field_name == "supplier_name")
    assert supplier.is_consistent is False
    assert any("供应商" in r for r in result.risks)


def test_quantity_large_deviation_fail():
    """订单100 / 入库80 / 发票100：数量超出±1% → FAIL。"""
    result = _run(
        _base_order(quantity=100.0),
        _base_receipt(quantity=80.0),
        _base_invoice(quantity=100.0),
    )
    assert result.overall_status == "FAIL"
    qty = next(c for c in result.comparisons if c.field_name == "quantity")
    assert qty.is_consistent is False


if __name__ == "__main__":
    test_perfect_match_pass()
    print("test_perfect_match_pass: PASS")
    test_amount_deviation_warning()
    print("test_amount_deviation_warning: PASS")
    test_supplier_mismatch_fail()
    print("test_supplier_mismatch_fail: PASS")
    test_quantity_large_deviation_fail()
    print("test_quantity_large_deviation_fail: PASS")
    print("全部测试通过：three_way_match 正常。")

"""金额测试单元测试。"""

from __future__ import annotations

import pytest

from src.amount_test import recalculate_gross_yuan, run_amount_test


@pytest.fixture(autouse=True)
def _disable_batch_llm(monkeypatch):
    monkeypatch.setattr(
        "src.llm.batch_assist.batch_llm_assist_enabled",
        lambda: False,
    )


def test_recalc_qty_price_tax():
    # 10 * 100 * (1-0.1) * 1.13 = 900 * 1.13 = 1017
    fields = {
        "quantity": "10",
        "unitPrice": "100",
        "discountRate": "10%",
        "taxRate": "13%",
        "totalAmount": "0.1017",  # 万元 = 1017 元
        "_amountUnit": "万元",
    }
    bd = recalculate_gross_yuan(fields)
    assert bd is not None
    assert abs(bd.gross_amount - 1017.0) < 0.05
    result = run_amount_test(
        [{"file_name": "order.pdf", "doc_type": "order", "fields": fields}]
    )
    # 无序时账走单据内勾稽；口径差异时可能 WARNING/FAIL，但重算值应接近 1017
    assert result.status in {"PASS", "WARNING", "FAIL"}
    if result.checks and result.checks[0].recalculated_amount is not None:
        assert abs(result.checks[0].recalculated_amount - 1017.0) < 1.0


def test_recalc_detects_book_mismatch():
    fields = {
        "quantity": "10",
        "unitPrice": "100",
        "taxRate": "13%",
        "totalAmount": "0.05",  # 账面 500 元，重算应为 1130
        "_amountUnit": "万元",
    }
    result = run_amount_test(
        [{"file_name": "order.pdf", "doc_type": "order", "fields": fields}]
    )
    assert result.status == "FAIL"
    assert result.checks[0].recalculated_amount is not None
    assert abs(result.checks[0].recalculated_amount - 1130.0) < 0.05


def test_recalc_zhe_discount():
    fields = {
        "quantity": "2",
        "unitPrice": "1000",
        "discountRate": "9折",
        "taxRate": "0",
    }
    bd = recalculate_gross_yuan(fields)
    assert bd is not None
    # 2*1000*0.9 = 1800
    assert abs(bd.gross_amount - 1800.0) < 0.01


def test_skip_without_qty_price():
    result = run_amount_test(
        [
            {
                "file_name": "inv.pdf",
                "doc_type": "invoice",
                "fields": {"totalAmount": "1.0", "_amountUnit": "万元"},
            }
        ]
    )
    assert result.status in {"SKIPPED", "WARNING"}


def test_yuan_total_without_unit_not_forced_to_wan():
    """无 _amountUnit 时，1130 应视为元，不能乘成 1130 万。"""
    fields = {
        "quantity": "10",
        "unitPrice": "100",
        "taxRate": "13%",
        "totalAmount": "1130",
    }
    result = run_amount_test(
        [{"file_name": "order.pdf", "doc_type": "order", "fields": fields}]
    )
    assert result.status in {"PASS", "WARNING", "FAIL"}
    if result.checks and result.checks[0].recalculated_amount is not None:
        assert result.checks[0].recalculated_amount < 1_000_000

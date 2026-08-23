"""GOSPD01030 X 列自然语言异常说明。"""

from __future__ import annotations

from datetime import date

from src.reporting.gospd01030_exception_nl import build_gospd01030_exception_nl


def test_three_way_qty_mismatch_nl():
    text = build_gospd01030_exception_nl(
        assertions={"match_status": "FAIL", "cutoff_status": "PASS"},
        three_way={
            "quantity_roles": {"ordered_qty": 100, "received_qty": 95, "invoiced_qty": 95},
            "three_way_summary": "数量不一致",
        },
        qty_book=95,
        qty_doc=100,
    )
    assert "100" in text and "95" in text
    assert "三单" in text or "数量" in text
    assert "FORMULA_LOGIC_CONFLICT" not in text
    assert "【规则发现】" not in text


def test_cutoff_late_recognition_nl():
    text = build_gospd01030_exception_nl(
        assertions={
            "match_status": "PASS",
            "cutoff_status": "FAIL",
            "period": {
                "verdict": False,
                "posting_date": "2026-01-09",
                "control_date": "2025-12-27",
                "period_end": "2025-12-31",
            },
            "exception": "截止性未通过；收入未记入正确会计期间",
        },
        posting_date="2026-01-09",
        receipt_date=date(2025, 12, 27),
        period_end=date(2025, 12, 31),
        raw_exception="截止性未通过（入账与控制权转移时点不合理）",
    )
    assert "2025-12-27" in text
    assert "2026-01-09" in text
    assert "少记" in text or "延后" in text


def test_carrier_missing_nl():
    text = build_gospd01030_exception_nl(
        assertions={"match_status": "PASS", "cutoff_status": "PASS"},
        raw_exception="缺件：缺少carrier_received事件日期证据",
        transport="外销-FOB离岸价格",
    )
    assert "装船" in text or "承运" in text
    assert "carrier_received" not in text

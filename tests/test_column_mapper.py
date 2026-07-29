"""column_mapper 智能列映射测试。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.legacy_ocr.ledger_parser import load_ledger_file, resolve_ledger_column_mapping
from src.utils.column_mapper import auto_map, column_to_field_score, similarity_score


def test_sap_ledger_auto_map():
    path = ROOT / "data" / "mock" / "示例_SAP_序时账_含合同索引号.csv"
    df = load_ledger_file(path)
    mapped = auto_map(list(df.columns))
    assert mapped.get("业务编号") == "销售订单号"
    assert mapped.get("入账日期") == "过账日期"
    assert mapped.get("金额") == "借方金额"


def test_resolve_ledger_mapping():
    path = ROOT / "data" / "mock" / "示例_SAP_序时账_含合同索引号.csv"
    df = load_ledger_file(path)
    ledger_map, standard_map, ok = resolve_ledger_column_mapping(list(df.columns))
    assert ok is True
    assert ledger_map["posting_date"] == "过账日期"
    assert ledger_map["biz_id"] == "销售订单号"
    assert ledger_map["amount"] == "借方金额"
    assert standard_map["入账日期"] == "过账日期"


def test_similarity_exact_and_contains():
    assert similarity_score("过账日期", "过账日期") >= 0.9
    assert similarity_score("销售订单号", "订单号") >= 0.3
    assert column_to_field_score("销售订单号", "业务编号") >= 0.6


def test_unknown_columns_empty_or_partial():
    mapped = auto_map(["列A", "列B", "无关字段"])
    assert "入账日期" not in mapped


if __name__ == "__main__":
    test_sap_ledger_auto_map()
    test_resolve_ledger_mapping()
    test_similarity_exact_and_contains()
    test_unknown_columns_empty_or_partial()
    print("column_mapper tests: PASS")

"""字段别名 / 签收数量表头解析回归。"""

from __future__ import annotations

from src.legacy_ocr.field_aliases import (
    coalesce_field_aliases,
    enrich_fields_from_text_aliases,
    parse_quantity_from_delivery_table,
    pick_quantity_value,
)
from src.legacy_ocr.field_normalize import normalize_extracted_fields
from src.legacy_ocr.ocr_adapter import extract_fields_heuristically
from src.three_way_match.matcher import build_request_from_ocr_fields
from src.three_way_match.summary import _qty_diff_phrase


SO25_0018_RECEIPT = """# 客户签收单

| 单据编号 | 销售订单号 | 收货单位 | 到货日期 | 签收/验收完成日期 |
|---|---|---|---|---|
| QS25-0018 | SO25-0018 | 华南某新能源汽车有限公司 | 2025-01-24 | 2025-01-24 |

| 物料编码 | 商品名称 | 单位 | 发运数量 | 实收数量 | 合格数量 | 差异数量 |
|---|---|---|---|---|---|---|
| MAT-01306 | 热管理阀体总成 | 件/PCS | 48 | 48 | 48 | 0 |
"""


def test_parse_fayun_shishou_table():
    assert parse_quantity_from_delivery_table(SO25_0018_RECEIPT) == 48.0


def test_heuristic_extracts_receipt_qty():
    fields = extract_fields_heuristically(SO25_0018_RECEIPT)
    assert float(fields.get("quantity") or 0) == 48.0


def test_normalize_alias_keys():
    fields, repairs = normalize_extracted_fields(
        {"实收数量": "48", "收货单位": "甲公司"},
        SO25_0018_RECEIPT,
    )
    assert float(fields["quantity"]) == 48.0
    assert fields.get("buyerName") == "甲公司"
    assert any(r.get("rule") in {"alias_quantity", "table_alias_quantity", "alias_buyer"} for r in repairs)


def test_three_way_reads_alias_qty():
    req = build_request_from_ocr_fields(
        {"quantity": 48, "totalAmount": 100, "supplierName": "卖方", "documentNo": "SO1"},
        {"发运数量": 48, "documentNo": "QS1"},  # 无 quantity 键
        {"quantity": 48, "totalAmount": 100, "documentNo": "FP1"},
    )
    assert req.warehouse_receipt.quantity == 48.0


def test_qty_diff_phrase_missing_not_100():
    text = _qty_diff_phrase(48, 0, 48)
    assert "100%" not in text
    assert "缺项" in text or "为零" in text


def test_coalesce_delivered_quantity():
    out = coalesce_field_aliases({"deliveredQuantity": 12})
    assert float(out["quantity"]) == 12.0
    assert pick_quantity_value({"合格数量": "9"}) == 9.0
    assert enrich_fields_from_text_aliases({}, SO25_0018_RECEIPT)["quantity"] in {48, "48", 48.0}

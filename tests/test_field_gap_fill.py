"""字段缺失检测与启发式金额/数量抽取。"""

from __future__ import annotations

from src.legacy_ocr.ocr_adapter import extract_fields_heuristically
from src.workflow.field_gap_fill import missing_fields_for_doc


def test_heuristic_order_total_and_qty_from_so_style_text():
    text = """
销售订单
业务编号 SO25-0281
MAT-05777 动力电池托盘冲压总成 件 357 27.40 1% 9,683.98 13% 10,942.90
订单价税合计 人民币 10,942.90 元
本批发货数量为 357 件。
"""
    fields = extract_fields_heuristically(text)
    assert float(str(fields.get("totalAmount")).replace(",", "")) == 10942.9
    assert float(str(fields.get("quantity")).replace(",", "")) == 357


def test_missing_fields_flags_order_amount_qty():
    item = {
        "doc_type": "order",
        "file_name": "o.pdf",
        "fields": {"documentNo": "SO25-0281", "documentDate": "2025-12-12"},
        "_field_meta": {},
    }
    miss = missing_fields_for_doc(item)
    assert "totalAmount" in miss
    assert "quantity" in miss


def test_receipt_does_not_require_total_amount():
    item = {
        "doc_type": "receipt",
        "fields": {
            "documentNo": "YS1",
            "documentDate": "2026-01-02",
            "acceptanceDate": "2026-01-02",
            "quantity": 357,
        },
    }
    miss = missing_fields_for_doc(item)
    assert "totalAmount" not in miss


def test_gap_fill_hydrates_pdf_text_layer():
    from pathlib import Path

    from src.workflow.field_gap_fill import gap_fill_classified_documents

    root = Path(__file__).resolve().parents[1]
    pdf = root / "data" / "mock" / "SO25-0281" / "SO25-0281_HT25-0281_02_销售订单.pdf"
    if not pdf.is_file():
        return
    item = {
        "doc_type": "order",
        "file_name": pdf.name,
        "path": str(pdf),
        "raw_text": "",
        "fields": {"documentNo": "SO25-0281", "documentDate": "2025-12-12"},
    }
    filled, summary = gap_fill_classified_documents([item])
    assert summary.get("text_hydrated", 0) >= 1 or filled[0].get("raw_text")
    assert str(filled[0].get("raw_text") or "").strip()

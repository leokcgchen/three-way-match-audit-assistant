"""文档智能分类规则测试。"""

from __future__ import annotations

import pytest

from src.ui.debug_console import classify_document


@pytest.mark.parametrize(
    ("file_name", "expected"),
    [
        ("SO25-0281_HT25-0281_02_销售订单.pdf", "order"),
        ("SO25-0281_HT25-0281_03_销售发货单.pdf", "receipt"),
        ("SO25-0281_HT25-0281_04_产品验收单.pdf", "receipt"),
        ("SO25-0281_HT25-0281_05_增值税发票.pdf", "invoice"),
        ("销售订单.pdf", "order"),
        ("销售发货单.pdf", "receipt"),
        ("产品验收单.pdf", "receipt"),
        ("客户签收验收单.pdf", "receipt"),
        ("增值税发票.pdf", "invoice"),
        ("HT25-0281_销售合同.pdf", "contract"),
        ("采购合同.docx", "contract"),
    ],
)
def test_classify_by_filename(file_name: str, expected: str) -> None:
    assert classify_document(file_name, "") == expected


@pytest.mark.parametrize(
    ("ocr_text", "expected"),
    [
        ("发票代码 1234567890 价税合计 1000.00 税率 13%", "invoice"),
        ("签收人：张三 验收人：李四 收货日期 2026-06-01", "receipt"),
        ("订单编号 PO-001 采购方 甲公司 供应商 乙公司", "order"),
        ("合同编号 HT-001 甲方 甲公司 乙方 乙公司", "contract"),
    ],
)
def test_classify_by_ocr_fallback(ocr_text: str, expected: str) -> None:
    assert classify_document("unknown_scan.pdf", ocr_text) == expected


def test_filename_priority_over_ht_token() -> None:
    """文件名含 HT 合同号但主体为订单时，不应判为合同。"""
    name = "SO25-0281_HT25-0281_02_销售订单.pdf"
    assert classify_document(name, "合同编号 HT25-0281 甲方 乙方") == "order"


def test_slot_hint_for_ambiguous_filename() -> None:
    """上传到发票槽位时，无关键词文件名应识别为发票。"""
    assert classify_document("scan_001.pdf", "", slot_hint="invoice") == "invoice"


def test_ocr_full_text_invoice_after_long_prefix() -> None:
    """OCR 全文扫描：发票特征不在前 200 字也能识别。"""
    text = "A" * 300 + " 发票代码 1234567890 价税合计 1000.00"
    assert classify_document("unknown.pdf", text) == "invoice"

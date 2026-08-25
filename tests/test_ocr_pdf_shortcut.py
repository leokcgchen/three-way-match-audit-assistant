"""PDF 文字层短路：有足够文本时不调远程 PaddleOCR。"""

from __future__ import annotations

from pathlib import Path

from src.legacy_ocr import LegacyOcrAdapter
from src.legacy_ocr.ocr_adapter import _extract_pdf_text_evidence


def test_recognize_document_prefers_pdf_text_layer(tmp_path: Path, monkeypatch):
    adapter = LegacyOcrAdapter(use_mock_when_unavailable=True)
    pdf = tmp_path / "contract.pdf"
    pdf.write_bytes(b"%PDF-1.4 placeholder")

    sample = (
        "销售合同编号：HT25-0001\n对应订单：SO25-0001\n"
        "验收合格后确认收入。含税金额合计人民币壹拾万元整。\n"
    )
    monkeypatch.setattr(
        "src.legacy_ocr.ocr_adapter._extract_pdf_text_layer",
        lambda _p: sample,
    )

    def _should_not_call(*_a, **_k):
        raise AssertionError("有文字层时不应调用远程 PaddleOCR")

    monkeypatch.setattr(adapter, "_call_paddle_ocr", _should_not_call)
    out = adapter.recognize_document(str(pdf), "contract")
    assert out["source"] == "pdf_text"
    assert "HT25-0001" in (out.get("rawText") or "")
    assert float(out.get("confidence") or 0) >= 0.9


def test_thin_pdf_text_still_tries_paddle(tmp_path: Path, monkeypatch):
    adapter = LegacyOcrAdapter(use_mock_when_unavailable=True)
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 placeholder")
    monkeypatch.setattr(
        "src.legacy_ocr.ocr_adapter._extract_pdf_text_layer",
        lambda _p: "短",
    )
    monkeypatch.setattr(
        adapter,
        "_read_file_as_base64",
        lambda _p: ("AAAA", "pdf"),
    )
    monkeypatch.setattr(
        adapter,
        "_call_paddle_ocr",
        lambda *_a, **_k: {"result": {"ocrEngineResult": {"layouts": []}}},
    )
    monkeypatch.setattr(
        adapter,
        "_parse_paddle_response",
        lambda _p: ("扫码识别长文本" * 5, 0.9, []),
    )
    out = adapter.recognize_document(str(pdf), "invoice")
    assert out["source"] == "paddleocr"


def test_native_pdf_shortcut_returns_positioned_words():
    pdf = Path(__file__).parent / "fixtures" / "one_to_many" / "PO_SO001.pdf"

    text, blocks = _extract_pdf_text_evidence(str(pdf))

    assert "SO001" in text
    order_number = next(block for block in blocks if block["text"] == "SO001")
    assert order_number["source"] == "native_pdf_word"
    assert order_number["page"] == 0
    assert len(order_number["bbox"]) == 4
    assert text[order_number["char_start"] : order_number["char_end"]] == "SO001"

    out = LegacyOcrAdapter(use_mock_when_unavailable=False).recognize_document(
        str(pdf), "purchase_order"
    )
    assert out["source"] == "pdf_text"
    assert any(block.get("bbox") for block in out["textBlocks"])

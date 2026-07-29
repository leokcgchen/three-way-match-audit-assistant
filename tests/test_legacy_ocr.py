"""Legacy OCR 冒烟测试（默认走 Mock，无需真实 API Key）。"""

from __future__ import annotations

from pathlib import Path

from src.legacy_ocr import LegacyOcrAdapter
from src.three_way_match import ThreeWayMatcher


def test_legacy_ocr_import_and_mock_pipeline(tmp_path: Path | None = None):
    adapter = LegacyOcrAdapter(use_mock_when_unavailable=True)
    root = Path(tmp_path) if tmp_path else Path(".")
    # 写三个空图片占位；OCR 失败后自动降级 Mock
    paths = {}
    for key in ("order", "receipt", "invoice"):
        p = root / f"{key}.bin"
        p.write_bytes(b"not-a-real-image")
        paths[key] = str(p)

    order = adapter.recognize_and_extract(paths["order"], "purchase_order")
    assert order["extractedFields"].get("documentNo")
    assert order["source"] in {"mock", "paddleocr"}

    matcher = ThreeWayMatcher()
    result = matcher.match_from_legacy_ocr(paths, inprocess=True)
    assert "match_result" in result
    assert result["match_result"].overall_status in {"PASS", "WARNING", "FAIL"}
    assert result.get("human_readable_summary") or result["match_result"].human_readable_summary
    print("test_legacy_ocr_import_and_mock_pipeline: PASS")


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory(prefix="legacy_ocr_") as td:
        test_legacy_ocr_import_and_mock_pipeline(Path(td))
    print("✅ OCR适配器可用")

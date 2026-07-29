"""从老三单系统迁移的 OCR / 字段提取能力（精简 Python 版）。"""

from src.legacy_ocr.ocr_adapter import LegacyOcrAdapter
from src.legacy_ocr.models import OcrDocument, OcrResult

__all__ = ["LegacyOcrAdapter", "OcrDocument", "OcrResult"]

"""验证合同 PDF 真实 OCR 识别（非 Mock 降级）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from src.legacy_ocr.ocr_adapter import LegacyOcrAdapter

CONTRACT_PDF = ROOT / "data" / "mock" / "SO25-0281_HT25-0281_01_销售合同.pdf"


def _field(result: dict, *keys: str) -> str:
    fields = result.get("extractedFields") or {}
    for key in keys:
        val = fields.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
        val = result.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return "N/A"


def test_contract_ocr() -> None:
    """测试合同 PDF 真实 OCR 识别。"""
    if not CONTRACT_PDF.is_file():
        raise FileNotFoundError(f"合同 PDF 不存在: {CONTRACT_PDF}")

    adapter = LegacyOcrAdapter(use_mock_when_unavailable=False)

    print("📄 正在识别合同...")
    print(f"   文件: {CONTRACT_PDF}")
    result = adapter.recognize_and_extract(str(CONTRACT_PDF), "contract")

    source = result.get("source", "unknown")
    print(f"\n🔌 OCR 来源: {source}")
    if source == "mock":
        raise RuntimeError("仍使用 Mock 模式，请检查 .env 中的 QIANFAN_API_KEY 配置")

    contract_no = _field(result, "contractNo", "contract_no", "documentNo")
    supplier = _field(result, "supplierName", "supplier_name", "buyerName")
    total_amount = _field(result, "totalAmount", "total_amount", "amount")
    payment_terms = _field(result, "paymentTerms", "payment_terms")
    signing_date = _field(result, "documentDate", "signing_date", "signDate")

    print("\n✅ 识别结果：")
    print(f"  合同编号: {contract_no}")
    print(f"  供应商: {supplier}")
    print(f"  合同金额: {total_amount}")
    print(f"  账期条款: {payment_terms}")
    print(f"  签订日期: {signing_date}")

    expected = {
        "contract_no": "HT25-0281",
        "supplier_name": "华曜汽车零部件制造有限公司",
        "total_amount": "10942.90",
    }
    print("\n🔍 验证：")
    actual_map = {
        "contract_no": contract_no,
        "supplier_name": supplier,
        "total_amount": total_amount,
    }
    all_ok = True
    for key, val in expected.items():
        actual = actual_map[key]
        if str(val) in str(actual):
            print(f"  ✅ {key}: {actual}")
        else:
            all_ok = False
            print(f"  ❌ {key}: 预期'{val}'，实际'{actual}'")

    raw_preview = (result.get("rawText") or "")[:500]
    if raw_preview:
        print("\n📝 OCR 文本预览（前500字）：")
        print(raw_preview)

    print("\n📦 完整 extractedFields：")
    print(json.dumps(result.get("extractedFields") or {}, ensure_ascii=False, indent=2))

    if not all_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    test_contract_ocr()

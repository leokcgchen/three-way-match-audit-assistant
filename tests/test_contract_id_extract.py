"""合同编号文本提取单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rules.batch_cutoff import batch_cutoff_check
from src.utils.date_extractor import (
    extract_contract_id_from_row,
    extract_contract_id_from_text,
)


def test_extract_contract_id_patterns() -> None:
    assert (
        extract_contract_id_from_text(
            "样本编号=GOSPD25-0001；合同索引号=HT2501-0001；订单=SO2501-0001"
        )
        == "HT2501-0001"
    )
    assert extract_contract_id_from_text("合同号：HT-001") == "HT-001"
    assert extract_contract_id_from_text("合同编号=AB12-9") == "AB12-9"
    assert extract_contract_id_from_text("订单号=SO2501-0001") == "SO2501-0001"
    assert extract_contract_id_from_text("合同索引号HT2501-0002") == "HT2501-0002"
    assert extract_contract_id_from_text("") is None
    assert extract_contract_id_from_text(None) is None
    print("test_extract_contract_id_patterns: PASS")


def test_extract_from_row_and_batch() -> None:
    mock = ROOT / "data" / "mock" / "示例_SAP_序时账_含合同索引号.csv"
    assert mock.exists(), f"缺少测试文件: {mock}"
    df = pd.read_csv(mock, encoding="utf-8-sig")
    row = df.iloc[0]
    cid = extract_contract_id_from_row(row, ["摘要", "销售订单号"])
    assert cid == "HT2501-0001"

    ledger = pd.DataFrame(
        {
            "合同编号": ["SA25-0001"],  # 错误占位（凭证号）
            "entry_date": [str(row["过账日期"])[:10]],
            "entry_amount": [float(row["借方金额"])],
            "摘要": [row["摘要"]],
            "销售订单号": [row["销售订单号"]],
        }
    )
    receipt = pd.DataFrame(
        {
            "合同编号": ["HT2501-0001"],
            "receipt_date": ["2025-01-10"],
        }
    )
    result = batch_cutoff_check(
        ledger,
        receipt,
        payment_days=30,
        extract_contract_from_text=True,
        contract_text_columns=["摘要", "销售订单号"],
    )
    hit = result.loc[result["合同编号"] == "HT2501-0001"].iloc[0]
    assert hit["contract_id_extract_status"] == "SUCCESS"
    assert hit["cutoff_status"] in {"PASS", "WARNING", "FAIL"}
    assert hit["receipt_date"] == "2025-01-10" or pd.notna(hit.get("deviation_days"))
    print("test_extract_from_row_and_batch: PASS", hit["cutoff_status"])


if __name__ == "__main__":
    test_extract_contract_id_patterns()
    test_extract_from_row_and_batch()
    print("全部测试通过：合同编号文本提取正常。")

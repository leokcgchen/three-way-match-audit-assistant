"""日期文本提取单元测试。"""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rules.batch_cutoff import batch_cutoff_check
from src.ui.ledger_import import parse_ledger_file
from src.utils.date_extractor import (
    extract_all_dates_from_text,
    extract_date_from_text,
    is_date_column_candidate,
)


class NamedBytesIO(BytesIO):
    def __init__(self, data: bytes, name: str) -> None:
        super().__init__(data)
        self.name = name


def test_extract_patterns() -> None:
    assert extract_date_from_text("货物于2026-06-01签收") == "2026-06-01"
    assert extract_date_from_text("2026年6月5日") == "2026-06-05"
    assert extract_date_from_text("2026/06/08") == "2026-06-08"
    assert extract_date_from_text("06-01-2026") == "2026-06-01"
    assert extract_date_from_text("06/01/2026") == "2026-06-01"
    assert extract_date_from_text("") is None
    assert extract_all_dates_from_text("A 2026-01-01 B 2026年2月3日") == [
        "2026-01-01",
        "2026-02-03",
    ]
    assert is_date_column_candidate(["货物于2026-06-01签收", "无", "2026/06/08"])
    print("test_extract_patterns: PASS")


def test_mock_jsonl_and_batch() -> None:
    path = ROOT / "data" / "mock" / "示例_签收单_含文本描述.jsonl"
    df = parse_ledger_file(NamedBytesIO(path.read_bytes(), path.name))
    receipt = pd.DataFrame(
        {"合同编号": df["合同编号"], "receipt_date": df["签收备注"]}
    )
    ledger = pd.DataFrame(
        [
            {"合同编号": "HT-001", "entry_date": "2026-06-11", "entry_amount": 1},
            {"合同编号": "HT-004", "entry_date": "2026-06-20", "entry_amount": 1},
        ]
    )
    result = batch_cutoff_check(
        ledger,
        receipt,
        payment_days=10,
        extract_date_from_text=True,
        receipt_date_column="receipt_date",
    )
    row1 = result.loc[result["合同编号"] == "HT-001"].iloc[0]
    row4 = result.loc[result["合同编号"] == "HT-004"].iloc[0]
    assert row1["date_extract_status"] == "SUCCESS"
    assert row1["receipt_date"] == "2026-06-01"
    assert row4["date_extract_status"] == "FAIL"
    print("test_mock_jsonl_and_batch: PASS")


if __name__ == "__main__":
    test_extract_patterns()
    test_mock_jsonl_and_batch()
    print("全部测试通过：日期文本提取正常。")

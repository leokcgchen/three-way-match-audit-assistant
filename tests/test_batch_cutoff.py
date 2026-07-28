"""批量截止性测试单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rules.batch_cutoff import batch_cutoff_check, export_cutoff_excel


def test_batch_partial_match() -> None:
    ledger = pd.DataFrame(
        [
            {"合同编号": "HT-001", "entry_date": "2026-06-11", "entry_amount": 100},
            {"合同编号": "HT-002", "entry_date": "2026-06-05", "entry_amount": 200},
            {"合同编号": "HT-003", "entry_date": "2026-06-20", "entry_amount": 300},
        ]
    )
    receipt = pd.DataFrame(
        [
            {"合同编号": "HT-001", "receipt_date": "2026-06-01"},
            {"合同编号": "HT-002", "receipt_date": "2026-06-01"},
            {"合同编号": "HT-999", "receipt_date": "2026-05-01"},
        ]
    )
    result = batch_cutoff_check(ledger, receipt, payment_days=10, match_key="合同编号")
    status_map = {
        str(r["合同编号"]): r["cutoff_status"]
        for _, r in result.iterrows()
    }
    assert status_map["HT-001"] == "PASS"
    assert status_map["HT-002"] == "FAIL"
    assert status_map["HT-003"] == "NO_RECEIPT"
    assert status_map["HT-999"] == "NO_LEDGER"
    print("test_batch_partial_match: PASS")


def test_export_excel(tmp_path: Path | None = None) -> None:
    out_dir = ROOT / "data" / "mock"
    out_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(
        [
            {
                "合同编号": "HT-001",
                "cutoff_status": "PASS",
                "deviation_days": 0,
                "issue_description": "ok",
            }
        ]
    )
    path = out_dir / "_tmp_batch_export.xlsx"
    export_cutoff_excel(df, str(path))
    assert path.exists()
    path.unlink(missing_ok=True)
    print("test_export_excel: PASS")


if __name__ == "__main__":
    test_batch_partial_match()
    test_export_excel()
    print("全部测试通过：批量截止性逻辑正常。")

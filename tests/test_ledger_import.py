"""序时账 Excel/CSV 导入辅助函数测试。"""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ui.ledger_import import map_and_fill_ledger_data, parse_ledger_file


class NamedBytesIO(BytesIO):
    def __init__(self, data: bytes, name: str) -> None:
        super().__init__(data)
        self.name = name


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "入账日期": "2026-06-11",
                "收入金额": 500.0,
                "凭证编号": "记-126",
                "客户名称": "云创科技",
            },
            {
                "入账日期": "2026-06-05",
                "收入金额": 320.5,
                "凭证编号": "记-127",
                "客户名称": "智汇数据",
            },
        ]
    )


def test_parse_xlsx_and_csv(tmp_path: Path | None = None) -> None:
    out = ROOT / "data" / "mock"
    out.mkdir(parents=True, exist_ok=True)
    df = _sample_df()
    xlsx = out / "示例_序时账.xlsx"
    csv = out / "示例_序时账.csv"
    df.to_excel(xlsx, index=False)
    df.to_csv(csv, index=False, encoding="utf-8-sig")

    dfx = parse_ledger_file(NamedBytesIO(xlsx.read_bytes(), "示例_序时账.xlsx"))
    assert len(dfx) == 2
    dfc = parse_ledger_file(NamedBytesIO(csv.read_bytes(), "示例_序时账.csv"))
    assert len(dfc) == 2
    print("test_parse_xlsx_and_csv: PASS")


def test_map_second_row() -> None:
    df = _sample_df()
    payload = map_and_fill_ledger_data(
        df,
        1,
        {
            "date_col": "入账日期",
            "amount_col": "收入金额",
            "voucher_col": "凭证编号",
            "customer_col": "客户名称",
        },
    )
    assert payload["entry_date"] == "2026-06-05"
    assert payload["entry_amount"] == 320.5
    assert payload["voucher_id"] == "记-127"
    print("test_map_second_row: PASS")


def test_reject_pdf() -> None:
    try:
        parse_ledger_file(NamedBytesIO(b"%PDF-1.4", "bad.pdf"))
        raise AssertionError("应拒绝 PDF")
    except ValueError as exc:
        assert "不支持" in str(exc)
    print("test_reject_pdf: PASS")


def test_parse_jsonl() -> None:
    raw = (
        '{"合同编号":"HT-001","入账日期":"2026-06-11","收入金额":500.0}\n'
        '{"合同编号":"HT-002","入账日期":"2026-06-05","收入金额":320.5}\n'
    ).encode("utf-8")
    df = parse_ledger_file(NamedBytesIO(raw, "示例_序时账.jsonl"))
    assert len(df) == 2
    assert "合同编号" in df.columns
    print("test_parse_jsonl: PASS")


def test_bad_amount() -> None:
    df = pd.DataFrame([{"入账日期": "2026-06-01", "收入金额": "abc"}])
    try:
        map_and_fill_ledger_data(
            df, 0, {"date_col": "入账日期", "amount_col": "收入金额"}
        )
        raise AssertionError("金额校验应失败")
    except ValueError as exc:
        assert "金额" in str(exc)
    print("test_bad_amount: PASS")


if __name__ == "__main__":
    test_parse_xlsx_and_csv()
    test_map_second_row()
    test_reject_pdf()
    test_parse_jsonl()
    test_bad_amount()
    print("全部测试通过：序时账文件导入逻辑正常。")

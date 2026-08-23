"""序时账解析与入账日期匹配测试。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.legacy_ocr.ledger_parser import (
    apply_ledger_to_classified,
    build_ledger_index,
    collect_document_biz_keys,
    load_ledger_file,
    lookup_posting_date,
    suggest_column_mapping,
)


def test_suggest_column_mapping_sap():
    path = ROOT / "data" / "mock" / "示例_SAP_序时账_含合同索引号.csv"
    df = load_ledger_file(path)
    mapping = suggest_column_mapping(list(df.columns))
    assert mapping["posting_date"] == "过账日期"
    assert mapping["biz_id"] == "销售订单号"


def test_build_index_and_lookup():
    path = ROOT / "data" / "mock" / "示例_SAP_序时账_含合同索引号.csv"
    df = load_ledger_file(path)
    mapping = suggest_column_mapping(list(df.columns))
    index = build_ledger_index(df, mapping)
    hit = lookup_posting_date(index, ["SO2501-0001"])
    assert hit is not None
    assert hit["posting_date"] == "2025-01-18"


def test_apply_to_invoice():
    path = ROOT / "data" / "mock" / "示例_SAP_序时账_含合同索引号.csv"
    df = load_ledger_file(path)
    mapping = suggest_column_mapping(list(df.columns))
    index = build_ledger_index(df, mapping)
    classified = [
        {
            "file_name": "invoice.pdf",
            "doc_type": "invoice",
            "fields": {
                "documentNo": "SO2501-0001",
                "invoiceNo": "INV-001",
                "postingDate": "2020-01-01",
            },
        }
    ]
    out = apply_ledger_to_classified(classified, index)
    assert out[0]["ledger_match_ok"] is True
    assert out[0]["fields"]["postingDate"] == "2025-01-18"


def test_apply_uses_order_filename_biz_keys():
    path = ROOT / "data" / "mock" / "示例_SAP_序时账_含合同索引号.csv"
    df = load_ledger_file(path)
    mapping = suggest_column_mapping(list(df.columns))
    index = build_ledger_index(df, mapping)
    classified = [
        {
            "file_name": "SO25-0281_HT25-0281_02_销售订单.pdf",
            "doc_type": "order",
            "fields": {"documentNo": "SO25-0281"},
        },
        {
            "file_name": "invoice.pdf",
            "doc_type": "invoice",
            "fields": {"documentNo": "INV-999", "invoiceNo": "INV-999"},
        },
    ]
    from src.legacy_ocr.ledger_parser import collect_workflow_biz_keys

    workflow_keys = collect_workflow_biz_keys(classified)
    assert "SO25-0281" in workflow_keys
    out = apply_ledger_to_classified(classified, index, order_biz_keys=workflow_keys)
    inv = next(x for x in out if x["doc_type"] == "invoice")
    assert inv["ledger_match_ok"] is False


def test_match_so25_0281_in_ledger_summary():
    import pandas as pd
    from src.legacy_ocr.ledger_parser import (
        apply_ledger_to_classified,
        collect_workflow_biz_keys,
        extract_biz_ids_from_filename,
    )

    assert "SO25-0281" in extract_biz_ids_from_filename(
        "SO25-0281_HT25-0281_05_增值税发票.pdf"
    )
    assert "HT25-0281" in extract_biz_ids_from_filename(
        "SO25-0281_HT25-0281_05_增值税发票.pdf"
    )

    df = pd.DataFrame(
        [
            {
                "凭证号": "SA25-0281",
                "过账日期": "2025-02-10",
                "销售订单号": "SO25-0281",
                "借方金额": 1000,
                "摘要": "订单=SO25-0281；合同索引号=HT25-0281",
            }
        ]
    )
    mapping = {"posting_date": "过账日期", "biz_id": "销售订单号", "amount": "借方金额"}
    index = build_ledger_index(df, mapping)
    classified = [
        {
            "file_name": "SO25-0281_HT25-0281_05_增值税发票.pdf",
            "doc_type": "invoice",
            "fields": {"invoiceNo": "INV-001"},
        },
        {
            "file_name": "SO25-0281_HT25-0281_02_销售订单.pdf",
            "doc_type": "order",
            "fields": {},
        },
    ]
    keys = collect_workflow_biz_keys(classified)
    out = apply_ledger_to_classified(classified, index, order_biz_keys=keys)
    inv = next(x for x in out if x["doc_type"] == "invoice")
    assert inv["ledger_match_ok"] is True
    assert inv["fields"]["postingDate"] == "2025-02-10"
    assert inv["ledger_matched_biz_id"] == "SO25-0281"


def test_collect_biz_keys():
    keys = collect_document_biz_keys(
        {"documentNo": "SO25-0281", "remarks": "订单=SO25-0281_HT25-0281"}
    )
    assert "SO25-0281" in keys


def test_normalize_biz_id_tail_i_to_one():
    from src.legacy_ocr.ledger_parser import extract_biz_ids_from_filename, normalize_biz_id

    assert normalize_biz_id("SO25-002I") == "SO25-0021"
    assert normalize_biz_id("SO25-002l") == "SO25-0021"
    assert extract_biz_ids_from_filename("SO25-002I_签收单.pdf") == ["SO25-0021"]


def test_kjht_fullwidth_colon_not_truncated_to_ht():
    from src.legacy_ocr.ledger_parser import extract_biz_ids_from_free_text

    ids = extract_biz_ids_from_free_text("合同编号：KJHT25-0282 业务编号：SO25-0282")
    assert "SO25-0282" in ids
    assert "KJHT25-0282" in ids
    assert "HT25-0282" not in ids


if __name__ == "__main__":
    test_suggest_column_mapping_sap()
    test_build_index_and_lookup()
    test_apply_to_invoice()
    test_collect_biz_keys()
    print("ledger_parser tests: PASS")

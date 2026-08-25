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


def test_sample_business_id_is_the_only_ledger_query_when_present():
    import pandas as pd

    df = pd.DataFrame(
        [
            {
                "business_id": "YW-2025-3962",
                "book_date": "2026-01-02",
                "book_amount": 113000,
            }
        ]
    )
    mapping = {
        "posting_date": "book_date",
        "biz_id": "business_id",
        "amount": "book_amount",
    }
    index = build_ledger_index(df, mapping)
    classified = [
        {
            "file_name": "YW-2025-3962_发票_FP-260102-8305.pdf",
            "doc_type": "invoice",
            "sample_business_id": "YW-2025-3962",
            "business_index_source": "filename",
            "fields": {"orderNo": "SO-251209-7214"},
        }
    ]

    out = apply_ledger_to_classified(
        classified,
        index,
        order_biz_keys=["SO-251209-7214"],
    )

    assert out[0]["ledger_match_ok"] is True
    assert out[0]["ledger_query_biz_id"] == "YW-2025-3962"
    assert out[0]["ledger_matched_biz_id"] == "YW-2025-3962"
    assert out[0]["ledger_index_column"] == "business_id"
    assert out[0]["ledger_match_reason"]["code"] == "MATCHED"


def test_ledger_failure_explains_both_index_sides():
    import pandas as pd

    df = pd.DataFrame(
        [{"business_id": "YW-2025-3962", "book_date": "2026-01-02"}]
    )
    index = build_ledger_index(
        df,
        {"posting_date": "book_date", "biz_id": "business_id", "amount": None},
    )
    classified = [
        {
            "file_name": "YW-2025-9999_发票.pdf",
            "doc_type": "invoice",
            "sample_business_id": "YW-2025-9999",
            "business_index_source": "filename",
            "fields": {"orderNo": "SO-251209-7214"},
        }
    ]

    out = apply_ledger_to_classified(classified, index)

    assert out[0]["ledger_match_ok"] is False
    assert out[0]["ledger_query_biz_id"] == "YW-2025-9999"
    assert out[0]["ledger_index_column"] == "business_id"
    assert out[0]["ledger_match_reason"] == {
        "code": "NOT_FOUND",
        "message": "序时账业务主键列中未找到与凭证业务编号相同的值。",
        "document_index": "YW-2025-9999",
        "document_index_source": "filename",
        "ledger_index_column": "business_id",
        "query_value": "YW-2025-9999",
    }


def test_pipeline_ledger_wrapper_resolves_sample_identity_before_lookup():
    from src.workflow.pipeline import apply_ledger_to_classified_list

    classified = [
        {
            "file_name": "YW-2025-3962_签收验收单_YS-260102-005.pdf",
            "doc_type": "receipt",
            "fields": {"documentNo": "YS-260102-005", "orderNo": "SO-251209-7214"},
        }
    ]
    rows = [
        {
            "business_id": "YW-2025-3962",
            "book_date": "2026-01-02",
            "book_amount": 113000,
        }
    ]

    out = apply_ledger_to_classified_list(
        classified,
        rows,
        {"posting_date": "book_date", "biz_id": "business_id", "amount": "book_amount"},
        sample_population={"business_ids": ["YW-2025-3962"]},
    )

    assert out[0]["sample_business_id"] == "YW-2025-3962"
    assert out[0]["business_index_source"] == "filename"
    assert out[0]["ledger_match_ok"] is True


if __name__ == "__main__":
    test_suggest_column_mapping_sap()
    test_build_index_and_lookup()
    test_apply_to_invoice()
    test_collect_biz_keys()
    print("ledger_parser tests: PASS")

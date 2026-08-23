from src.workflow.required_docs import (
    content_host_type,
    missing_required_docs,
    present_doc_labels,
    slot_completeness_matrix,
)


def test_content_type_ignores_filename():
    doc = {
        "file_name": "随便叫什么.pdf",
        "doc_type": "invoice",
        "raw_text": "销售订单\n订单编号 SO25-0281\n采购方 甲\n供应商 乙",
        "source_packet": {"card_type": "order"},
    }
    assert content_host_type(doc) == "order"
    assert "订单" in present_doc_labels([doc])


def test_filename_alone_does_not_count_as_identified():
    doc = {
        "file_name": "SO25-0281_05_增值税发票.pdf",
        "doc_type": "invoice",
        "raw_text": "",
    }
    assert content_host_type(doc) == ""
    assert present_doc_labels([doc]) == []


def test_01030_missing_fulfillment():
    job = {"plan": {"required_steps": ["three_way_cutoff", "amount_test"]}}
    docs = [
        {
            "raw_text": "增值税专用发票\n发票号码 1\n价税合计 100",
            "source_packet": {"card_type": "invoice"},
        },
        {
            "raw_text": "销售订单\n订单编号 SO1",
            "source_packet": {"card_type": "order"},
        },
    ]
    miss = missing_required_docs(docs, job)
    assert "签收或发货" in miss
    assert "发票" not in miss
    assert "订单" not in miss


def test_slot_matrix_marks_uncertain_when_unresolved_unit():
    job = {"plan": {"required_steps": ["three_way_cutoff", "amount_test"]}}
    docs = [
        {
            "raw_text": "销售订单\n订单编号 SO1",
            "source_packet": {"card_type": "order"},
        },
        {
            "raw_text": "增值税专用发票\n发票号码 1\n价税合计 100",
            "source_packet": {"card_type": "invoice"},
        },
        {
            "doc_type": "other",
            "raw_text": "模糊扫描页",
            "source_packet": {"card_type": "unresolved"},
        },
    ]
    matrix = {r["id"]: r["status"] for r in slot_completeness_matrix(docs, job)}
    assert matrix["order"] == "present"
    assert matrix["invoice"] == "present"
    assert matrix["fulfillment"] == "uncertain"


def test_slot_matrix_doc_type_alone_counts_present():
    job = {"plan": {"required_steps": ["three_way_cutoff", "amount_test"]}}
    docs = [
        {"doc_type": "order", "raw_text": ""},
        {"doc_type": "invoice", "raw_text": ""},
        {"doc_type": "receipt", "raw_text": ""},
    ]
    matrix = {r["id"]: r["status"] for r in slot_completeness_matrix(docs, job)}
    assert matrix["order"] == "present"
    assert matrix["invoice"] == "present"
    assert matrix["fulfillment"] == "present"

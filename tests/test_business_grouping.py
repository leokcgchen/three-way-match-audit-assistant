from src.workflow.business_grouping import build_business_groups, group_documents_by_business


def _doc(name, kind, **fields):
    return {"file_name": name, "doc_type": kind, "fields": fields}


def test_strong_key_builds_matched_group():
    groups = build_business_groups([
        _doc("contract.pdf", "contract", contractNo="HT-1", orderNo="SO-1"),
        _doc("invoice.pdf", "invoice", orderNo="SO-1", invoiceNo="INV-1"),
    ])
    assert len(groups) == 1
    assert groups[0]["status"] == "MATCHED"
    assert groups[0]["doc_count"] == 2
    assert "orderNo:SO-1" in groups[0]["strong_keys"]


def test_human_group_override_is_explicit_and_has_priority():
    groups = build_business_groups([
        {**_doc("a.pdf", "invoice", invoiceNo="INV-A"), "business_group_id": "BG-MANUAL"},
        {**_doc("b.pdf", "receipt"), "business_group_id": "BG-MANUAL"},
    ])
    assert len(groups) == 1
    assert groups[0]["group_id"] == "BG-MANUAL"
    assert groups[0]["manual_override"] is True


def test_sample_business_id_groups_documents_before_document_numbers() -> None:
    documents = [
        {
            **_doc("order.pdf", "order", orderNo="SO-251209-7214"),
            "sample_business_id": "YW-2025-3962",
        },
        {
            **_doc("invoice.pdf", "invoice", invoiceNo="FP-260102-8305"),
            "sample_business_id": "YW-2025-3962",
        },
        {
            **_doc("receipt.pdf", "receipt", documentNo="YS-260102-005"),
            "sample_business_id": "YW-2025-3962",
        },
    ]

    groups = group_documents_by_business(documents)

    assert len(groups) == 1
    assert groups[0][0] == "YW-2025-3962"
    assert [document["file_name"] for document in groups[0][1]] == [
        "order.pdf",
        "invoice.pdf",
        "receipt.pdf",
    ]

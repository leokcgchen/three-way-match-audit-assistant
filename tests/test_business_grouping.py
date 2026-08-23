from src.workflow.business_grouping import build_business_groups


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

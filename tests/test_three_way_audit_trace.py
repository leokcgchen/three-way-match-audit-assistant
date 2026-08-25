from src.three_way_match.audit_trace import build_three_way_audit_view


def _doc(doc_type: str, text: str) -> dict:
    return {"doc_type": doc_type, "file_name": f"{doc_type}.pdf", "raw_text": text, "fields": {}}


def _match(comparisons: list[dict], status: str = "PASS") -> dict:
    return {
        "match_result": {"overall_status": status, "comparisons": comparisons},
        "cutoff_available": True,
        "cutoff_result": {"测试状态": "FAIL", "问题描述": "提前确认收入"},
    }


def test_same_business_so_and_contract_number_are_one_group() -> None:
    docs = [
        _doc("order", "订单号 SO25-0281，合同 HT25-0281"),
        _doc("receipt", "业务 SO25-0281"),
        _doc("invoice", "销售订单 SO25-0281"),
    ]
    result = build_three_way_audit_view(
        docs,
        _match([
            {"field_name": "supplier_name", "is_consistent": True},
            {"field_name": "total_amount", "is_consistent": True},
            {"field_name": "quantity", "is_consistent": True},
        ]),
    )
    assert result["document_binding"]["status"] == "PASS"
    assert result["field_consistency"]["status"] == "PASS"
    assert result["three_way_status"] == "PASS"
    assert result["cutoff_status"] == "FAIL"


def test_missing_receipt_is_document_binding_failure() -> None:
    result = build_three_way_audit_view(
        [_doc("order", "SO25-0281"), _doc("invoice", "SO25-0281")],
        _match([]),
    )
    assert result["document_binding"]["reason_code"] == "REQUIRED_DOCUMENT_MISSING"
    assert result["field_consistency"]["status"] == "NOT_TESTED"
    assert result["three_way_failure_category"] == "DOCUMENT_BINDING"


def test_contract_can_satisfy_the_anchor_role_when_references_are_shared() -> None:
    docs = [
        _doc("contract", "合同 HT25-0001"),
        _doc("receipt", "合同 HT25-0001"),
        _doc("invoice", "合同 HT25-0001"),
    ]

    match = _match([{"field_name": "supplier_name", "is_consistent": True}])
    match["anchor_source"] = "CONTRACT_AS_ORDER_ANCHOR"
    result = build_three_way_audit_view(docs, match)

    assert result["document_binding"]["status"] == "PASS"
    assert result["document_binding"]["anchor_role"] == "contract"


def test_inconsistent_field_is_not_cutoff_failure() -> None:
    docs = [_doc("order", "SO25-0281"), _doc("receipt", "SO25-0281"), _doc("invoice", "SO25-0281")]
    result = build_three_way_audit_view(
        docs,
        _match([
            {"field_name": "total_amount", "is_consistent": False, "diff_description": "金额超出容差"},
        ], "FAIL"),
    )
    assert result["field_consistency"]["reason_code"] == "FIELD_INCONSISTENT_HIGH_RISK"
    assert result["three_way_failure_category"] == "FIELD_CONSISTENCY"
    assert result["cutoff_status"] == "FAIL"


def test_same_numeric_suffix_without_shared_reference_requires_manual_binding() -> None:
    docs = [
        _doc("order", "订单号 SO25-0281"),
        _doc("receipt", "验收依据 HT25-0281"),
        _doc("invoice", "客户采购号 PO25-0281"),
    ]

    result = build_three_way_audit_view(
        docs,
        _match([
            {"field_name": "supplier_name", "is_consistent": True},
            {"field_name": "total_amount", "is_consistent": True},
            {"field_name": "quantity", "is_consistent": True},
        ]),
    )

    assert result["document_binding"]["status"] == "FAIL"
    assert result["document_binding"]["reason_code"] == "BUSINESS_REFERENCE_UNCONFIRMED"
    assert result["decision"] == "HOLD_REVIEW"
    assert result["hold_reason_code"] == "AMBIGUOUS_BINDING"


def test_human_confirmed_group_allows_fields_to_be_tested_without_shared_reference() -> None:
    docs = [
        _doc("order", "订单号 SO25-0281"),
        _doc("receipt", "验收依据 HT25-0281"),
        _doc("invoice", "客户采购号 PO25-0281"),
    ]
    result = build_three_way_audit_view(
        docs,
        _match([
            {"field_name": "supplier_name", "is_consistent": True},
            {"field_name": "total_amount", "is_consistent": True},
            {"field_name": "quantity", "is_consistent": True},
        ]),
        business_binding_confirmed=True,
    )

    assert result["document_binding"]["status"] == "PASS"
    assert result["document_binding"]["reason_code"] == "DOCUMENT_GROUP_HUMAN_CONFIRMED"
    assert result["field_consistency"]["status"] == "PASS"
    assert result["three_way_status"] == "PASS"


def test_persists_date_chronology_and_marks_an_inversion_as_fail() -> None:
    docs = [
        {"doc_type": "contract", "file_name": "c.pdf", "fields": {"documentDate": "2025-12-04"}},
        {"doc_type": "order", "file_name": "o.pdf", "fields": {"documentDate": "2025-12-03"}},
        {"doc_type": "receipt", "file_name": "r.pdf", "fields": {}},
        {"doc_type": "invoice", "file_name": "i.pdf", "fields": {"documentDate": "2025-12-05"}},
    ]

    result = build_three_way_audit_view(docs, _match([]))

    assert result["date_chronology"]["status"] == "FAIL"
    assert "合同日" in result["date_chronology"]["summary"]

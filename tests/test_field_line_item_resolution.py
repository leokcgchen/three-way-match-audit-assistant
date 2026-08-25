from src.workflow.field_resolution.line_items import extract_line_nodes, match_line_groups


def _line(
    line_id: str,
    *,
    name: str = "伺服电机",
    model: str = "SM-130",
    qty: int = 20,
    unit: str = "台",
    code: str = "",
    total: int | None = None,
    amount: int | None = None,
    tax: int | None = None,
    unit_price: int | None = None,
) -> dict:
    return {
        "line_id": line_id,
        "goods_name": name,
        "model": model,
        "item_code": code,
        "quantity": qty,
        "unit": unit,
        "total_amount": total,
        "net_amount": amount,
        "tax_amount": tax,
        "unit_price": unit_price,
        "document_id": f"{line_id}.pdf",
        "evidence_ids": [f"ev-{line_id}"],
    }


def test_extract_line_nodes_keeps_dynamic_multi_item_fields() -> None:
    document = {
        "file_name": "order.pdf",
        "doc_type": "order",
        "fields": {
            "items": [
                {"lineNo": "10", "goodsName": "伺服电机", "model": "SM-130", "quantity": 8, "unit": "台"},
                {"lineNo": "20", "goodsName": "编码器", "model": "EN-20", "quantity": 2, "unit": "件"},
            ]
        },
    }
    lines = extract_line_nodes(document)
    assert [(line["line_id"], line["goods_name"], line["model"]) for line in lines] == [
        ("10", "伺服电机", "SM-130"),
        ("20", "编码器", "EN-20"),
    ]


def test_two_receipts_sum_to_order_quantity_with_explanation() -> None:
    groups = match_line_groups(
        [_line("order", qty=20)],
        [_line("receipt-a", qty=8), _line("receipt-b", qty=12)],
        [_line("invoice", qty=20)],
    )
    group = groups[0]
    assert group["quantity_result"] == "PASS"
    assert group["calculation"] == "8台 + 12台 = 20台；发票20台"


def test_reordered_rows_and_many_invoice_lines_match_by_model() -> None:
    orders = [_line("o-sm", model="SM-130", qty=20), _line("o-en", name="编码器", model="EN-20", qty=2)]
    receipts = [_line("r-en", name="编码器", model="EN-20", qty=2), _line("r-sm", model="SM-130", qty=20)]
    invoices = [
        _line("i-sm-a", model="SM-130", qty=8),
        _line("i-en", name="编码器", model="EN-20", qty=2),
        _line("i-sm-b", model="SM-130", qty=12),
    ]
    groups = match_line_groups(orders, receipts, invoices)
    assert {group["order_line_id"]: group["quantity_result"] for group in groups} == {
        "o-sm": "PASS",
        "o-en": "PASS",
    }


def test_same_name_with_different_model_never_collapses() -> None:
    orders = [_line("o-130", model="SM-130"), _line("o-180", model="SM-180")]
    receipt = _line("r-180", model="SM-180")
    groups = match_line_groups(orders, [receipt], [_line("i-180", model="SM-180")])
    group_180 = next(group for group in groups if group["order_line_id"] == "o-180")
    group_130 = next(group for group in groups if group["order_line_id"] == "o-130")
    assert [line["line_id"] for line in group_180["receipt_lines"]] == ["r-180"]
    assert group_130["quantity_result"] == "REVIEW"


def test_unit_mismatch_and_quantity_short_or_over_are_explicit_failures() -> None:
    mismatch = match_line_groups(
        [_line("o", qty=20, unit="台")],
        [_line("r", qty=20, unit="件")],
        [_line("i", qty=20, unit="台")],
    )[0]
    assert mismatch["unit_result"] == "FAIL"
    assert "UNIT_MISMATCH" in mismatch["reason_codes"]

    short = match_line_groups([_line("o", qty=20)], [_line("r", qty=18)], [_line("i", qty=18)])[0]
    over = match_line_groups([_line("o", qty=20)], [_line("r", qty=22)], [_line("i", qty=22)])[0]
    assert short["quantity_result"] == "FAIL"
    assert "QUANTITY_SHORTAGE" in short["reason_codes"]
    assert over["quantity_result"] == "FAIL"
    assert "QUANTITY_OVERAGE" in over["reason_codes"]


def test_amount_formula_uses_net_plus_tax_and_detects_failure() -> None:
    passed = match_line_groups(
        [_line("o", total=113000)],
        [_line("r")],
        [_line("i", amount=100000, tax=13000)],
    )[0]
    assert passed["amount_result"] == "PASS"
    assert passed["amount_calculation"] == "100000 + 13000 = 113000"

    failed = match_line_groups(
        [_line("o", total=113000)],
        [_line("r")],
        [_line("i", amount=100000, tax=12000)],
    )[0]
    assert failed["amount_result"] == "FAIL"
    assert "AMOUNT_FORMULA_MISMATCH" in failed["reason_codes"]


def test_amount_formula_exposes_quantity_unit_price_and_tax_when_supported() -> None:
    passed = match_line_groups(
        [_line("o", total=113000)],
        [_line("r")],
        [_line("i", amount=100000, tax=13000, unit_price=5000)],
    )[0]
    assert passed["amount_result"] == "PASS"
    assert passed["amount_calculation"] == "20 × 5000 + 13000 = 113000"


def test_equal_cost_ambiguous_assignment_stays_for_review() -> None:
    orders = [_line("o-a", name="电机", model="", code=""), _line("o-b", name="电机", model="", code="")]
    groups = match_line_groups(orders, [_line("r", name="电机", model="", code="")], [])
    assert any(group["quantity_result"] == "REVIEW" for group in groups)
    assert any("AMBIGUOUS_LINE_ASSIGNMENT" in group["reason_codes"] for group in groups)


def test_missing_quantity_stays_unknown_instead_of_becoming_zero() -> None:
    order = _line("o", qty=20)
    receipt = _line("r", qty=20)
    invoice = _line("i", qty=20)
    invoice["quantity"] = None

    group = match_line_groups([order], [receipt], [invoice])[0]

    assert group["invoiced_quantity"] == ""
    assert group["quantity_result"] == "REVIEW"
    assert "LINE_QUANTITY_EVIDENCE_MISSING" in group["reason_codes"]


def test_model_conflict_blocks_fallback_to_same_generic_goods_name() -> None:
    order = _line("o", name="控制器", model="MVC-300", qty=2)
    receipt = _line("r", name="控制器", model="MC-300", qty=2)

    group = match_line_groups([order], [receipt], [_line("i", name="控制器", model="MVC-300", qty=2)])[0]

    assert group["receipt_lines"] == []
    assert group["quantity_result"] == "REVIEW"
    assert "STRONG_MODEL_CONFLICT" in group["reason_codes"]


def test_line_nodes_use_only_their_nested_item_evidence() -> None:
    document = {
        "file_name": "order.pdf",
        "doc_type": "order",
        "fields": {
            "items": [
                {"goodsName": "工业相机镜头", "model": "VL-50", "quantity": 10},
                {"goodsName": "视觉检测相机", "model": "VC-500", "quantity": 15},
            ]
        },
        "field_evidence_nodes": [
            {"evidence_id": "ev-vl", "field_key": "items.0.model", "usable_for_decision": True},
            {"evidence_id": "ev-vc", "field_key": "items.1.model", "usable_for_decision": True},
            {"evidence_id": "ev-header", "field_key": "orderNo", "usable_for_decision": True},
        ],
    }

    lines = extract_line_nodes(document)

    assert lines[0]["evidence_ids"] == ["ev-vl"]
    assert lines[1]["evidence_ids"] == ["ev-vc"]

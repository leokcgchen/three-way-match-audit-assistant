from src.workflow.classify import classify_document, light_classify_file
from src.workflow.field_catalog import (
    default_field_plan,
    ensure_field_plan,
    resolve_target_fields,
)


def test_light_classify_by_filename():
    hit = light_classify_file("SO25-0281_HT25-0281_02_销售订单.pdf")
    assert hit["doc_type"] == "order"
    assert hit["doc_type_source"] == "light"


def test_classify_invoice_filename():
    assert classify_document("增值税发票.pdf", "") == "invoice"


def test_field_plan_resolve_includes_global_and_custom():
    plan = default_field_plan()
    plan["by_type"]["order"]["custom"] = ["warrantyMonths"]
    plan["global_extra"] = ["projectCode"]
    keys = resolve_target_fields("order", plan)
    assert "totalAmount" in keys  # system required
    assert "warrantyMonths" in keys
    assert "projectCode" in keys


def test_ensure_field_plan_strips_dupes():
    raw = {
        "confirmed": True,
        "global_extra": ["a", "a", ""],
        "by_type": {
            "invoice": {
                "system_required": ["invoiceNo"],
                "selected_optional": ["invoiceNo", "remarks"],
                "custom": ["remarks", "foo"],
            }
        },
    }
    plan = ensure_field_plan(raw)
    assert plan["global_extra"] == ["a"]
    inv = plan["by_type"]["invoice"]
    assert "invoiceNo" in inv["system_required"]
    assert "invoiceNo" not in inv["selected_optional"]
    assert inv["custom"] == ["foo"]


def test_default_optional_not_preselected():
    plan = default_field_plan()
    for slot in plan["by_type"].values():
        assert slot["selected_optional"] == []


def test_auto_confirm_field_plan():
    from src.workflow.field_catalog import auto_confirm_field_plan

    plan = auto_confirm_field_plan()
    assert plan["confirmed"] is True
    assert plan["confirmed_at"]

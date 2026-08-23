from src.workflow.workbook_row_edits import (
    apply_edits_to_rows,
    preview_rows_for_gate5,
    schema_for_goals,
    upsert_chain_edit,
)


def test_schema_only_gospd01030():
    assert schema_for_goals(["gospd01030"])["format"] == "gospd01030"
    assert schema_for_goals(["amount_only"]) is None


def test_apply_edits_overrides_w_x_not_formula_fields():
    job = {
        "workbook_row_edits": {
            "gospd01030": {
                "SO25-1": {
                    "all_ok": "YES 是",
                    "exception": "人工复核无异常",
                }
            }
        }
    }
    rows = [
        {
            "chain_id": "SO25-1",
            "all_ok": "",
            "exception": "系统冲突",
            "period_ok": "YES 是",
        }
    ]
    out = apply_edits_to_rows(rows, job, fmt="gospd01030")
    assert out[0]["all_ok"] == "YES 是"
    assert out[0]["exception"] == "人工复核无异常"
    assert out[0]["period_ok"] == "YES 是"  # 独立判断字段不被 schema 覆写
    assert out[0]["auditor_edited"] is True


def test_upsert_rejects_unknown_format():
    try:
        upsert_chain_edit({}, fmt="nope", chain_id="x", patch={"all_ok": "a"})
        assert False, "should raise"
    except ValueError:
        pass


def test_preview_unsupported_goal():
    out = preview_rows_for_gate5({"goal_ids": ["other"], "plan": {"goal_ids": ["other"]}})
    assert out["supported"] is False

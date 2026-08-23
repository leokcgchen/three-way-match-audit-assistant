"""底稿目标多选并集（仅官方 GOSPD）。"""

from __future__ import annotations

import pytest

from src.workflow.recipes import list_workpaper_goals, resolve_workflow_plan


def test_empty_goals():
    plan = resolve_workflow_plan([])
    assert plan["required_steps"] == []
    assert "upload_ocr" in plan["skipped_steps"]


def test_only_official_goals_listed():
    ids = {g["goal_id"] for g in list_workpaper_goals()}
    assert ids == {
        "gospd01010",
        "gospd01030",
        "gospd01010_2",
        "gospd01010_3",
        "gospd01010_4",
    }


def test_gospd01030_skips_amount_and_contract():
    plan = resolve_workflow_plan(["gospd01030"])
    assert "field_confirm" in plan["required_steps"]
    assert "three_way_cutoff" in plan["required_steps"]
    assert "amount_test" not in plan["required_steps"]
    assert "contract_terms" not in plan["required_steps"]
    assert "amount_test" in plan["skipped_steps"]
    assert "GOSPD01030" in plan["workbook_sheets"]
    assert plan["workbook_formats"] == ["gospd01030"]
    assert "裁剪序时账" in plan["note"]
    assert all("Gate4" not in (s.get("label") or "") for s in plan["step_labels"])


def test_multi_select_union_official():
    plan = resolve_workflow_plan(["gospd01030", "gospd01010"])
    assert "amount_test" in plan["required_steps"]
    assert "contract_terms" in plan["required_steps"]
    assert "three_way_cutoff" in plan["required_steps"]
    assert "GOSPD01030" in plan["workbook_sheets"]
    assert "GOSPD01010.1" in plan["workbook_sheets"]
    assert plan["workbook_formats"] == ["gospd01030", "gospd01010"]


def test_unknown_goal():
    with pytest.raises(ValueError):
        resolve_workflow_plan(["no_such"])
    with pytest.raises(ValueError):
        resolve_workflow_plan(["amount"])  # 已下线的自建目标


def test_gospd01010_chain_steps():
    plan = resolve_workflow_plan(["gospd01010"])
    # 与官方程序序一致：条款清晰性 → 金额准确性 → 三单/截止
    assert plan["required_steps"] == [
        "upload_ocr",
        "field_confirm",
        "evidence_match",
        "relations_gate4",
        "contract_terms",
        "amount_test",
        "three_way_cutoff",
        "conclusion_gate5",
        "workbook_export",
    ]
    assert "GOSPD01010.1" in plan["workbook_sheets"]
    assert "amount_test" in plan["required_steps"]
    assert "three_way_cutoff" in plan["required_steps"]
    assert plan["required_steps"].index("contract_terms") < plan["required_steps"].index(
        "amount_test"
    )

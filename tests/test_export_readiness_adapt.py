"""Adaptation checks for export_readiness + three_way split persistence."""

from __future__ import annotations

from src.workflow.export_readiness import build_export_readiness
from src.workflow.job_store import JobStore
from src.workflow.three_way_persist import clear_three_way_fields, three_way_sample_patch


def test_three_way_sample_patch_writes_split_views():
    patch = three_way_sample_patch(
        {
            "overall_status": "FAIL",
            "three_way_status": "PASS",
            "cutoff_status": "FAIL",
            "three_way_summary": "三单通过",
            "cutoff_summary": "截止失败",
            "document_binding": {"status": "PASS"},
            "field_consistency": {"status": "PASS"},
            "decision_trace": [{"stage": "DOCUMENT_BINDING"}],
            "cutoff_available": True,
            "cutoff_result": {"测试状态": "FAIL"},
        }
    )
    assert patch["three_way"]["three_way_status"] == "PASS"
    assert patch["three_way_match"]["status"] == "PASS"
    assert patch["cutoff_test"]["status"] == "FAIL"


def test_invalidate_clears_split_keys():
    store = JobStore()
    job = store.create(title="adapt")
    jid = job["job_id"]
    patch = three_way_sample_patch(
        {"overall_status": "PASS", "three_way_status": "PASS", "cutoff_status": "PASS"}
    )
    store.update(jid, goal_ids=["gospd01030"], plan={"goal_ids": ["gospd01030"], "required_steps": ["three_way_cutoff", "conclusion_gate5"]}, **patch)
    store.invalidate_by_targets(jid, ["three_way"])
    after = store.get(jid)
    assert after["three_way"] is None
    assert after.get("three_way_match") is None
    assert after.get("cutoff_test") is None


def test_export_readiness_fail_released_by_gate5():
    job = {
        "classified": [{"file_name": "a.pdf", "doc_type": "invoice", "fields": {"orderNo": "SO25-0281"}}],
        "goal_ids": ["gospd01030"],
        "plan": {
            "goal_ids": ["gospd01030"],
            "required_steps": [
                "field_confirm",
                "relations_gate4",
                "three_way_cutoff",
                "conclusion_gate5",
            ],
        },
        "gospd_sample_results": {
            "SO25-0281": {
                "fields_confirmed": True,
                "matching_confirmed": True,
                "conclusion_confirmed": True,
                **three_way_sample_patch(
                    {
                        "three_way_status": "PASS",
                        "cutoff_status": "FAIL",
                        "overall_status": "FAIL",
                        "cutoff_available": True,
                        "cutoff_result": {"测试状态": "FAIL"},
                    }
                ),
            }
        },
        "advisory_candidates": [],
        "period_end": "2025-12-31",
    }
    readiness = build_export_readiness(job)
    by_id = {s["id"]: s for s in readiness["stages"]}
    assert by_id["cutoff"]["status"] == "DONE"
    assert "放行" in by_id["cutoff"]["reason"]
    assert by_id["period_end"]["status"] == "DONE"
    assert readiness["ready"] is True


def test_export_readiness_blocks_without_period_end_for_01030():
    job = {
        "classified": [
            {"file_name": "a.pdf", "doc_type": "invoice", "fields": {"orderNo": "SO25-0281"}}
        ],
        "goal_ids": ["gospd01030"],
        "plan": {
            "goal_ids": ["gospd01030"],
            "required_steps": [
                "field_confirm",
                "relations_gate4",
                "three_way_cutoff",
                "conclusion_gate5",
            ],
        },
        "gospd_sample_results": {
            "SO25-0281": {
                "fields_confirmed": True,
                "matching_confirmed": True,
                "conclusion_confirmed": True,
                **three_way_sample_patch(
                    {
                        "three_way_status": "PASS",
                        "cutoff_status": "PASS",
                        "overall_status": "PASS",
                        "cutoff_available": True,
                        "cutoff_result": {"测试状态": "PASS"},
                    }
                ),
            }
        },
        "advisory_candidates": [],
    }
    readiness = build_export_readiness(job)
    by_id = {s["id"]: s for s in readiness["stages"]}
    assert by_id["period_end"]["blocking"] is True
    assert readiness["ready"] is False
    assert "period_end" in by_id["period_end"]["reason"] or "报告期末" in by_id[
        "period_end"
    ]["reason"]


def test_clear_three_way_fields_helper():
    d = {"three_way": {"x": 1}, "three_way_match": {"y": 1}, "cutoff_test": {"z": 1}, "keep": 1}
    clear_three_way_fields(d)
    assert d["three_way"] is None
    assert d["three_way_match"] is None
    assert d["cutoff_test"] is None
    assert d["keep"] == 1

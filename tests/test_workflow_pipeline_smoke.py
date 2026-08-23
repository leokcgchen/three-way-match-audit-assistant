"""工作台 pipeline 冒烟：plan → job → seed → confirm fields。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import app
from src.workflow.job_store import JOB_STORE
from src.workflow.recipes import resolve_workflow_plan


def test_resolve_plan_and_confirm_fields_path():
    plan = resolve_workflow_plan(["gospd01010"])
    assert "field_confirm" in plan["required_steps"]
    assert "amount_test" in plan["required_steps"]
    assert "three_way_cutoff" in plan["required_steps"]

    job = JOB_STORE.create(title="smoke")
    job_id = job["job_id"]
    job = JOB_STORE.set_goals(job_id, ["gospd01010"])
    assert "evidence_match" in job["plan"]["required_steps"]

    client = TestClient(app)
    r = client.post(f"/api/v1/workflow/jobs/{job_id}/seed-demo")
    assert r.status_code == 200
    body = r.json()
    assert len(body.get("classified") or []) >= 1
    assert body.get("fields_confirmed") is False

    r2 = client.post(f"/api/v1/workflow/jobs/{job_id}/hitl/fields/confirm")
    assert r2.status_code == 200
    confirmed = r2.json()
    assert confirmed.get("fields_confirmed") is True
    assert confirmed.get("fields_confirm_sig")
    # API 确认须写入 ACCEPTED，否则底稿 rule_readable 空读
    from src.models.field_values import rule_readable_fields

    for doc in confirmed.get("classified") or []:
        readable = rule_readable_fields(doc)
        assert readable, f"confirmed doc has no ACCEPTED fields: {doc.get('file_name')}"


def test_ocr_status_endpoint():
    client = TestClient(app)
    r = client.get("/api/v1/workflow/ocr-status")
    assert r.status_code == 200
    data = r.json()
    assert "configured" in data
    assert "message" in data

"""字段对照行级验证写入 HITL。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import app
from src.workflow.job_store import JOB_STORE


def test_field_row_verify_persists_on_job():
    client = TestClient(app)
    job = JOB_STORE.create(title="row-verify")
    jid = job["job_id"]
    JOB_STORE.update(jid, active_chain_id="SO25-0001")

    r = client.post(
        f"/api/v1/workflow/jobs/{jid}/hitl/field-row/verify",
        json={"chain_id": "SO25-0001", "field_key": "totalAmount", "verified": True, "reason": "人工核对"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    ver = (body.get("field_row_verifications") or {}).get("SO25-0001") or {}
    assert ver.get("totalAmount", {}).get("verified") is True

    r2 = client.post(
        f"/api/v1/workflow/jobs/{jid}/hitl/field-row/verify",
        json={"chain_id": "SO25-0001", "field_key": "totalAmount", "verified": False},
    )
    assert r2.status_code == 200
    fresh = JOB_STORE.get(jid) or {}
    assert "totalAmount" not in ((fresh.get("field_row_verifications") or {}).get("SO25-0001") or {})

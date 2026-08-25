"""字段确认必须绑定调用方指定的业务链。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import app
from src.models.field_values import get_verified_value, seed_field_meta
from src.workflow.job_store import JOB_STORE


def _doc(chain_id: str, name: str) -> dict:
    doc = {
        "file_name": name,
        "doc_type": "order",
        "fields": {"orderNo": chain_id, "totalAmount": 100},
    }
    seed_field_meta(doc, source="test")
    return doc


def test_confirm_fields_uses_request_chain_not_the_jobs_active_chain():
    """Concurrent tabs must not confirm whichever chain was most recently made active."""
    client = TestClient(app)
    job = JOB_STORE.create(title="confirm-explicit-chain")
    chain_a = _doc("SO25-0281", "a.pdf")
    chain_b = _doc("SO25-0282", "b.pdf")
    JOB_STORE.update(
        job["job_id"],
        goal_ids=["gospd01010"],
        classified=[chain_a, chain_b],
        active_chain_id="SO25-0281",
    )

    response = client.post(
        f"/api/v1/workflow/jobs/{job['job_id']}/hitl/fields/confirm",
        json={"chain_id": "SO25-0282"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["active_chain_id"] == "SO25-0281"
    samples = body.get("gospd_sample_results") or {}
    assert samples["SO25-0282"]["fields_confirmed"] is True
    assert not samples.get("SO25-0281", {}).get("fields_confirmed")
    # The job-level mirror stays the UI cursor's (SO25-0281) state, never B's.
    assert body["fields_confirmed"] is False
    assert body.get("fields_confirm_sig") in {None, ""}
    by_name = {d["file_name"]: d for d in body["classified"]}
    assert get_verified_value(by_name["b.pdf"], "totalAmount") == 100
    assert get_verified_value(by_name["a.pdf"], "totalAmount") is None


def test_confirm_fields_rejects_a_chain_that_does_not_exist_in_the_job():
    client = TestClient(app)
    job = JOB_STORE.create(title="confirm-unknown-chain")
    JOB_STORE.update(
        job["job_id"],
        goal_ids=["gospd01010"],
        classified=[_doc("SO25-0281", "a.pdf")],
        active_chain_id="SO25-0281",
    )

    response = client.post(
        f"/api/v1/workflow/jobs/{job['job_id']}/hitl/fields/confirm",
        json={"chain_id": "SO25-9999"},
    )

    assert response.status_code == 400
    assert "SO25-9999" in response.json()["detail"]

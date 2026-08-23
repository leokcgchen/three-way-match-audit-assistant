from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import app
from src.models.advisory_candidates import new_advisory_candidate
from src.workflow.job_store import JOB_STORE


def _seed_event_job() -> str:
    job = JOB_STORE.create(title="events-api")
    candidate = new_advisory_candidate(
        task_type="FIELD_GAP_FILL",
        business_id="SO25-0281",
        evidence={"source_doc": "invoice.pdf", "page": 1},
        payload={"field_name": "totalAmount", "value": 100},
        fingerprint="events-api",
    )
    JOB_STORE.update(job["job_id"], advisory_candidates=[candidate])
    return job["job_id"]


def test_events_endpoint_returns_open_events_and_summary():
    job_id = _seed_event_job()
    response = TestClient(app).get(f"/api/v1/workflow/jobs/{job_id}/events")
    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {"events", "summary"}
    assert body["summary"]["open"] == 1
    assert all(row["state"] == "OPEN" for row in body["events"])


def test_override_requires_reason_at_api_boundary():
    job_id = _seed_event_job()
    client = TestClient(app)
    event_id = client.get(f"/api/v1/workflow/jobs/{job_id}/events").json()["events"][0]["event_id"]
    response = client.post(
        f"/api/v1/workflow/jobs/{job_id}/events/{event_id}/decision",
        json={"decision": "OVERRIDE", "value": "2025-12-31", "reason": ""},
    )
    assert response.status_code == 422


def test_events_endpoint_can_include_resolved_history():
    job_id = _seed_event_job()
    client = TestClient(app)
    event = client.get(f"/api/v1/workflow/jobs/{job_id}/events").json()["events"][0]
    decided = client.post(
        f"/api/v1/workflow/jobs/{job_id}/events/{event['event_id']}/decision",
        json={"decision": "ACCEPT_AI", "operator": "api-auditor"},
    )
    assert decided.status_code == 200
    hidden = client.get(f"/api/v1/workflow/jobs/{job_id}/events").json()
    assert hidden["events"] == []
    history = client.get(
        f"/api/v1/workflow/jobs/{job_id}/events?include_passed=true"
    ).json()
    assert history["summary"]["passed"] == 1
    assert history["events"][0]["state"] == "RESOLVED"

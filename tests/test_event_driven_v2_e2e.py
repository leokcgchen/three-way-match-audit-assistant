from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import app
from src.workflow.job_store import JOB_STORE


def test_v2_normal_and_exception_paths_share_one_event_source_of_truth(monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(settings, "QUALITY_RISK_SAMPLE_RATE", 0.0)
    monkeypatch.setattr(settings, "QUALITY_RANDOM_SAMPLE_RATE", 0.0)
    client = TestClient(app)
    created = client.post(
        "/api/v1/workflow/jobs", json={"title": "V2 端到端验收"}
    )
    assert created.status_code == 200
    job_id = created.json()["job_id"]

    populated = client.put(
        f"/api/v1/workflow/jobs/{job_id}/sample-population",
        json={
            "business_ids": ["SO25-0001", "SO25-0002"],
            "source": "e2e",
            "note": "一笔正常、一笔缺件",
        },
    )
    assert populated.status_code == 200

    JOB_STORE.update(
        job_id,
        goal_ids=["gospd01030"],
        plan={
            "goal_ids": ["gospd01030"],
            "required_steps": ["conclusion_gate5"],
            "goals": [],
        },
        classified=[
            {
                "file_name": "SO25-0001-发票.pdf",
                "doc_type": "invoice",
                "declared_business_ids": ["SO25-0001"],
                "fields": {
                    "invoiceNo": "INV-001",
                    "buyerName": "测试客户",
                    "totalAmount": 100,
                    "documentDate": "2025-12-30",
                },
            }
        ],
    )

    event_response = client.get(f"/api/v1/workflow/jobs/{job_id}/events")
    assert event_response.status_code == 200
    events = event_response.json()["events"]
    assert all(row["chain_id"] != "SO25-0001" for row in events)
    missing = next(row for row in events if row["chain_id"] == "SO25-0002")
    assert missing["event_type"] == "MISSING_DOCUMENT"
    assert missing["action_step"] == "sample_desk"

    readiness = client.get(
        f"/api/v1/workflow/jobs/{job_id}/export-readiness"
    )
    assert readiness.status_code == 200
    assert readiness.json()["ready"] is False
    event_gate = next(
        stage
        for stage in readiness.json()["stages"]
        if stage["id"] == "review_events"
    )
    assert event_gate["blocking"] is True
    assert event_gate["action"]["step"] == "event_review"

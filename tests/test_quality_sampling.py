from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import app
from src.workflow.job_store import JOB_STORE
from src.workflow.quality_sampling import (
    build_quality_sample_selections,
    select_quality_samples,
)


def _job() -> dict:
    chain_ids = ["SO25-0001", "SO25-0002", "SO25-0003"]
    classified = []
    for index, chain_id in enumerate(chain_ids, start=1):
        if chain_id == "SO25-0003":
            continue
        classified.append(
            {
                "file_name": f"{chain_id}.pdf",
                "doc_type": "invoice",
                "declared_business_ids": [chain_id],
                "fields": {
                    "invoiceNo": f"INV-{index}",
                    "buyerName": "测试客户",
                    "totalAmount": 100 * index,
                    "documentDate": "2025-12-30",
                },
            }
        )
    return {
        "job_id": "quality-job",
        "goal_ids": ["gospd01030"],
        "plan": {
            "goal_ids": ["gospd01030"],
            "required_steps": ["conclusion_gate5"],
        },
        "period_end": "2025-12-31",
        "sample_population": {
            "business_ids": chain_ids,
            "rows": [
                {"business_id": "SO25-0001", "book_amount": 100, "book_date": "2025-12-01"},
                {"business_id": "SO25-0002", "book_amount": 200, "book_date": "2025-12-30"},
                {"business_id": "SO25-0003", "book_amount": 999, "book_date": "2025-12-31"},
            ],
        },
        "classified": classified,
        "gospd_sample_results": {},
    }


def test_sampling_is_stable_for_same_job_and_seed():
    job = _job()
    first = select_quality_samples(job, risk_rate=0.5, random_rate=0.5, seed="v2")
    second = select_quality_samples(job, risk_rate=0.5, random_rate=0.5, seed="v2")
    assert first == second
    assert first


def test_open_or_failed_chains_are_not_quality_samples():
    selected = select_quality_samples(_job(), risk_rate=1, random_rate=1, seed="v2")
    assert "SO25-0003" not in selected
    assert set(selected) == {"SO25-0001", "SO25-0002"}


def test_chain_with_non_desk_review_event_is_not_quality_sampled():
    job = _job()
    job["classified"][0].update(
        ledger_evaluated=True,
        ledger_match_ok=False,
        ledger_amount=999,
    )
    selected = select_quality_samples(job, risk_rate=1, random_rate=1, seed="v2")
    assert "SO25-0001" not in selected
    assert "SO25-0002" in selected


def test_selection_records_explain_risk_and_random_routes():
    rows = build_quality_sample_selections(
        _job(), risk_rate=0.5, random_rate=0.5, seed="v2"
    )
    assert {row["chain_id"] for row in rows} == {"SO25-0001", "SO25-0002"}
    assert all(row["selection_id"].startswith("qs_") for row in rows)
    assert all(row["reason"] for row in rows)
    assert {row["route"] for row in rows}.issubset({"RISK", "RANDOM", "RISK_AND_RANDOM"})


def test_events_api_materializes_quality_samples_once_for_eligible_chains():
    created = JOB_STORE.create(title="quality-api")
    client = TestClient(app)
    before_completion = client.get(
        f"/api/v1/workflow/jobs/{created['job_id']}/events"
    )
    assert before_completion.status_code == 200
    assert not any(
        row["event_type"] == "QUALITY_SAMPLE"
        for row in before_completion.json()["events"]
    )

    patch = _job()
    patch.pop("job_id", None)
    JOB_STORE.update(created["job_id"], **patch)

    first = client.get(f"/api/v1/workflow/jobs/{created['job_id']}/events")
    second = client.get(f"/api/v1/workflow/jobs/{created['job_id']}/events")

    assert first.status_code == 200
    first_ids = [
        row["event_id"]
        for row in first.json()["events"]
        if row["event_type"] == "QUALITY_SAMPLE"
    ]
    second_ids = [
        row["event_id"]
        for row in second.json()["events"]
        if row["event_type"] == "QUALITY_SAMPLE"
    ]
    assert first_ids
    assert first_ids == second_ids

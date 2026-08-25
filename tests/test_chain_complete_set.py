"""“本笔已齐套”是逐业务、可审计且不污染截止性的人工声明。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import app
from src.workflow.job_store import JOB_STORE


def _doc(chain_id: str, file_name: str) -> dict:
    return {
        "file_name": file_name,
        "doc_type": "order",
        "business_group_id": chain_id,
        "fields": {"orderNo": chain_id, "quantity": 100},
    }


def _job() -> dict:
    job = JOB_STORE.create(title="complete-set")
    sample_a = {
        "chain_id": "SO25-0001",
        "three_way": {"status": "PASS"},
        "three_way_match": {"status": "PASS"},
        "cutoff_test": {"status": "PASS"},
        "conclusion_confirmed": True,
        "conclusion_confirm_sig": "sig-a",
    }
    sample_b = {
        "chain_id": "SO25-0002",
        "three_way": {"status": "PASS"},
        "three_way_match": {"status": "PASS"},
        "cutoff_test": {"status": "PASS"},
        "conclusion_confirmed": True,
        "conclusion_confirm_sig": "sig-b",
    }
    return JOB_STORE.update(
        job["job_id"],
        goal_ids=["gospd01030"],
        sample_population={
            "business_ids": ["SO25-0001", "SO25-0002"],
            "count": 2,
            "source": "test",
            "rows": [],
        },
        classified=[_doc("SO25-0001", "a.pdf"), _doc("SO25-0002", "b.pdf")],
        gospd_sample_results={"SO25-0001": sample_a, "SO25-0002": sample_b},
        active_chain_id="SO25-0002",
    )


def test_complete_set_is_saved_only_for_the_requested_chain() -> None:
    job = _job()
    client = TestClient(app)

    response = client.put(
        f"/api/v1/workflow/jobs/{job['job_id']}/chains/SO25-0001/complete-set",
        json={"complete_set": True},
    )

    assert response.status_code == 200, response.text
    samples = response.json()["gospd_sample_results"]
    assert samples["SO25-0001"]["complete_set"] is True
    assert samples["SO25-0002"].get("complete_set") in {None, False}


def test_complete_set_change_clears_only_that_chains_three_way_and_conclusion() -> None:
    job = _job()
    client = TestClient(app)

    response = client.put(
        f"/api/v1/workflow/jobs/{job['job_id']}/chains/SO25-0001/complete-set",
        json={"complete_set": True},
    )

    assert response.status_code == 200, response.text
    samples = response.json()["gospd_sample_results"]
    assert samples["SO25-0001"]["three_way"] is None
    assert samples["SO25-0001"]["three_way_match"] is None
    assert samples["SO25-0001"]["cutoff_test"] == {"status": "PASS"}
    assert samples["SO25-0001"]["conclusion_confirmed"] is False
    assert samples["SO25-0001"]["conclusion_confirm_sig"] is None
    assert samples["SO25-0002"]["three_way"] == {"status": "PASS"}
    assert samples["SO25-0002"]["conclusion_confirmed"] is True


def test_complete_set_rejects_unknown_chain() -> None:
    job = _job()
    client = TestClient(app)

    response = client.put(
        f"/api/v1/workflow/jobs/{job['job_id']}/chains/SO25-0404/complete-set",
        json={"complete_set": True},
    )

    assert response.status_code == 404
    assert "SO25-0404" in response.json()["detail"]


def test_chains_response_exposes_complete_set_default_and_saved_values() -> None:
    job = _job()
    client = TestClient(app)

    before = client.get(f"/api/v1/workflow/jobs/{job['job_id']}/chains")
    assert before.status_code == 200
    assert {row["chain_id"]: row["complete_set"] for row in before.json()["chains"]} == {
        "SO25-0001": False,
        "SO25-0002": False,
    }

    saved = client.put(
        f"/api/v1/workflow/jobs/{job['job_id']}/chains/SO25-0001/complete-set",
        json={"complete_set": True},
    )
    assert saved.status_code == 200
    after = client.get(f"/api/v1/workflow/jobs/{job['job_id']}/chains")
    assert {row["chain_id"]: row["complete_set"] for row in after.json()["chains"]} == {
        "SO25-0001": True,
        "SO25-0002": False,
    }

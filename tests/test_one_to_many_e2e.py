"""一对多三单从逐笔齐套声明到持久化结果的真实工作流回归。"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient

from src.api.main import app
from src.workflow.job_store import JOB_STORE
from src.workflow.signatures import fields_signature


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "one_to_many"


def _classified(name: str) -> list[dict]:
    pack = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
    docs = deepcopy(pack["classified"])
    for doc in docs:
        doc["business_group_id"] = "SO001"
    return docs


def _seed_job(fixture_name: str) -> dict:
    docs = _classified(fixture_name)
    job = JOB_STORE.create(title=f"one-to-many:{fixture_name}")
    sample = {
        "chain_id": "SO001",
        "fields_confirmed": True,
        "fields_confirm_sig": fields_signature(docs),
        "complete_set": False,
    }
    return JOB_STORE.update(
        job["job_id"],
        goal_ids=["gospd01030"],
        plan={
            "goal_ids": ["gospd01030"],
            "required_steps": ["three_way_cutoff"],
            "goals": [],
        },
        sample_population={
            "business_ids": ["SO001"],
            "count": 1,
            "source": "one-to-many-e2e",
            "rows": [],
        },
        classified=docs,
        gospd_sample_results={"SO001": sample},
        active_chain_id="SO001",
    )


def test_complete_one_to_many_set_is_accumulated_persisted_and_exposed() -> None:
    client = TestClient(app)
    job = _seed_job("classified_complete.json")
    job_id = job["job_id"]

    declared = client.put(
        f"/api/v1/workflow/jobs/{job_id}/chains/SO001/complete-set",
        json={"complete_set": True},
    )
    assert declared.status_code == 200, declared.text

    reviewed = client.post(
        f"/api/v1/workflow/jobs/{job_id}/three-way-cutoff",
        json={},
    )
    assert reviewed.status_code == 200, reviewed.text
    sample = reviewed.json()["gospd_sample_results"]["SO001"]
    row = sample["three_way"]["fulfillment"]["rows"][0]
    assert sample["complete_set"] is True
    assert row["received_qty"] == "100"
    assert row["invoiced_qty"] == "100"
    assert sample["three_way"]["fulfillment"]["light"] == "GREEN"

    chains = client.get(f"/api/v1/workflow/jobs/{job_id}/chains")
    assert chains.status_code == 200, chains.text
    chain = next(item for item in chains.json()["chains"] if item["chain_id"] == "SO001")
    assert chain["complete_set"] is True


def test_partial_invoice_turns_from_yellow_to_red_after_complete_set_declaration() -> None:
    client = TestClient(app)
    job = _seed_job("classified_partial.json")
    job_id = job["job_id"]

    first = client.post(
        f"/api/v1/workflow/jobs/{job_id}/three-way-cutoff",
        json={},
    )
    assert first.status_code == 200, first.text
    before = first.json()["gospd_sample_results"]["SO001"]["three_way"]["fulfillment"]
    assert before["light"] == "YELLOW"
    assert "SET_CLAIMED_INCOMPLETE" not in before["flags"]

    declared = client.put(
        f"/api/v1/workflow/jobs/{job_id}/chains/SO001/complete-set",
        json={"complete_set": True},
    )
    assert declared.status_code == 200, declared.text
    rerun = client.post(
        f"/api/v1/workflow/jobs/{job_id}/three-way-cutoff",
        json={},
    )
    assert rerun.status_code == 200, rerun.text
    after = rerun.json()["gospd_sample_results"]["SO001"]["three_way"]["fulfillment"]
    assert after["light"] == "RED"
    assert "SET_CLAIMED_INCOMPLETE" in after["flags"]

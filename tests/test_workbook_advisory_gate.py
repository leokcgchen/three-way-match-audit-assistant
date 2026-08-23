"""底稿导向适配：Gate5/导出拦顾问待决。"""

from __future__ import annotations

import pytest

from src.models.advisory_candidates import new_advisory_candidate
from src.workflow.job_store import JOB_STORE


def test_confirm_conclusion_blocks_pending_advisory():
    job = JOB_STORE.create(title="wb-gate")
    jid = job["job_id"]
    cand = new_advisory_candidate(
        task_type="FIELD_GAP_FILL",
        payload={"field_name": "quantity", "value": 1},
        evidence={"excerpt": "x"},
        business_id="SO25-0001",
        fingerprint="gate-pending",
        invalidates=["amount"],
    )
    JOB_STORE.update(
        jid,
        plan={
            "goal_ids": [],
            "required_steps": ["amount_test"],
            "goals": [],
            "skipped_steps": [],
        },
        amount_test={"status": "PASS"},
        fields_confirmed=True,
        matching_confirmed=True,
        advisory_candidates=[cand],
    )
    with pytest.raises(ValueError, match="顾问候选"):
        JOB_STORE.confirm_conclusion(jid)

    # 拒绝后可确认
    cand["status"] = "REJECTED"
    JOB_STORE.update(jid, advisory_candidates=[cand])
    out = JOB_STORE.confirm_conclusion(jid)
    assert out["conclusion_confirmed"] is True

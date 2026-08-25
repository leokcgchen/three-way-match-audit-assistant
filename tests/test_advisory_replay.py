"""#4 定向失效与顾问决议复跑。"""

from __future__ import annotations

from src.audit.gap_fill_replay import apply_advisory_decision
from src.models.advisory_candidates import new_advisory_candidate
from src.workflow.job_store import JOB_STORE


def _seed_job(**extra):
    job = JOB_STORE.create(title="adv-replay")
    jid = job["job_id"]
    plan = {
        "goal_ids": [],
        "goals": [],
        "required_steps": [
            "evidence_match",
            "amount_test",
            "contract_terms",
            "three_way_cutoff",
        ],
        "skipped_steps": [],
    }
    patch = {
        "plan": plan,
        "goal_ids": [],
        "classified": [
            {
                "file_name": "inv.pdf",
                "doc_type": "invoice",
                "fields": {"amountInclTax": 100},
                "raw_text": "价税合计 100",
            }
        ],
        "fields_confirmed": True,
        "fields_confirm_sig": "sig",
        "matching_confirmed": True,
        "matching_confirm_sig": "msig",
        "evidence": {"status": "PASS"},
        "amount_test": {"status": "PASS", "stale": False},
        "contract_terms": {"status": "PASS"},
        "three_way": {"overall_status": "PASS"},
        "conclusion_confirmed": True,
        "conclusion_confirm_sig": "csig",
        "workbook_path": "x.xlsx",
        "workbook_paths": ["x.xlsx"],
        **extra,
    }
    return JOB_STORE.update(jid, **patch)


def test_invalidate_by_targets_amount_keeps_matching():
    job = _seed_job()
    jid = job["job_id"]
    expanded = JOB_STORE.invalidate_by_targets(jid, ["amount", "gate5"])
    assert "amount" in expanded
    assert "gate5" in expanded
    assert "workbook" in expanded
    fresh = JOB_STORE.get(jid)
    assert fresh["amount_test"] is None
    assert fresh["conclusion_confirmed"] is False
    assert fresh["matching_confirmed"] is True
    assert fresh["evidence"] == {"status": "PASS"}
    assert fresh["contract_terms"] == {"status": "PASS"}
    assert fresh["workbook_path"] is None


def test_invalidate_evidence_cascades_tests():
    job = _seed_job()
    jid = job["job_id"]
    JOB_STORE.invalidate_by_targets(jid, ["evidence"])
    fresh = JOB_STORE.get(jid)
    assert fresh["matching_confirmed"] is False
    assert fresh["amount_test"] is None
    assert fresh["three_way"] is None
    assert fresh["evidence"] == {"status": "PASS"}  # 证据体保留，待重跑覆盖


def test_gospd_field_gap_fill_keeps_gate3_and_gate4():
    """已过 Gate3/4 后接受字段顾问：只脏测项，不逼重走字段/勾稽。"""
    from src.workflow.signatures import fields_signature, matching_signature

    job = JOB_STORE.create(title="gospd-soft-gates")
    jid = job["job_id"]
    docs = [
        {
            "file_name": "SO25-0281_order.pdf",
            "doc_type": "order",
            "fields": {"orderNo": "SO25-0281", "totalAmount": "100"},
        },
        {
            "file_name": "SO25-0281_inv.pdf",
            "doc_type": "invoice",
            "fields": {"orderNo": "SO25-0281", "invoiceNo": "INV1", "totalAmount": "100"},
        },
    ]
    ev = {"status": "OK", "nodes": [], "links": []}
    sig = fields_signature(docs)
    msig = matching_signature(evidence=ev, relations=[], duplicates={})
    cand = new_advisory_candidate(
        task_type="FIELD_GAP_FILL",
        business_id="SO25-0281",
        payload={
            "field_name": "buyerName",
            "normalized_candidate": "测试买方",
            "file_name": "SO25-0281_inv.pdf",
        },
        evidence={"source_doc": "SO25-0281_inv.pdf"},
        # 旧候选可能仍带 fields；软路径须剥掉
        invalidates=["fields", "evidence", "amount", "cutoff", "terms", "three_way", "gate5"],
    )
    JOB_STORE.update(
        jid,
        goal_ids=["gospd01030"],
        plan={
            "goal_ids": ["gospd01030"],
            "required_steps": [
                "field_confirm",
                "relations_gate4",
                "amount_test",
                "three_way_cutoff",
                "conclusion_gate5",
            ],
            "goals": [],
            "skipped_steps": [],
        },
        classified=docs,
        active_chain_id="SO25-0281",
        fields_confirmed=True,
        fields_confirm_sig=sig,
        matching_confirmed=True,
        matching_confirm_sig=msig,
        evidence=ev,
        relations=[],
        duplicates={},
        amount_test={"status": "PASS"},
        three_way={"overall_status": "PASS"},
        advisory_candidates=[cand],
        gospd_sample_results={
            "SO25-0281": {
                "fields_confirmed": True,
                "fields_confirm_sig": sig,
                "matching_confirmed": True,
                "matching_confirm_sig": msig,
                "evidence": ev,
                "relations": [],
                "duplicates": {},
                "amount_test": {"status": "PASS"},
                "three_way": {"overall_status": "PASS"},
            }
        },
    )
    out = apply_advisory_decision(jid, cand["candidate_id"], "VERIFIED", auto_replay=False)
    assert "fields" not in out["invalidates"]
    assert "evidence" not in out["invalidates"]
    assert "three_way" in out["invalidates"]
    fresh = out["job"]
    sample = (fresh.get("gospd_sample_results") or {}).get("SO25-0281") or {}
    assert fresh.get("fields_confirmed") is True
    assert sample.get("fields_confirmed") is True
    assert fresh.get("matching_confirmed") is True
    assert sample.get("matching_confirmed") is True
    assert sample.get("three_way") is None
    JOB_STORE.require_fields_confirmed(jid)
    JOB_STORE.require_matching_confirmed(jid)


def test_apply_advisory_verified_amount_replays(monkeypatch):
    cand = new_advisory_candidate(
        task_type="AMOUNT_GAP_FILL",
        kind="fact",
        business_id="SO1",
        payload={
            "field_name": "quantity",
            "normalized_candidate": 2,
            "file_name": "inv.pdf",
            "excerpt": "价税合计 100",
            "confidence": 0.9,
        },
        evidence={"excerpt": "价税合计 100", "source_doc": "inv.pdf"},
        verify={"passed": True, "reason": "ok"},
        invalidates=["amount", "gate5"],
        fingerprint="qty|2|inv.pdf",
    )
    job = _seed_job(advisory_candidates=[cand])
    jid = job["job_id"]

    calls = {"amount": 0}

    def _fake_amount(docs):
        calls["amount"] += 1
        return {"status": "WARNING", "replayed": True}

    monkeypatch.setattr("src.audit.gap_fill_replay.run_amount", _fake_amount)

    out = apply_advisory_decision(jid, cand["candidate_id"], "VERIFIED", auto_replay=True)
    assert out["after"]["status"] == "VERIFIED"
    assert "amount" in out["invalidates"]
    assert "amount" in out["replayed"]
    assert calls["amount"] == 1
    fresh = JOB_STORE.get(jid)
    assert fresh["amount_test"]["replayed"] is True
    assert fresh["matching_confirmed"] is True
    # 候选已写入字段三值
    meta = (fresh["classified"][0].get("_field_meta") or {}).get("quantity") or {}
    assert meta.get("normalized_candidate") == 2


def test_apply_advisory_rejected_does_not_invalidate():
    cand = new_advisory_candidate(
        task_type="AMOUNT_GAP_FILL",
        payload={"field_name": "quantity", "value": 9},
        evidence={"excerpt": "x", "source_doc": "inv.pdf"},
        invalidates=["amount", "gate5"],
        fingerprint="reject-case",
    )
    job = _seed_job(advisory_candidates=[cand])
    jid = job["job_id"]
    out = apply_advisory_decision(jid, cand["candidate_id"], "REJECTED", reason="不适用")
    assert out["after"]["status"] == "REJECTED"
    assert out["invalidates"] == []
    fresh = JOB_STORE.get(jid)
    assert fresh["amount_test"]["status"] == "PASS"
    assert fresh["conclusion_confirmed"] is True


def test_gap_fill_replay_uses_01030_year_end_calendar_only(monkeypatch):
    from src.audit.gap_fill_replay import _replay_dirty

    calls = []

    def _fake_three_way(docs, **kwargs):
        calls.append(kwargs)
        return {"three_way_status": "PASS", "cutoff_status": "PASS"}

    monkeypatch.setattr("src.audit.gap_fill_replay.run_three_way", _fake_three_way)
    job = _seed_job(
        goal_ids=["gospd01030"],
        plan={"goal_ids": ["gospd01030"], "required_steps": ["three_way_cutoff"]},
        period_end="2025-12-31",
        calendar_mode=None,
        fiscal_year_start="2025-01-01",
    )
    _replay_dirty(job["job_id"], ["cutoff"])
    assert calls[-1] == {
        "period_end": "2025-12-31",
        "calendar_mode": "period_end_only",
        "fiscal_year_start": "2025-01-01",
    }

    job = _seed_job(
        goal_ids=["gospd01010"],
        plan={"goal_ids": ["gospd01010"], "required_steps": ["three_way_cutoff"]},
        period_end="2025-12-31",
        calendar_mode=None,
    )
    _replay_dirty(job["job_id"], ["cutoff"])
    assert calls[-1]["calendar_mode"] is None

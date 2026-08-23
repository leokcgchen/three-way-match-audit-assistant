"""本笔人工核对：字段确认 + 自动匹配 + 采纳关系 + Gate4。"""

from __future__ import annotations

from src.models.relation_candidates import new_relation, pending_proposed
from src.workflow.job_store import JOB_STORE


def test_confirm_chain_linkage_auto_evidence_and_accept_relations(monkeypatch):
    """无 evidence 时自动匹配，并顺带确认 PROPOSED 关系后写入 Gate4。"""

    def fake_seed(self, job_id, *, with_llm_disambiguation=False):
        job = self.get(job_id)
        assert job
        cid = job.get("active_chain_id") or "SO25-0296"
        evidence = {"matched": True, "links": [], "status": "PASS"}
        rels = [
            new_relation(
                from_id="a.pdf",
                to_id=f"b{i}.pdf",
                rel_type="SUPPORTS",
                status="PROPOSED",
                shared_keys=["SO25-0296"],
            )
            for i in range(2)
        ]
        dups = {"ran": True, "findings": [], "summary": {"total": 0}}
        return self.save_chain_sample(
            job_id,
            cid,
            {
                "evidence": evidence,
                "relations": rels,
                "duplicates": dups,
                "matching_confirmed": False,
            },
        )

    monkeypatch.setattr(
        JOB_STORE.__class__,
        "seed_evidence_match",
        fake_seed,
    )

    job = JOB_STORE.create(title="hitl-auto")
    jid = job["job_id"]
    docs = [
        {
            "file_name": "a.pdf",
            "doc_type": "invoice",
            "fields": {"orderNo": "SO25-0296", "totalAmount": "1"},
        },
        {
            "file_name": "b.pdf",
            "doc_type": "sales_order",
            "fields": {"orderNo": "SO25-0296", "totalAmount": "1"},
        },
    ]
    JOB_STORE.update(
        jid,
        goal_ids=["gospd01030"],
        plan={
            "goal_ids": ["gospd01030"],
            "required_steps": [
                "field_confirm",
                "evidence_match",
                "relations_gate4",
                "conclusion_gate5",
            ],
            "goals": [],
            "skipped_steps": [],
        },
        classified=docs,
        active_chain_id="SO25-0296",
        gospd_sample_results={},
    )

    out = JOB_STORE.confirm_chain_linkage(
        jid, auto_evidence=True, auto_accept_relations=True
    )
    assert out["fields_confirmed"] is True
    assert out["matching_confirmed"] is True
    assert out["next_action"] == "done"
    assert out["evidence_seeded"] is True
    job2 = out["job"]
    assert pending_proposed(job2.get("relations") or []) == []
    sample = (job2.get("gospd_sample_results") or {}).get("SO25-0296") or {}
    assert sample.get("matching_confirmed") is True
    assert sample.get("fields_confirmed") is True


def test_confirm_chain_linkage_blocks_on_duplicate_hint(monkeypatch):
    def fake_seed(self, job_id, *, with_llm_disambiguation=False):
        cid = "SO1"
        return self.save_chain_sample(
            job_id,
            cid,
            {
                "evidence": {"matched": True},
                "relations": [],
                "duplicates": {
                    "ran": True,
                    "findings": [{"invoice_no": "X"}],
                    "summary": {"total": 1},
                    "blocks_downstream_hint": True,
                },
                "matching_confirmed": False,
            },
        )

    monkeypatch.setattr(JOB_STORE.__class__, "seed_evidence_match", fake_seed)

    job = JOB_STORE.create(title="hitl-dup")
    jid = job["job_id"]
    JOB_STORE.update(
        jid,
        goal_ids=["gospd01030"],
        plan={
            "goal_ids": ["gospd01030"],
            "required_steps": ["field_confirm", "relations_gate4"],
            "goals": [],
            "skipped_steps": [],
        },
        classified=[
            {
                "file_name": "a.pdf",
                "doc_type": "invoice",
                "fields": {"orderNo": "SO1"},
            }
        ],
        active_chain_id="SO1",
    )
    out = JOB_STORE.confirm_chain_linkage(jid)
    assert out["fields_confirmed"] is True
    assert out["matching_confirmed"] is False
    assert out["next_action"] == "ack_duplicates"

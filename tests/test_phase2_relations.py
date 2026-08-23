"""Phase 2：候选关系、重复号检测、覆盖地图 Gate4/5。"""

from __future__ import annotations

from src.audit.coverage_map import build_coverage_map
from src.audit.duplicate_detector import detect_duplicates
from src.audit.relation_proposer import propose_relations_from_evidence
from src.models.relation_candidates import (
    decide_relation,
    new_relation,
    pending_proposed,
    summary_counts,
    upsert_relations,
)


def test_relation_status_machine_preserves_decided():
    a = new_relation(
        from_id="合同.pdf",
        to_id="订单.pdf",
        rel_type="LINKED_CONTRACT_TO_ORDER",
        shared_keys=["SO25-0001"],
        excerpt="共享 SO25-0001",
    )
    store = [a]
    store, before, after = decide_relation(
        store, a["relation_id"], "VERIFIED", actor="tester", reason="核对通过"
    )
    assert before["status"] == "PROPOSED"
    assert after["status"] == "VERIFIED"
    assert pending_proposed(store) == []

    refreshed = new_relation(
        from_id="合同.pdf",
        to_id="订单.pdf",
        rel_type="LINKED_CONTRACT_TO_ORDER",
        shared_keys=["SO25-0001"],
        excerpt="新摘录",
        status="PROPOSED",
    )
    merged = upsert_relations(store, [refreshed], preserve_decided=True)
    assert len(merged) == 1
    assert merged[0]["status"] == "VERIFIED"
    assert merged[0]["excerpt"] == "新摘录"


def test_duplicate_invoice_and_multi_version_contract():
    docs = [
        {
            "file_name": "inv_a.pdf",
            "doc_type": "invoice",
            "fields": {"invoiceNo": "123456789012"},
        },
        {
            "file_name": "inv_b.pdf",
            "doc_type": "invoice",
            "fields": {"invoiceNo": "123456789012"},
        },
        {
            "file_name": "ht_v1.pdf",
            "doc_type": "contract",
            "fields": {"contractNo": "HT25-0001"},
        },
        {
            "file_name": "ht_v2.pdf",
            "doc_type": "contract",
            "fields": {"contractNo": "HT25-0001"},
        },
    ]
    out = detect_duplicates(docs)
    assert out["ran"] is True
    types = {f["issue_type"] for f in out["findings"]}
    assert "DUPLICATE_INVOICE_NO" in types
    assert "MULTI_VERSION_CONTRACT" in types
    assert out["blocks_downstream_hint"] is True


def test_propose_relations_from_evidence_links():
    evidence = {
        "status": "WARNING",
        "nodes": [
            {"role": "contract", "file_name": "c.pdf", "linked": True, "biz_keys": ["SO1"]},
            {"role": "order", "file_name": "o.pdf", "linked": True, "biz_keys": ["SO1"]},
            {"role": "invoice", "file_name": "i.pdf", "linked": True, "biz_keys": ["SO1"]},
        ],
        "links": [
            {"from_role": "contract", "to_role": "order", "shared_keys": ["SO1"]},
            {"from_role": "order", "to_role": "invoice", "shared_keys": ["SO1"]},
        ],
        "llm_disambiguation": {
            "ran": True,
            "proposals": [
                {
                    "file_name": "extra.pdf",
                    "disposition": "EXCLUDE",
                    "reason": "另一业务",
                    "excerpt": "合同号 HT-OTHER",
                }
            ],
        },
    }
    rels = propose_relations_from_evidence([], evidence)
    assert len(rels) >= 3
    counts = summary_counts(rels)
    assert counts["PROPOSED"] == counts["total"]
    assert any(r["rel_type"] == "EXCLUDE_FROM_CLUSTER" for r in rels)


def test_coverage_map_phase2_dimensions():
    cov = build_coverage_map(
        classified=[{"doc_type": "invoice"}, {"doc_type": "contract"}],
        evidence={"status": "PASS", "llm_disambiguation": {"ran": True}},
        fields_confirmed=True,
        matching_confirmed=True,
        conclusion_confirmed=False,
        relations=[
            {
                "relation_id": "x",
                "status": "VERIFIED",
                "from_id": "a",
                "to_id": "b",
            }
        ],
        duplicates={"ran": True, "summary": {"total": 1}, "findings": [{"finding_id": "1"}]},
    )
    by_id = {d["dimension_id"]: d for d in cov["dimensions"]}
    assert by_id["HITL_MATCH_CONFIRM"]["status"] == "CHECKED"
    assert by_id["RELATION_CANDIDATES"]["status"] == "CHECKED"
    assert by_id["DUPLICATE_DETECTION"]["status"] == "CHECKED"
    assert by_id["HITL_CONCLUSION_CONFIRM"]["status"] == "PARTIAL"
    assert cov["version"].startswith("coverage-map-v1")

"""三值字段、LLM 验证器、HITL 日志、覆盖地图、匹配消歧应用。"""

from __future__ import annotations

import json

from src.audit.coverage_map import build_coverage_map
from src.audit.hitl_log import append_hitl_event, hitl_log_path
from src.evidence_match.disambiguation import apply_disambiguation_proposal
from src.llm.verifier import excerpt_in_text, verify_claims
from src.models.field_values import (
    accept_all_current_fields,
    accept_field,
    effective_fields,
    seed_field_meta,
    set_candidate,
)


def test_three_value_raw_preserved_on_candidate():
    item = {"fields": {"orderNo": "SO25-002I"}, "file_name": "a.pdf"}
    seed_field_meta(item, source="ocr")
    set_candidate(item, "orderNo", "SO25-0021", source="llm", extractor="gap")
    meta = item["_field_meta"]["orderNo"]
    assert meta["raw_value"] == "SO25-002I"
    assert meta["normalized_candidate"] == "SO25-0021"
    assert meta["status"] == "UNRESOLVED"
    accept_field(item, "orderNo", "SO25-0021", source="manual")
    assert meta["accepted_value"] == "SO25-0021"
    assert meta["status"] == "ACCEPTED"
    assert effective_fields(item)["orderNo"] == "SO25-0021"
    # 已确认后再来候选：不降级、不改正式 fields
    set_candidate(item, "orderNo", "SO25-9999", source="llm", extractor="gap")
    assert meta["status"] == "ACCEPTED"
    assert meta["accepted_value"] == "SO25-0021"
    assert item["fields"]["orderNo"] == "SO25-0021"
    assert meta["normalized_candidate"] == "SO25-9999"


def test_accept_all_marks_accepted():
    item = {"fields": {"amount": 100, "orderNo": "SO1"}}
    keys = accept_all_current_fields(item)
    assert "amount" in keys and "orderNo" in keys
    assert item["_field_meta"]["amount"]["status"] == "ACCEPTED"


def test_verifier_rejects_bad_excerpt():
    text = "销售订单号：SO25-0021，金额100元。"
    result = verify_claims(
        [
            {
                "issue_code": "REBATE_TERM_AMBIGUOUS",
                "excerpt": "这段文字根本不在原文里啊啊啊啊",
                "confidence": 0.99,
            },
            {
                "issue_code": "REBATE_TERM_AMBIGUOUS",
                "excerpt": "销售订单号：SO25-0021",
                "confidence": 0.99,
            },
        ],
        full_text=text,
        allowed_codes={"REBATE_TERM_AMBIGUOUS"},
    )
    assert len(result.accepted) == 1
    assert len(result.rejected) == 1
    assert excerpt_in_text("销售订单号：SO25-0021", text)


def test_hitl_log_append(tmp_path, monkeypatch):
    monkeypatch.setattr("src.audit.hitl_log._log_dir", lambda: tmp_path)
    event = append_hitl_event(
        action="accept_field",
        entity_id="doc::orderNo",
        before="A",
        after="B",
        reason="OCR纠错",
        operator="tester",
    )
    assert event["operator"] == "tester"
    path = hitl_log_path()
    assert path.exists()
    line = path.read_text(encoding="utf-8").strip().splitlines()[-1]
    loaded = json.loads(line)
    assert loaded["action"] == "accept_field"
    assert loaded["after"] == "B"


def test_coverage_map_marks_unchecked_and_checked():
    cov = build_coverage_map(
        classified=[{"doc_type": "contract"}, {"doc_type": "invoice"}],
        evidence={"status": "PASS", "issue_description": "ok"},
        fields_confirmed=True,
    )
    by_id = {d["dimension_id"]: d for d in cov["dimensions"]}
    assert by_id["HITL_FIELD_CONFIRM"]["status"] == "CHECKED"
    assert by_id["EVIDENCE_MATCH"]["status"] == "CHECKED"
    assert by_id["AMOUNT_ACCURACY"]["status"] == "UNCHECKED"
    assert by_id["POPULATION_COMPLETENESS"]["status"] == "UNCHECKED"
    assert cov["summary"]["checked"] >= 2


def test_apply_disambiguation_exclude():
    docs = [
        {"file_name": "inv_a.pdf", "doc_type": "invoice", "fields": {"documentNo": "A"}},
        {"file_name": "inv_b.pdf", "doc_type": "invoice", "fields": {"documentNo": "B"}},
    ]
    out = apply_disambiguation_proposal(
        docs,
        {
            "file_name": "inv_b.pdf",
            "disposition": "EXCLUDE",
            "reason": "正文指向其他业务",
            "excerpt": "xxx",
        },
    )
    assert out[1]["excluded_from_match"] is True
    assert not out[0].get("excluded_from_match")

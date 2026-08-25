import json
from pathlib import Path

from src.workflow.field_resolution.comparison_plan import build_field_resolution_payload


FIXTURE = Path(__file__).parent / "fixtures" / "explainable_fields" / "3962_expected.json"


def _result() -> tuple[dict, dict]:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return fixture, build_field_resolution_payload(fixture["job"], fixture["chain_id"])


def test_3962_golden_result_is_pass_with_only_customer_mapping_warning() -> None:
    fixture, resolution = _result()
    plan = resolution["comparison_plan"]
    expected = fixture["expected"]
    assert plan["overall_status"] == expected["overall_status"]
    assert plan["three_way_status"] == expected["three_way_status"]
    assert plan["cutoff_status"] == expected["cutoff_status"]
    assert [item["issue_code"] for item in plan["domains"]["issues"]] == expected["issue_codes"]


def test_3962_amount_and_chronology_are_explained_without_false_date_equality() -> None:
    fixture, resolution = _result()
    plan = resolution["comparison_plan"]
    amount_rows = [row for row in plan["domains"]["recalculation"] if row["concept"] == "line_amount"]
    assert [row["calculation"] for row in amount_rows] == [fixture["expected"]["amount_calculation"]]
    assert [event["value"] for event in plan["domains"]["chronology"]["events"]] == fixture["expected"]["chronology_values"]
    assert all("date" not in row["concept"] for row in plan["domains"]["consistency"])


def test_every_decision_row_is_traceable_to_located_evidence() -> None:
    _, resolution = _result()
    evidence = {node["evidence_id"]: node for node in resolution["evidence_nodes"]}
    decision_rows = [
        *resolution["comparison_plan"]["domains"]["consistency"],
        *resolution["comparison_plan"]["domains"]["recalculation"],
    ]
    for row in decision_rows:
        assert row["evidence_ids"], row
        assert all(evidence[evidence_id]["usable_for_decision"] for evidence_id in row["evidence_ids"])
        assert all(evidence[evidence_id]["metadata"]["file_name"] for evidence_id in row["evidence_ids"])


def test_missing_raw_anchor_cannot_silently_support_a_pass() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    receipt = next(doc for doc in fixture["job"]["classified"] if doc["doc_type"] == "receipt")
    receipt["raw_text"] = receipt["raw_text"].replace("宁波海岳机电有限公司", "")
    resolution = build_field_resolution_payload(fixture["job"], fixture["chain_id"])
    buyer_row = next(row for row in resolution["comparison_plan"]["domains"]["consistency"] if row["concept"] == "buyer_identity")
    assert buyer_row["result"] == "PASS"
    unsupported = [
        node for node in resolution["evidence_nodes"]
        if node["document_role"] == "receipt" and node["field_key"] == "buyerName"
    ]
    assert unsupported and unsupported[0]["usable_for_decision"] is False
    assert unsupported[0]["evidence_id"] not in buyer_row["evidence_ids"]

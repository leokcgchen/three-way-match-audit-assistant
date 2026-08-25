from fastapi.testclient import TestClient

from src.api.main import app
from src.workflow.job_store import JOB_STORE


def _doc(role: str, code: str = "KH-1") -> dict:
    fields = {
        "orderNo": "SO-1",
        "sellerName": "甲方设备有限公司",
        "buyerName": "乙方制造有限公司",
        "customerCode": code,
        "goodsName": "伺服电机",
        "model": "SM-130",
        "quantity": 20,
        "totalAmount": 113000,
    }
    return {
        "file_name": f"{role}.pdf",
        "doc_type": role,
        "business_ids": ["SO-1"],
        "fields": fields,
        "raw_text": "\n".join(str(value) for value in fields.values()),
    }


def _create_job() -> str:
    job = JOB_STORE.create(title="dynamic-resolution-api")
    JOB_STORE.update(
        job["job_id"],
        goal_ids=["gospd01030"],
        active_chain_id="SO-1",
        classified=[_doc("order", "KH-A"), _doc("receipt", "KH-B"), _doc("invoice", "")],
    )
    return job["job_id"]


def test_refresh_is_idempotent_for_unchanged_documents() -> None:
    client = TestClient(app)
    job_id = _create_job()
    url = f"/api/v1/workflow/jobs/{job_id}/field-resolution/refresh"
    first = client.post(url, json={"chain_id": "SO-1"})
    second = client.post(url, json={"chain_id": "SO-1"})
    assert first.status_code == second.status_code == 200
    assert first.json()["resolution_id"] == second.json()["resolution_id"]
    assert second.json()["cache_hit"] is True


def test_unknown_edge_returns_404() -> None:
    client = TestClient(app)
    job_id = _create_job()
    client.post(f"/api/v1/workflow/jobs/{job_id}/field-resolution/refresh", json={"chain_id": "SO-1"})
    response = client.post(
        f"/api/v1/workflow/jobs/{job_id}/field-resolution/edges/not-real/decision",
        json={"chain_id": "SO-1", "decision": "CONFIRMED", "reason": "已取得客户编码映射表"},
    )
    assert response.status_code == 404


def test_human_decision_appends_audit_without_mutating_raw_evidence() -> None:
    client = TestClient(app)
    job_id = _create_job()
    refreshed = client.post(
        f"/api/v1/workflow/jobs/{job_id}/field-resolution/refresh",
        json={"chain_id": "SO-1"},
    ).json()
    edge = next(edge for edge in refreshed["edges"] if edge["concept"] == "customer_code_mapping")
    raw_before = [node["raw_value"] for node in refreshed["evidence_nodes"]]
    response = client.post(
        f"/api/v1/workflow/jobs/{job_id}/field-resolution/edges/{edge['edge_id']}/decision",
        json={"chain_id": "SO-1", "decision": "CONFIRMED", "reason": "已取得销售系统与仓储系统客户编码映射表"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    decided = next(item for item in body["edges"] if item["edge_id"] == edge["edge_id"])
    assert decided["status"] == "CONFIRMED"
    assert decided["decision_owner"] == "human"
    assert body["audit_log"][-1]["reason"].startswith("已取得")
    assert [node["raw_value"] for node in body["evidence_nodes"]] == raw_before


def test_human_decision_requires_a_reason() -> None:
    client = TestClient(app)
    job_id = _create_job()
    response = client.post(
        f"/api/v1/workflow/jobs/{job_id}/field-resolution/edges/anything/decision",
        json={"chain_id": "SO-1", "decision": "REJECTED", "reason": ""},
    )
    assert response.status_code == 422


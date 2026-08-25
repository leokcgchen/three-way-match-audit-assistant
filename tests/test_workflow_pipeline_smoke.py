"""工作台 pipeline 冒烟：plan → job → controlled fixture → confirm fields。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import app
from src.workflow.job_store import JOB_STORE
from src.workflow.recipes import resolve_workflow_plan


def test_resolve_plan_and_confirm_fields_path():
    plan = resolve_workflow_plan(["gospd01010"])
    assert "field_confirm" in plan["required_steps"]
    assert "amount_test" in plan["required_steps"]
    assert "three_way_cutoff" in plan["required_steps"]

    job = JOB_STORE.create(title="smoke")
    job_id = job["job_id"]
    job = JOB_STORE.set_goals(job_id, ["gospd01010"])
    assert "evidence_match" in job["plan"]["required_steps"]

    from src.models.field_values import seed_field_meta

    docs = [
        {"file_name": "controlled_contract.pdf", "path": "", "doc_type": "contract", "fields": {"contractNo": "HT-001", "orderNo": "SO-001", "totalAmount": 1000}},
        {"file_name": "controlled_order.pdf", "path": "", "doc_type": "order", "fields": {"documentNo": "SO-001", "orderNo": "SO-001", "totalAmount": 1000, "quantity": 1}},
        {"file_name": "controlled_invoice.pdf", "path": "", "doc_type": "invoice", "fields": {"invoiceNo": "INV-001", "orderNo": "SO-001", "totalAmount": 1000}},
        {"file_name": "controlled_delivery.pdf", "path": "", "doc_type": "delivery", "fields": {"documentNo": "DEL-001", "orderNo": "SO-001", "deliveryDate": "2025-01-02"}},
        {"file_name": "controlled_receipt.pdf", "path": "", "doc_type": "receipt", "fields": {"documentNo": "REC-001", "orderNo": "SO-001", "acceptanceDate": "2025-01-03"}},
    ]
    for doc in docs:
        doc["ocr_source"] = "controlled_test_fixture"
        seed_field_meta(doc, source="controlled_test_fixture", extractor="test")
    body = JOB_STORE.update(job_id, classified=docs)
    assert len(body.get("classified") or []) >= 1
    assert body.get("fields_confirmed") is False

    client = TestClient(app)
    assert client.post(f"/api/v1/workflow/jobs/{job_id}/seed-demo").status_code == 404
    r2 = client.post(f"/api/v1/workflow/jobs/{job_id}/hitl/fields/confirm")
    assert r2.status_code == 200, r2.text
    confirmed = r2.json()
    assert confirmed.get("fields_confirmed") is True
    assert confirmed.get("fields_confirm_sig")
    # API 确认须写入 ACCEPTED，否则底稿 rule_readable 空读
    from src.models.field_values import rule_readable_fields

    for doc in confirmed.get("classified") or []:
        readable = rule_readable_fields(doc)
        assert readable, f"confirmed doc has no ACCEPTED fields: {doc.get('file_name')}"


def test_ocr_status_endpoint():
    client = TestClient(app)
    r = client.get("/api/v1/workflow/ocr-status")
    assert r.status_code == 200
    data = r.json()
    assert "configured" in data
    assert "message" in data

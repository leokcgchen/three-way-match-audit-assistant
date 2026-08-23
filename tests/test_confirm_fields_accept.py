"""API 字段确认必须 accept_all，供底稿只读已确认事实。"""

from __future__ import annotations

from src.models.field_values import get_verified_value, rule_readable_fields, seed_field_meta
from src.workflow.job_store import JOB_STORE


def test_confirm_fields_accepts_seeded_meta_for_generic_job():
    job = JOB_STORE.create(title="confirm-accept")
    jid = job["job_id"]
    doc = {
        "file_name": "r.pdf",
        "doc_type": "receipt",
        "fields": {"acceptanceDate": "2025-01-08", "totalAmount": 1000},
    }
    seed_field_meta(doc, source="ocr")
    assert get_verified_value(doc, "acceptanceDate") is None
    JOB_STORE.set_classified(jid, [doc])
    out = JOB_STORE.confirm_fields(jid)
    d0 = (out.get("classified") or [])[0]
    assert get_verified_value(d0, "acceptanceDate") == "2025-01-08"
    assert rule_readable_fields(d0)["totalAmount"] == 1000

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import app
from src.workflow.job_store import JOB_STORE
from src.workflow.recognition_versions import (
    reprocess_classified_documents,
    snapshot_recognition,
)


INVOICE_TEXT = """
增值税专用发票
发票号码：FP-260102-8305
开票日期：2026年01月02日
对应销售订单：SO-251209-7214
价税合计（小写） ¥113,000.00
"""


def _job(*, human: bool = False) -> dict:
    source = "manual" if human else "auto_fields_ok"
    return {
        "job_id": "job-reprocess",
        "classified": [
            {
                "file_name": "invoice.pdf",
                "file_fingerprint": "fp-1",
                "doc_type": "invoice",
                "raw_text": INVOICE_TEXT,
                "fields": {"documentNo": "HUMAN-VALUE" if human else "SO-251209-7214"},
                "_field_meta": {
                    "documentNo": {
                        "raw_value": "HUMAN-VALUE" if human else "SO-251209-7214",
                        "normalized_candidate": "HUMAN-VALUE" if human else "SO-251209-7214",
                        "accepted_value": "HUMAN-VALUE" if human else "SO-251209-7214",
                        "status": "ACCEPTED",
                        "source": source,
                        "extractor": "hitl" if human else "confirm_all",
                    }
                },
            }
        ],
        "fields_confirmed": True,
        "matching_confirmed": True,
        "gospd_sample_results": {"SO-251209-7214": {"fields_confirmed": True}},
    }


def test_snapshot_contains_pre_run_fields_and_hash() -> None:
    snapshot = snapshot_recognition(_job())

    assert snapshot["job_id"] == "job-reprocess"
    assert snapshot["documents"][0]["fields"]["documentNo"] == "SO-251209-7214"
    assert snapshot["snapshot_hash"]


def test_reprocess_supersedes_auto_values_but_preserves_history() -> None:
    updated = reprocess_classified_documents(_job())
    doc = updated["classified"][0]

    assert doc["fields"]["documentNo"] == "FP-260102-8305"
    assert doc["fields"]["invoiceNo"] == "FP-260102-8305"
    assert doc["fields"]["orderNo"] == "SO-251209-7214"
    assert doc["recognition_history"][-1]["fields"]["documentNo"] == "SO-251209-7214"
    assert updated["fields_confirmed"] is False
    assert updated["matching_confirmed"] is False


def test_reprocess_does_not_overwrite_human_accepted_value() -> None:
    updated = reprocess_classified_documents(_job(human=True))
    doc = updated["classified"][0]

    assert doc["fields"]["documentNo"] == "HUMAN-VALUE"
    assert doc["reprocess_conflicts"][0]["field_key"] == "documentNo"
    assert doc["reprocess_conflicts"][0]["new_candidate"] == "FP-260102-8305"


def test_reprocess_is_local_only_by_default(monkeypatch) -> None:
    from src.legacy_ocr.ocr_adapter import LegacyOcrAdapter

    def forbidden(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("network-backed LLM must not be called")

    monkeypatch.setattr(LegacyOcrAdapter, "_llm_chat_json", forbidden)

    updated = reprocess_classified_documents(_job())

    assert updated["classified"][0]["fields"]["invoiceNo"] == "FP-260102-8305"
    assert updated["recognition_reprocess"]["allow_llm_field_supplement"] is False


def test_reprocess_route_continues_into_automatic_review(monkeypatch) -> None:
    job = JOB_STORE.create(title="reprocess-auto-review")
    job_id = job["job_id"]
    JOB_STORE.update(
        job_id,
        goal_ids=["gospd01030"],
        plan={"goal_ids": ["gospd01030"], "required_steps": ["three_way_cutoff"]},
        classified=[{"file_name": "invoice.pdf", "doc_type": "invoice", "fields": {}}],
    )

    def fake_reprocess(current, **_kwargs):
        return {
            **current,
            "recognition_reprocess": {"status": "COMPLETED"},
        }

    def fake_finish(current_job_id):
        return JOB_STORE.update(
            current_job_id,
            auto_review_last_run={"status": "COMPLETED", "summary": "自动审阅已执行"},
        )

    monkeypatch.setattr(
        "src.workflow.recognition_versions.reprocess_classified_documents",
        fake_reprocess,
    )
    monkeypatch.setattr("src.workflow.sample_desk.finish_after_classify", fake_finish)

    response = TestClient(app).post(
        f"/api/v1/workflow/jobs/{job_id}/recognition/reprocess",
        json={"allow_llm_field_supplement": False},
    )

    assert response.status_code == 200, response.text
    assert response.json()["auto_review_last_run"]["status"] == "COMPLETED"
    assert response.json()["auto_review_processing"] is False

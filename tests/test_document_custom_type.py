from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import app
from src.workflow.job_store import JOB_STORE


def _seed_job() -> dict:
    job = JOB_STORE.create(title="当前文件自定义类型")
    return JOB_STORE.update(
        job["job_id"],
        classified=[
            {
                "file_name": "bill-of-lading.pdf",
                "path": "bill-of-lading.pdf",
                "doc_type": "other",
                "raw_text": "BILL OF LADING",
                "fields": {"documentNo": "BL-001", "documentDate": "2026-01-02"},
            },
            {
                "file_name": "other-document.pdf",
                "path": "other-document.pdf",
                "doc_type": "other",
                "raw_text": "OTHER DOCUMENT",
                "fields": {"documentNo": "OTHER-001"},
            },
        ],
    )


def test_patch_other_document_saves_name_for_current_file_without_reocr() -> None:
    job = _seed_job()
    before = job["classified"][0]

    response = TestClient(app).patch(
        f"/api/v1/workflow/jobs/{job['job_id']}/documents/fields",
        json={
            "file_name": before["file_name"],
            "fields": before["fields"],
            "doc_type": "other",
            "custom_doc_type_name": "  海运提单  ",
            "doc_type_confirmed": True,
        },
    )

    assert response.status_code == 200, response.text
    classified = response.json()["classified"]
    saved = next(row for row in classified if row["file_name"] == before["file_name"])
    untouched = next(row for row in classified if row["file_name"] == "other-document.pdf")
    assert saved["doc_type"] == "other"
    assert saved["custom_doc_type_name"] == "海运提单"
    assert saved["doc_type_confirmed"] is True
    assert saved["type_uncertain"] is False
    assert saved["raw_text"] == before["raw_text"]
    assert saved["fields"] == before["fields"]
    assert untouched.get("custom_doc_type_name") is None


def test_confirming_other_requires_a_current_file_name() -> None:
    job = _seed_job()

    response = TestClient(app).patch(
        f"/api/v1/workflow/jobs/{job['job_id']}/documents/fields",
        json={
            "file_name": "bill-of-lading.pdf",
            "fields": {"documentNo": "BL-001"},
            "doc_type": "other",
            "custom_doc_type_name": "   ",
            "doc_type_confirmed": True,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "请填写当前文件的具体单据名称"


def test_custom_document_name_rejects_unreasonably_long_text() -> None:
    job = _seed_job()

    response = TestClient(app).patch(
        f"/api/v1/workflow/jobs/{job['job_id']}/documents/fields",
        json={
            "file_name": "bill-of-lading.pdf",
            "fields": {"documentNo": "BL-001"},
            "doc_type": "other",
            "custom_doc_type_name": "海" * 81,
            "doc_type_confirmed": True,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "当前文件具体名称不能超过 80 个字符"


def test_switching_to_fixed_type_clears_stale_custom_name() -> None:
    job = _seed_job()
    JOB_STORE.patch_document_fields(
        job["job_id"],
        file_name="bill-of-lading.pdf",
        fields={"documentNo": "BL-001"},
        doc_type="other",
        custom_doc_type_name="海运提单",
        doc_type_confirmed=True,
    )

    response = TestClient(app).patch(
        f"/api/v1/workflow/jobs/{job['job_id']}/documents/fields",
        json={
            "file_name": "bill-of-lading.pdf",
            "fields": {"documentNo": "BL-001"},
            "doc_type": "delivery",
            "custom_doc_type_name": "海运提单",
            "doc_type_confirmed": True,
        },
    )

    assert response.status_code == 200, response.text
    saved = response.json()["classified"][0]
    assert saved["doc_type"] == "delivery"
    assert saved.get("custom_doc_type_name") is None
    assert saved["doc_type_confirmed"] is True
    assert saved["type_uncertain"] is False

from __future__ import annotations

import io

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from src.api.main import app
from src.workflow.sample_scope import partition_documents_by_sample_scope
from src.workflow.job_store import JOB_STORE
from src.workflow.pipeline import job_workdir


def _pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def test_outside_sample_document_is_quarantined() -> None:
    accepted, exceptions = partition_documents_by_sample_scope(
        [
            {
                "file_name": "SO25-9999_合同.pdf",
                "doc_type": "contract",
                "fields": {"orderNo": "SO25-9999"},
                "path": "D:/job/SO25-9999_合同.pdf",
            }
        ],
        {"business_ids": ["SO25-0001"]},
    )

    assert accepted == []
    assert len(exceptions) == 1
    assert exceptions[0]["detected_business_ids"] == ["SO25-9999"]
    assert exceptions[0]["scope_status"] == "OUT_OF_SAMPLE"
    assert exceptions[0]["recommended_action"] == "delete"
    assert exceptions[0]["document"]["file_name"] == "SO25-9999_合同.pdf"


def test_matching_sample_document_remains_classified() -> None:
    document = {
        "file_name": "SO25-0001_发票.pdf",
        "doc_type": "invoice",
        "fields": {"orderNo": "SO25-0001", "invoiceNo": "12345678"},
    }

    accepted, exceptions = partition_documents_by_sample_scope(
        [document],
        {"business_ids": ["SO25-0001"]},
    )

    assert accepted == [document]
    assert exceptions == []


def test_two_partial_receipts_with_same_body_order_stay_in_one_business() -> None:
    documents = [
        {
            "file_name": "签收单甲.pdf",
            "doc_type": "receipt",
            "raw_text": "Order SO-251209-7214; Amount 40,000",
            "fields": {"totalAmount": 40000},
        },
        {
            "file_name": "签收单乙.pdf",
            "doc_type": "receipt",
            "raw_text": "Order SO-251209-7214; Amount 73,000",
            "fields": {"totalAmount": 73000},
        },
    ]
    population = {
        "business_ids": ["YW-2025-3962"],
        "rows": [
            {
                "business_id": "YW-2025-3962",
                "order_numbers": ["SO-251209-7214"],
                "book_amount": 113000,
            }
        ],
    }

    accepted, exceptions = partition_documents_by_sample_scope(documents, population)

    assert exceptions == []
    assert [item["sample_business_id"] for item in accepted] == [
        "YW-2025-3962",
        "YW-2025-3962",
    ]


def test_unmatched_bill_of_lading_inherits_unique_business_through_shared_reference() -> None:
    documents = [
        {
            "file_name": "商业发票.pdf",
            "doc_type": "invoice",
            "raw_text": "Order SO-251229-7498; B/L No. BL-SHAHAM-260104-638",
            "fields": {},
        },
        {
            "file_name": "海运提单.pdf",
            "doc_type": "other",
            "raw_text": "BILL OF LADING No. BL-SHAHAM-260104-638",
            "fields": {},
        },
    ]
    population = {
        "business_ids": ["YW-2025-3995"],
        "rows": [
            {
                "business_id": "YW-2025-3995",
                "order_numbers": ["SO-251229-7498"],
            }
        ],
    }

    accepted, exceptions = partition_documents_by_sample_scope(documents, population)

    assert exceptions == []
    assert len(accepted) == 2
    inherited = next(item for item in accepted if item["file_name"] == "海运提单.pdf")
    assert inherited["sample_business_id"] == "YW-2025-3995"
    assert inherited["business_index_source"] == "document_reference"
    assert inherited["business_index_evidence"][0]["reference"] == "BL-SHAHAM-260104-638"


def test_shared_reference_across_two_businesses_never_inherits() -> None:
    documents = [
        {
            "file_name": "发票甲.pdf",
            "raw_text": "SO-251209-7214; B/L No. BL-SHARED-001",
            "fields": {},
        },
        {
            "file_name": "发票乙.pdf",
            "raw_text": "SO-251212-7259; B/L No. BL-SHARED-001",
            "fields": {},
        },
        {
            "file_name": "提单.pdf",
            "raw_text": "B/L No. BL-SHARED-001",
            "fields": {},
        },
    ]

    population = {
        "business_ids": ["YW-2025-3962", "YW-2025-3971"],
        "rows": [
            {"business_id": "YW-2025-3962", "order_numbers": ["SO-251209-7214"]},
            {"business_id": "YW-2025-3971", "order_numbers": ["SO-251212-7259"]},
        ],
    }
    accepted, exceptions = partition_documents_by_sample_scope(documents, population)

    assert len(accepted) == 2
    exception = next(item for item in exceptions if item["file_name"] == "提单.pdf")
    assert exception["scope_status"] == "INDEX_CONFLICT"
    assert exception["candidate_business_ids"] == ["YW-2025-3962", "YW-2025-3971"]


def test_shared_material_model_is_not_treated_as_document_reference() -> None:
    documents = [
        {
            "file_name": "发票.pdf",
            "raw_text": "SO-251209-7214; Material NW-500",
            "fields": {},
        },
        {
            "file_name": "无编号附件.pdf",
            "raw_text": "Material NW-500",
            "fields": {},
        },
    ]
    population = {
        "business_ids": ["YW-2025-3962"],
        "rows": [
            {"business_id": "YW-2025-3962", "order_numbers": ["SO-251209-7214"]}
        ],
    }

    accepted, exceptions = partition_documents_by_sample_scope(documents, population)

    assert len(accepted) == 1
    assert exceptions[0]["file_name"] == "无编号附件.pdf"
    assert exceptions[0]["scope_status"] == "UNASSIGNED"


def test_related_contract_number_does_not_make_matching_order_out_of_scope() -> None:
    document = {
        "file_name": "SO25-0281_HT25-0281_合同.pdf",
        "doc_type": "contract",
        "fields": {"orderNo": "SO25-0281", "contractNo": "HT25-0281"},
    }

    accepted, exceptions = partition_documents_by_sample_scope(
        [document],
        {"business_ids": ["SO25-0281"]},
    )

    assert accepted == [document]
    assert exceptions == []


def test_declared_sample_order_accepts_related_contract_only_document() -> None:
    document = {
        "file_name": "合同扫描件.pdf",
        "doc_type": "contract",
        "fields": {"contractNo": "HT25-0281"},
        "declared_business_ids": ["SO25-0281"],
    }

    accepted, exceptions = partition_documents_by_sample_scope(
        [document],
        {"business_ids": ["SO25-0281"]},
    )

    assert accepted == [document]
    assert exceptions == []


def test_second_order_number_in_same_file_is_quarantined_as_mixed_scope() -> None:
    accepted, exceptions = partition_documents_by_sample_scope(
        [
            {
                "file_name": "SO25-0281_SO25-9999_混合合同.pdf",
                "doc_type": "contract",
                "fields": {"orderNo": "SO25-0281"},
            }
        ],
        {"business_ids": ["SO25-0281"]},
    )

    assert accepted == []
    assert exceptions[0]["scope_status"] == "MIXED_SCOPE"
    assert exceptions[0]["detected_business_ids"] == ["SO25-0281", "SO25-9999"]


def test_document_without_business_id_waits_in_exception_area() -> None:
    accepted, exceptions = partition_documents_by_sample_scope(
        [{"file_name": "扫描件.pdf", "doc_type": "invoice", "fields": {"invoiceNo": "12345678"}}],
        {"business_ids": ["SO25-0001"]},
    )

    assert accepted == []
    assert exceptions[0]["detected_business_ids"] == []
    assert exceptions[0]["scope_status"] == "UNASSIGNED"
    assert "无法确认" in exceptions[0]["reason"]


def test_body_identifier_outside_sample_is_quarantined() -> None:
    accepted, exceptions = partition_documents_by_sample_scope(
        [
            {
                "file_name": "扫描件.pdf",
                "doc_type": "invoice",
                "raw_text": "Sales Order SO25-9999",
                "fields": {},
            }
        ],
        {"business_ids": ["SO25-0001"]},
    )

    assert accepted == []
    assert exceptions[0]["scope_status"] == "OUT_OF_SAMPLE"
    assert exceptions[0]["detected_business_ids"] == ["SO25-9999"]


def test_valid_declared_sample_business_keeps_unidentified_document_in_scope() -> None:
    document = {
        "file_name": "扫描件.pdf",
        "doc_type": "receipt",
        "fields": {},
        "declared_business_ids": ["SO25-0001"],
    }

    accepted, exceptions = partition_documents_by_sample_scope(
        [document],
        {"business_ids": ["SO25-0001"]},
    )

    assert accepted == [document]
    assert exceptions == []


def test_no_population_keeps_legacy_documents_untouched() -> None:
    documents = [{"file_name": "SO25-9999.pdf", "fields": {"orderNo": "SO25-9999"}}]

    accepted, exceptions = partition_documents_by_sample_scope(documents, None)

    assert accepted == documents
    assert exceptions == []


def test_delete_scope_exception_removes_job_references_and_local_file() -> None:
    job = JOB_STORE.create(title="scope-delete")
    job_id = job["job_id"]
    path = job_workdir(job_id) / "SO25-9999.pdf"
    path.write_bytes(b"outside sample")
    exception = {
        "exception_id": "scope-delete-me",
        "file_name": path.name,
        "scope_status": "OUT_OF_SAMPLE",
        "detected_business_ids": ["SO25-9999"],
        "recommended_action": "delete",
        "document": {"file_name": path.name, "path": str(path)},
    }
    JOB_STORE.update(
        job_id,
        scope_exceptions=[exception],
        classified=[{"file_name": path.name, "path": str(path)}],
        pending_files=[{"file_name": path.name, "path": str(path)}],
        packet_units=[{"unit_id": "u1", "source_file": path.name}],
    )

    response = TestClient(app).delete(
        f"/api/v1/workflow/jobs/{job_id}/scope-exceptions/scope-delete-me"
    )

    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["scope_exceptions"] == []
    assert updated["classified"] == []
    assert updated["pending_files"] == []
    assert updated["packet_units"] == []
    assert not path.exists()


def test_delete_scope_exception_validates_every_path_before_unlinking() -> None:
    job = JOB_STORE.create(title="scope-delete-guard")
    job_id = job["job_id"]
    valid_path = job_workdir(job_id) / "SO25-9998.pdf"
    valid_path.write_bytes(b"keep when any path is unsafe")
    exception = {
        "exception_id": "scope-unsafe-path",
        "file_name": valid_path.name,
        "scope_status": "OUT_OF_SAMPLE",
        "detected_business_ids": ["SO25-9998"],
        "recommended_action": "delete",
        "document": {
            "file_name": valid_path.name,
            "path": str(job_workdir(job_id).parent / "outside-job.pdf"),
        },
    }
    JOB_STORE.update(
        job_id,
        scope_exceptions=[exception],
        pending_files=[{"file_name": valid_path.name, "path": str(valid_path)}],
    )

    response = TestClient(app).delete(
        f"/api/v1/workflow/jobs/{job_id}/scope-exceptions/scope-unsafe-path"
    )

    assert response.status_code == 409
    assert valid_path.exists()
    assert len((JOB_STORE.get(job_id) or {}).get("scope_exceptions") or []) == 1


def test_upload_process_quarantines_outside_file_instead_of_creating_business() -> None:
    job = JOB_STORE.create(title="scope-upload")
    job_id = job["job_id"]
    JOB_STORE.update(
        job_id,
        sample_population={"business_ids": ["SO25-0001"], "count": 1, "source": "test"},
    )

    response = TestClient(app).post(
        f"/api/v1/workflow/jobs/{job_id}/upload",
        files=[("files", ("SO25-9999_合同.pdf", _pdf_bytes(), "application/pdf"))],
        data={"process": "true"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["classified"] == []
    assert body["scope_exceptions"][0]["detected_business_ids"] == ["SO25-9999"]
    assert body["scope_exceptions"][0]["recommended_action"] == "delete"


def test_get_job_migrates_legacy_outside_business_into_exception_area() -> None:
    job = JOB_STORE.create(title="scope-legacy-migration")
    job_id = job["job_id"]
    JOB_STORE.update(
        job_id,
        sample_population={"business_ids": ["SO25-0001"], "count": 1, "source": "legacy"},
        classified=[
            {
                "file_name": "SO25-9999_旧文件.pdf",
                "doc_type": "contract",
                "fields": {"orderNo": "SO25-9999", "contractNo": "HT25-9999"},
            }
        ],
    )

    response = TestClient(app).get(f"/api/v1/workflow/jobs/{job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["classified"] == []
    assert body["scope_exceptions"][0]["detected_business_ids"] == [
        "SO25-9999",
        "HT25-9999",
    ]


def test_yw_filename_is_the_canonical_sample_business_even_when_order_number_differs() -> None:
    document = {
        "file_name": "YW-2025-3962_发票_FP-260102-8305.pdf",
        "doc_type": "invoice",
        "fields": {"orderNo": "SO-251209-7214", "invoiceNo": "FP-260102-8305"},
    }

    accepted, exceptions = partition_documents_by_sample_scope(
        [document],
        {"business_ids": ["YW-2025-3962"]},
    )

    assert exceptions == []
    assert accepted[0]["sample_business_id"] == "YW-2025-3962"
    assert accepted[0]["business_index_source"] == "filename"
    assert accepted[0]["business_index_candidates"] == [
        {"value": "YW-2025-3962", "source": "filename"}
    ]


def test_yw_business_outside_sample_is_quarantined() -> None:
    accepted, exceptions = partition_documents_by_sample_scope(
        [
            {
                "file_name": "YW-2025-9999_发票.pdf",
                "doc_type": "invoice",
                "fields": {"orderNo": "SO-251209-7214"},
            }
        ],
        {"business_ids": ["YW-2025-3962"]},
    )

    assert accepted == []
    assert exceptions[0]["scope_status"] == "OUT_OF_SAMPLE"
    assert exceptions[0]["detected_business_ids"] == ["YW-2025-9999"]


def test_secondary_order_number_does_not_pose_as_sample_business_id() -> None:
    accepted, exceptions = partition_documents_by_sample_scope(
        [
            {
                "file_name": "发票扫描件.pdf",
                "doc_type": "invoice",
                "fields": {"orderNo": "SO-251209-7214"},
            }
        ],
        {"business_ids": ["YW-2025-3962"]},
    )

    assert accepted == []
    assert exceptions[0]["scope_status"] == "UNASSIGNED"
    assert exceptions[0]["detected_business_ids"] == []


def test_two_sample_business_ids_in_one_filename_are_ambiguous() -> None:
    accepted, exceptions = partition_documents_by_sample_scope(
        [
            {
                "file_name": "YW-2025-3962_YW-2025-3971_混合.pdf",
                "doc_type": "invoice",
                "fields": {},
            }
        ],
        {"business_ids": ["YW-2025-3962", "YW-2025-3971"]},
    )

    assert accepted == []
    assert exceptions[0]["scope_status"] == "INDEX_CONFLICT"
    assert exceptions[0]["detected_business_ids"] == [
        "YW-2025-3962",
        "YW-2025-3971",
    ]


def test_unique_order_alias_accepts_document_without_business_id() -> None:
    population = {
        "business_ids": ["YW-2025-3962"],
        "rows": [
            {
                "business_id": "YW-2025-3962",
                "order_numbers": ["SO-251209-7214"],
            }
        ],
    }

    accepted, exceptions = partition_documents_by_sample_scope(
        [{"file_name": "SO-251209-7214_签收单.pdf", "doc_type": "receipt", "fields": {}}],
        population,
    )

    assert exceptions == []
    assert accepted[0]["sample_business_id"] == "YW-2025-3962"
    assert accepted[0]["business_index_status"] == "MATCHED"
    assert accepted[0]["business_index_evidence"][0]["type"] == "order_number"


def test_known_order_alias_with_same_prefix_as_business_id_is_not_mixed_scope() -> None:
    population = {
        "business_ids": ["SO-CASE-0001"],
        "rows": [
            {
                "business_id": "SO-CASE-0001",
                "order_numbers": ["SO-ORDER-7214"],
            }
        ],
    }

    accepted, exceptions = partition_documents_by_sample_scope(
        [
            {
                "file_name": "SO-CASE-0001_SO-ORDER-7214.pdf",
                "doc_type": "invoice",
                "fields": {},
            }
        ],
        population,
    )

    assert exceptions == []
    assert accepted[0]["sample_business_id"] == "SO-CASE-0001"


def test_business_and_order_alias_conflict_is_quarantined() -> None:
    population = {
        "business_ids": ["YW-2025-3962", "YW-2025-3971"],
        "rows": [
            {
                "business_id": "YW-2025-3962",
                "order_numbers": ["SO-251209-7214"],
            },
            {
                "business_id": "YW-2025-3971",
                "order_numbers": ["SO-251212-7259"],
            },
        ],
    }

    accepted, exceptions = partition_documents_by_sample_scope(
        [
            {
                "file_name": "YW-2025-3962_SO-251212-7259.pdf",
                "doc_type": "invoice",
                "fields": {},
            }
        ],
        population,
    )

    assert accepted == []
    assert exceptions[0]["scope_status"] == "INDEX_CONFLICT"
    assert exceptions[0]["candidate_business_ids"] == [
        "YW-2025-3962",
        "YW-2025-3971",
    ]


def test_similar_business_digits_wait_for_human_review() -> None:
    population = {
        "business_ids": ["YW-2025-3962"],
        "rows": [{"business_id": "YW-2025-3962", "order_numbers": []}],
    }

    accepted, exceptions = partition_documents_by_sample_scope(
        [{"file_name": "YW-2025-3992_扫描件.pdf", "doc_type": "invoice", "fields": {}}],
        population,
    )

    assert accepted == []
    assert exceptions[0]["scope_status"] == "SIMILAR_CANDIDATE"
    assert exceptions[0]["candidate_business_ids"] == ["YW-2025-3962"]
    assert exceptions[0]["recommended_action"] == "review"

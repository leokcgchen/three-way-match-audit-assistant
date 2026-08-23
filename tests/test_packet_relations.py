from __future__ import annotations

import pytest

from src.workflow.packet_relations import (
    normalize_business_ids,
    validate_confirmable_units,
    with_business_ids,
)


def test_normalize_business_ids_reads_legacy_chain_id() -> None:
    assert normalize_business_ids({"chain_id": "SO25-0281"}) == ["SO25-0281"]


def test_normalize_business_ids_deduplicates_and_preserves_order() -> None:
    unit = {"business_ids": ["SO25-0281", "SO25-0282", "SO25-0281"]}

    assert normalize_business_ids(unit) == ["SO25-0281", "SO25-0282"]


def test_explicit_empty_business_ids_do_not_fall_back_to_legacy_chain() -> None:
    unit = {"business_ids": [], "chain_id": "SO25-0281"}

    assert normalize_business_ids(unit) == []


def test_with_business_ids_keeps_first_business_as_legacy_mirror() -> None:
    unit = with_business_ids(
        {"unit_id": "u1", "chain_id": "OLD"},
        ["SO25-0282", "SO25-0281", "SO25-0282"],
    )

    assert unit["business_ids"] == ["SO25-0282", "SO25-0281"]
    assert unit["chain_id"] == "SO25-0282"


def test_validate_units_allows_one_physical_unit_linked_to_two_businesses() -> None:
    units = [
        {
            "unit_id": "u1",
            "source_file": "packet.pdf",
            "pages": [1, 2],
            "doc_type": "receipt",
            "business_ids": ["SO25-0281", "SO25-0282"],
            "boundary_confirmed": True,
            "dropped": False,
        }
    ]

    validate_confirmable_units(
        units,
        multi_page_files={"packet.pdf"},
        start_ocr=True,
    )


def test_validate_units_rejects_unassigned_document() -> None:
    units = [
        {
            "unit_id": "u-unassigned",
            "source_file": "packet.pdf",
            "pages": [1],
            "doc_type": "invoice",
            "business_ids": [],
            "boundary_confirmed": True,
        }
    ]

    with pytest.raises(ValueError, match="u-unassigned.*业务归属"):
        validate_confirmable_units(
            units,
            multi_page_files={"packet.pdf"},
            start_ocr=False,
        )


def test_validate_units_rejects_unconfirmed_multi_page_boundary() -> None:
    units = [
        {
            "unit_id": "u-boundary",
            "source_file": "packet.pdf",
            "pages": [1, 2],
            "doc_type": "contract",
            "business_ids": ["SO25-0281"],
            "boundary_confirmed": False,
        }
    ]

    with pytest.raises(ValueError, match="u-boundary.*拆包边界"):
        validate_confirmable_units(
            units,
            multi_page_files={"packet.pdf"},
            start_ocr=False,
        )


def test_validate_units_rejects_unresolved_type_when_starting_ocr() -> None:
    units = [
        {
            "unit_id": "u-type",
            "source_file": "packet.pdf",
            "pages": [1, 2],
            "doc_type": "unresolved",
            "business_ids": ["SO25-0281"],
            "boundary_confirmed": True,
        }
    ]

    with pytest.raises(ValueError, match="u-type.*单据类型"):
        validate_confirmable_units(
            units,
            multi_page_files={"packet.pdf"},
            start_ocr=True,
        )


def test_validate_units_allows_unresolved_type_when_saving_without_ocr() -> None:
    units = [
        {
            "unit_id": "u-draft",
            "source_file": "packet.pdf",
            "pages": [1, 2],
            "doc_type": "unresolved",
            "business_ids": ["SO25-0281"],
            "boundary_confirmed": True,
        }
    ]

    validate_confirmable_units(
        units,
        multi_page_files={"packet.pdf"},
        start_ocr=False,
    )


def test_validate_units_ignores_dropped_pages() -> None:
    units = [
        {
            "unit_id": "u-drop",
            "source_file": "packet.pdf",
            "pages": [2],
            "doc_type": "unresolved",
            "business_ids": [],
            "boundary_confirmed": False,
            "dropped": True,
        }
    ]

    validate_confirmable_units(
        units,
        multi_page_files={"packet.pdf"},
        start_ocr=True,
    )


def test_apply_unit_edits_persists_multi_business_and_confirmation_metadata() -> None:
    from src.workflow.packet_engine import apply_unit_edits

    existing = [
        {
            "unit_id": "u1",
            "source_file": "packet.pdf",
            "source_path": "packet.pdf",
            "pages": [1, 2],
            "doc_type": "receipt",
            "chain_id": "SO25-0281",
            "needs_review": True,
        }
    ]
    edits = [
        {
            "unit_id": "u1",
            "business_ids": ["SO25-0281", "SO25-0282"],
            "boundary_confirmed": True,
            "business_binding_source": "human",
            "suggested_doc_type": "delivery",
            "doc_type": "receipt",
            "doc_type_source": "human",
            "drop_reason": "",
        }
    ]

    [unit] = apply_unit_edits(existing, edits)

    assert unit["business_ids"] == ["SO25-0281", "SO25-0282"]
    assert unit["chain_id"] == "SO25-0281"
    assert unit["boundary_confirmed"] is True
    assert unit["business_binding_source"] == "human"
    assert unit["suggested_doc_type"] == "delivery"
    assert unit["doc_type_source"] == "human"


def test_apply_unit_edits_treats_legacy_submission_as_human_confirmation() -> None:
    from src.workflow.packet_engine import apply_unit_edits

    existing = [
        {
            "unit_id": "u-legacy",
            "source_file": "packet.pdf",
            "source_path": "packet.pdf",
            "pages": [1, 2],
            "doc_type": "contract",
            "chain_id": "SO25-0281",
        }
    ]

    [unit] = apply_unit_edits(
        existing,
        [{"unit_id": "u-legacy", "chain_id": "SO25-0281"}],
    )

    assert unit["business_ids"] == ["SO25-0281"]
    assert unit["boundary_confirmed"] is True


def test_materialize_multi_business_unit_once_with_relationship_metadata(
    tmp_path,
    monkeypatch,
) -> None:
    from src.workflow.packet_engine import materialize_units

    source = tmp_path / "packet.pdf"
    source.write_bytes(b"source")

    def fake_extract(_src, pages, dest) -> None:
        assert pages == [1, 2]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"derived")

    monkeypatch.setattr("src.workflow.packet_engine.job_workdir", lambda _job_id: tmp_path)
    monkeypatch.setattr("src.workflow.packet_engine.extract_pdf_page_range", fake_extract)
    units = [
        {
            "unit_id": "u1",
            "source_file": source.name,
            "source_path": str(source),
            "pages": [1, 2],
            "doc_type": "receipt",
            "business_ids": ["SO25-0281", "SO25-0282"],
            "chain_id": "SO25-0281",
            "boundary_confirmed": True,
            "business_binding_source": "human",
            "suggested_doc_type": "delivery",
            "doc_type_source": "human",
        }
    ]

    specs = materialize_units("job-1", units, run_id="run-1")

    assert len(specs) == 1
    source_packet = specs[0]["source_packet"]
    assert source_packet["business_ids"] == ["SO25-0281", "SO25-0282"]
    assert source_packet["chain_id"] == "SO25-0281"
    assert source_packet["boundary_confirmed"] is True
    assert source_packet["doc_type_source"] == "human"


def test_confirm_packet_rejects_explicit_unconfirmed_boundary(monkeypatch) -> None:
    from src.workflow.packet_engine import confirm_packet

    monkeypatch.setattr("src.workflow.packet_engine.materialize_units", lambda *_a, **_kw: [])
    job = {
        "job_id": "job-1",
        "packet_run": {
            "run_id": "run-1",
            "files": [
                {
                    "file_name": "packet.pdf",
                    "kind": "packet_single_chain",
                    "page_count": 2,
                }
            ],
        },
        "packet_units": [],
        "pending_files": [],
    }
    edits = [
        {
            "unit_id": "u1",
            "source_file": "packet.pdf",
            "source_path": "packet.pdf",
            "pages": [1, 2],
            "doc_type": "contract",
            "business_ids": ["SO25-0281"],
            "boundary_confirmed": False,
        }
    ]

    with pytest.raises(ValueError, match="拆包边界"):
        confirm_packet(job, units=edits, start_ocr=False)

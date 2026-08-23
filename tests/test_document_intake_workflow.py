from __future__ import annotations

import io
import json
from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from src.api.main import app
from src.workflow.job_store import JOB_STORE
from src.workflow.packet_cards import load_category_cards
from src.workflow.packet_split import _page_from_text


def _pdf_bytes(page_count: int) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=595, height=842)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def _create_job(*business_ids: str) -> dict:
    job = JOB_STORE.create(title="document-intake-e2e")
    return JOB_STORE.update(
        job["job_id"],
        sample_population={"business_ids": list(business_ids), "count": len(business_ids)},
    )


def _upload_and_analyze(client: TestClient, job: dict, monkeypatch) -> dict:
    cards = load_category_cards()
    texts = [
        "销售合同\n合同编号 HT25-0281\n订单号 SO25-0281\n合同金额 100",
        "合同条款续页\n交付与验收安排",
        "产品验收单\n订单号 SO25-0281\n客户签字",
    ]

    def fake_load(file_name, path, **_kwargs):
        return [
            _page_from_text(file_name, path, index, text, "pdf_text", cards)
            for index, text in enumerate(texts, start=1)
        ]

    monkeypatch.setattr("src.workflow.packet_engine.load_file_pages", fake_load)
    uploaded = client.post(
        f"/api/v1/workflow/jobs/{job['job_id']}/upload",
        files=[("files", ("客户混装包.pdf", _pdf_bytes(3), "application/pdf"))],
        data={
            "process": "false",
            "business_hints": json.dumps({"客户混装包.pdf": ["SO25-0281"]}),
        },
    )
    assert uploaded.status_code == 200, uploaded.text
    analyzed = client.post(f"/api/v1/workflow/jobs/{job['job_id']}/packet/analyze")
    assert analyzed.status_code == 200, analyzed.text
    return analyzed.json()


def _merged_human_unit(analyzed: dict, *, doc_type: str = "receipt") -> dict:
    first = analyzed["packet_units"][0]
    return {
        **first,
        "unit_id": "human_merged_1",
        "pages": [1, 2, 3],
        "page_start": 1,
        "page_end": 3,
        "doc_type": doc_type,
        "card_type": doc_type,
        "suggested_doc_type": "contract",
        "doc_type_source": "human",
        "chain_id": "SO25-0281",
        "business_ids": ["SO25-0281", "SO25-0282"],
        "business_binding_source": "human",
        "boundary_confirmed": True,
        "needs_review": False,
    }


def test_human_merge_multi_business_override_starts_ocr_and_audits(monkeypatch) -> None:
    job = _create_job("SO25-0281", "SO25-0282")
    client = TestClient(app)
    analyzed = _upload_and_analyze(client, job, monkeypatch)
    merged = _merged_human_unit(analyzed)

    blocked = client.post(
        f"/api/v1/workflow/jobs/{job['job_id']}/packet/confirm",
        json={"units": [{**merged, "boundary_confirmed": False}], "start_ocr": True},
    )
    assert blocked.status_code == 400
    assert "边界" in blocked.json()["detail"]

    events: list[dict] = []
    monkeypatch.setattr(
        "src.api.workflow_router.append_hitl_event",
        lambda **event: events.append(event) or event,
    )
    process_calls: list[str] = []

    def fake_process(job_id: str, force: bool = False):
        process_calls.append(job_id)
        return JOB_STORE.get(job_id)

    monkeypatch.setattr("src.api.workflow_router.process_pending", fake_process)
    confirmed = client.post(
        f"/api/v1/workflow/jobs/{job['job_id']}/packet/confirm",
        json={"units": [merged], "start_ocr": True},
    )

    assert confirmed.status_code == 200, confirmed.text
    assert process_calls == [job["job_id"]]
    stored = JOB_STORE.get(job["job_id"])
    assert stored is not None
    materialized = [item for item in stored["pending_files"] if item.get("from_packet")]
    assert len(materialized) == 1
    source_packet = materialized[0]["source_packet"]
    assert source_packet["pages"] == [1, 2, 3]
    assert source_packet["business_ids"] == ["SO25-0281", "SO25-0282"]
    assert source_packet["doc_type_source"] == "human"

    [event] = [event for event in events if event["action"] == "packet_confirm"]
    assert event["after"]["manual_boundary_changes"] >= 1
    assert event["after"]["manual_type_overrides"] == 1
    assert event["after"]["business_link_changes"] >= 1
    assert event["after"]["batch_confirmed_units"] == 1


def test_process_endpoint_rechecks_unresolved_confirmed_packet(monkeypatch) -> None:
    job = _create_job("SO25-0281")
    client = TestClient(app)
    analyzed = _upload_and_analyze(client, job, monkeypatch)
    unresolved = _merged_human_unit(analyzed, doc_type="unresolved")
    unresolved["business_ids"] = ["SO25-0281"]

    saved = client.post(
        f"/api/v1/workflow/jobs/{job['job_id']}/packet/confirm",
        json={"units": [unresolved], "start_ocr": False},
    )
    assert saved.status_code == 200, saved.text

    response = client.post(f"/api/v1/workflow/jobs/{job['job_id']}/process")
    assert response.status_code == 409
    assert "单据类型" in str(response.json()["detail"])

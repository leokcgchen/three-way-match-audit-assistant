from __future__ import annotations

import io
import json

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


def _job_with_sample(*business_ids: str) -> dict:
    job = JOB_STORE.create(title="business-hint")
    return JOB_STORE.update(
        job["job_id"],
        sample_population={
            "business_ids": list(business_ids),
            "count": len(business_ids),
            "source": "test",
            "rows": [],
        },
    )


def _post_upload(
    client: TestClient,
    job_id: str,
    *,
    file_name: str = "two-page.pdf",
    business_hints: str | None = None,
):
    data = {"process": "false"}
    if business_hints is not None:
        data["business_hints"] = business_hints
    return client.post(
        f"/api/v1/workflow/jobs/{job_id}/upload",
        files=[("files", (file_name, _pdf_bytes(2), "application/pdf"))],
        data=data,
    )


def test_upload_saves_business_hint_as_unconfirmed_pending_context() -> None:
    job = _job_with_sample("SO25-0281", "SO25-0282")
    client = TestClient(app)

    response = _post_upload(
        client,
        job["job_id"],
        business_hints=json.dumps({"two-page.pdf": ["SO25-0281"]}),
    )

    assert response.status_code == 200, response.text
    [pending] = response.json()["pending_files"]
    assert pending["declared_business_ids"] == ["SO25-0281"]
    assert pending["upload_source"] == "business_row"
    assert pending["packet_kind"] == "packet_single_chain"


def test_upload_rejects_invalid_business_hint_json() -> None:
    job = _job_with_sample("SO25-0281")
    client = TestClient(app)

    response = _post_upload(client, job["job_id"], business_hints="not-json")

    assert response.status_code == 400
    assert "business_hints" in response.json()["detail"]


def test_upload_rejects_business_outside_sample_population() -> None:
    job = _job_with_sample("SO25-0281")
    client = TestClient(app)

    response = _post_upload(
        client,
        job["job_id"],
        business_hints=json.dumps({"two-page.pdf": ["SO25-9999"]}),
    )

    assert response.status_code == 400
    assert "SO25-9999" in response.json()["detail"]
    assert (JOB_STORE.get(job["job_id"]) or {}).get("pending_files") == []


def test_upload_requires_sample_population_for_business_hint() -> None:
    job = JOB_STORE.create(title="no-sample")
    client = TestClient(app)

    response = _post_upload(
        client,
        job["job_id"],
        business_hints=json.dumps({"two-page.pdf": ["SO25-0281"]}),
    )

    assert response.status_code == 400
    assert "抽样清单" in response.json()["detail"]


def test_packet_analyze_keeps_row_hint_as_preselection(monkeypatch) -> None:
    cards = load_category_cards()

    def fake_load(file_name, path, **_kwargs):
        return [
            _page_from_text(
                file_name,
                path,
                1,
                "销售合同\n订单号 SO25-9999\n合同编号 HT25-9999\n合同金额 100",
                "pdf_text",
                cards,
            ),
            _page_from_text(
                file_name,
                path,
                2,
                "合同条款续页\n交货和验收安排按双方约定执行",
                "pdf_text",
                cards,
            ),
        ]

    monkeypatch.setattr("src.workflow.packet_engine.load_file_pages", fake_load)
    job = _job_with_sample("SO25-0281")
    client = TestClient(app)
    uploaded = _post_upload(
        client,
        job["job_id"],
        business_hints=json.dumps({"two-page.pdf": ["SO25-0281"]}),
    )
    assert uploaded.status_code == 200

    response = client.post(f"/api/v1/workflow/jobs/{job['job_id']}/packet/analyze")

    assert response.status_code == 200, response.text
    units = response.json()["packet_units"]
    assert units
    assert all(unit["business_ids"] == ["SO25-0281"] for unit in units)
    assert all(unit["chain_id"] == "SO25-0281" for unit in units)
    assert all(unit["business_binding_source"] is None for unit in units)
    assert all(unit["boundary_confirmed"] is False for unit in units)


def test_upload_without_business_hints_keeps_legacy_shape() -> None:
    job = _job_with_sample("SO25-0281")
    client = TestClient(app)

    response = _post_upload(client, job["job_id"], business_hints=None)

    assert response.status_code == 200
    [pending] = response.json()["pending_files"]
    assert pending.get("declared_business_ids") in (None, [])
    assert pending.get("upload_source") in (None, "mixed_packet")

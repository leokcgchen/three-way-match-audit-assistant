from __future__ import annotations

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from src.api.main import app
from src.api import workflow_router
from src.workflow.job_store import JOB_STORE
from src.workflow.pipeline import _process_one_file


def _job_with_docs(tmp_path):
    job = JOB_STORE.create(title='selected rerun')
    docs = []
    for name in ('invoice.pdf', 'contract.pdf'):
        path = tmp_path / name
        path.write_bytes(name.encode())
        docs.append({'file_name': name, 'path': str(path), 'doc_type': 'invoice' if name.startswith('invoice') else 'contract', 'fields': {}})
    return JOB_STORE.update(job['job_id'], classified=docs, pending_files=[])


def _blank_pdf(path, pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    with path.open('wb') as fh:
        writer.write(fh)


def test_forced_rerun_rejects_empty_selection(tmp_path):
    job = _job_with_docs(tmp_path)
    response = TestClient(app).post(f"/api/v1/workflow/jobs/{job['job_id']}/process?force=true", json={})
    assert response.status_code == 400


def test_forced_rerun_sends_only_selected_file(monkeypatch, tmp_path):
    job = _job_with_docs(tmp_path)
    captured = {}

    class FakeThread:
        def __init__(self, *, kwargs, **_other):
            captured.update(kwargs)

        def start(self):
            return None

    monkeypatch.setattr(workflow_router.threading, 'Thread', FakeThread)
    response = TestClient(app).post(
        f"/api/v1/workflow/jobs/{job['job_id']}/process?force=true",
        json={'file_names': ['invoice.pdf']},
    )
    assert response.status_code == 200, response.text
    assert [spec['filename'] for spec in captured['specs']] == ['invoice.pdf']
    assert response.json()['ocr_has_run'] is True


def test_initial_process_selected_standard_files_keeps_manual_packet(monkeypatch, tmp_path):
    ordinary_path = tmp_path / 'ordinary.pdf'
    packet_path = tmp_path / 'packet.pdf'
    _blank_pdf(ordinary_path)
    _blank_pdf(packet_path, pages=2)
    job = JOB_STORE.create(title='partial initial OCR')
    job = JOB_STORE.update(
        job['job_id'],
        pending_files=[
            {
                'file_name': ordinary_path.name,
                'path': str(ordinary_path),
                'doc_type': 'invoice',
                'packet_kind': 'standard',
                'mixed_packet_declared': False,
            },
            {
                'file_name': packet_path.name,
                'path': str(packet_path),
                'doc_type': 'other',
                'packet_kind': 'packet_single_chain',
                'mixed_packet_declared': True,
            },
        ],
        packet_run={'status': 'needs_review', 'files': []},
        packet_confirmed=False,
    )
    captured = {}

    class FakeThread:
        def __init__(self, *, kwargs, **_other):
            captured.update(kwargs)

        def start(self):
            return None

    monkeypatch.setattr(workflow_router.threading, 'Thread', FakeThread)
    response = TestClient(app).post(
        f"/api/v1/workflow/jobs/{job['job_id']}/process",
        json={'file_names': [ordinary_path.name]},
    )

    assert response.status_code == 200, response.text
    assert [spec['filename'] for spec in captured['specs']] == [ordinary_path.name]
    assert [row['file_name'] for row in captured['remaining_pending']] == [packet_path.name]


def test_unrecognized_foreign_pdf_is_uncertain_other_not_mixed(monkeypatch, tmp_path):
    class FakeAdapter:
        def recognize_document(self, *_args, **_kwargs):
            return {
                'rawText': 'BILL OF LADING\nPort of loading: Shanghai',
                'source': 'pdf_text',
                'confidence': 0.9,
                'textBlocks': [],
            }

        def is_api_configured(self):
            return True

        def extract_fields(self, *_args, **_kwargs):
            return {'documentNo': 'BL-001', 'documentDate': '2026-01-02'}

    monkeypatch.setattr('src.legacy_ocr.LegacyOcrAdapter', FakeAdapter)

    item = _process_one_file(
        filename='foreign-bill-of-lading.pdf',
        content=b'%PDF-foreign-document',
        folder=tmp_path,
    )

    assert item['doc_type'] == 'other'
    assert item['type_uncertain'] is True
    assert item.get('mixed_packet_declared') is not True
    assert item['raw_text'].startswith('BILL OF LADING')

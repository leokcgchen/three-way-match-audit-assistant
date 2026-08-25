"""拆包分笔 API：分析 → 确认物化 → 不得未确认就 OCR。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from src.api.main import app
from src.workflow.job_store import JOB_STORE
from src.workflow.packet_cards import load_category_cards
from src.workflow.packet_engine import packet_blocks_process, packet_needs_review
from src.workflow.packet_split import PageRec, _page_from_text

from tests.test_packet_split import CONTRACT_P1, INVOICE_A, ORDER_P, RECEIPT_P


def _blank_pdf(path: Path, n: int) -> None:
    writer = PdfWriter()
    for _ in range(n):
        writer.add_blank_page(width=595, height=842)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        writer.write(fh)


def _pages_from_texts(name: str, path: str, texts: list[str]) -> list[PageRec]:
    cards = load_category_cards()
    return [
        _page_from_text(name, path, i, text, "pdf_text", cards)
        for i, text in enumerate(texts, start=1)
    ]


def _human_confirm_units(units: list[dict]) -> None:
    """Make the legacy API fixtures represent the new explicit human gate."""
    for unit in units:
        if unit.get("dropped"):
            continue
        chain_id = str(unit.get("chain_id") or "")
        unit["business_ids"] = [chain_id] if chain_id and chain_id != "未识别业务号" else []
        unit["business_binding_source"] = "human"
        unit["boundary_confirmed"] = True


def test_standard_files_skip_unpack(tmp_path: Path):
    job = JOB_STORE.create(title="std")
    job_id = job["job_id"]
    JOB_STORE.set_goals(job_id, ["gospd01010"])
    pdf = tmp_path / "SO25-0296_HT25-0296_05_增值税发票.pdf"
    _blank_pdf(pdf, 1)
    client = TestClient(app)
    with pdf.open("rb") as fh:
        r = client.post(
            f"/api/v1/workflow/jobs/{job_id}/upload",
            files=[("files", (pdf.name, fh, "application/pdf"))],
            data={"process": "false"},
        )
    assert r.status_code == 200
    body = r.json()
    pending = body.get("pending_files") or []
    assert pending
    assert pending[0].get("packet_kind") == "standard"
    r2 = client.post(f"/api/v1/workflow/jobs/{job_id}/packet/analyze")
    assert r2.status_code == 200
    analyzed = r2.json()
    assert (analyzed.get("packet_run") or {}).get("status") == "skipped"
    assert analyzed.get("packet_confirmed") is True
    assert not packet_blocks_process(analyzed)


def test_two_page_standard_document_does_not_require_unpack(tmp_path: Path):
    job = JOB_STORE.create(title="two-page-standard")
    job_id = job["job_id"]
    JOB_STORE.set_goals(job_id, ["gospd01010"])
    pdf = tmp_path / "YW-2025-3962_销售订单_SO-251209-7214.pdf"
    _blank_pdf(pdf, 2)

    client = TestClient(app)
    with pdf.open("rb") as fh:
        response = client.post(
            f"/api/v1/workflow/jobs/{job_id}/upload",
            files=[("files", (pdf.name, fh, "application/pdf"))],
            data={"process": "false"},
        )

    assert response.status_code == 200
    body = response.json()
    pending = body.get("pending_files") or []
    assert pending[0].get("packet_kind") == "standard"
    assert pending[0].get("mixed_packet_declared") is False
    assert not packet_needs_review(body)


def test_unknown_type_is_marked_for_field_review_without_becoming_mixed(tmp_path: Path):
    job = JOB_STORE.create(title="unknown-type")
    job_id = job["job_id"]
    JOB_STORE.set_goals(job_id, ["gospd01010"])
    pdf = tmp_path / "foreign-bill-of-lading.pdf"
    _blank_pdf(pdf, 2)

    client = TestClient(app)
    with pdf.open("rb") as fh:
        response = client.post(
            f"/api/v1/workflow/jobs/{job_id}/upload",
            files=[("files", (pdf.name, fh, "application/pdf"))],
            data={"process": "false"},
        )

    assert response.status_code == 200, response.text
    pending = response.json().get("pending_files") or []
    assert pending[0]["doc_type"] == "other"
    assert pending[0]["type_uncertain"] is True
    assert pending[0]["mixed_packet_declared"] is False
    assert not packet_needs_review(response.json())


def test_auditor_can_move_a_reviewed_pdf_into_manual_unpack(tmp_path: Path):
    pdf = tmp_path / "reviewed-foreign-document.pdf"
    _blank_pdf(pdf, 2)
    job = JOB_STORE.create(title="review-to-mixed")
    job = JOB_STORE.update(
        job["job_id"],
        active_step="field_confirm",
        classified=[{
            "file_name": pdf.name,
            "path": str(pdf),
            "doc_type": "other",
            "type_uncertain": True,
            "raw_text": "PAGE ONE\nPAGE TWO",
            "fields": {"documentNo": "BL-001"},
        }],
        pending_files=[],
    )

    response = TestClient(app).post(
        f"/api/v1/workflow/jobs/{job['job_id']}/documents/declare-mixed",
        json={"file_name": pdf.name},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["classified"] == []
    assert body["active_step"] == "packet_unpack"
    assert body["packet_run"]["status"] == "pending_analyze"
    pending = body["pending_files"]
    assert pending[0]["file_name"] == pdf.name
    assert pending[0]["mixed_packet_declared"] is True
    assert pending[0]["type_uncertain"] is False
    assert pdf.is_file()


def test_manual_mixed_packet_requires_unpack(tmp_path: Path):
    job = JOB_STORE.create(title="manual-mixed")
    job_id = job["job_id"]
    JOB_STORE.set_goals(job_id, ["gospd01010"])
    pdf = tmp_path / "审计师声明的混装资料包.pdf"
    _blank_pdf(pdf, 2)

    client = TestClient(app)
    with pdf.open("rb") as fh:
        response = client.post(
            f"/api/v1/workflow/jobs/{job_id}/upload",
            files=[("files", (pdf.name, fh, "application/pdf"))],
            data={"process": "false", "mixed_packet": "true"},
        )

    assert response.status_code == 200
    body = response.json()
    pending = body.get("pending_files") or []
    assert pending[0].get("mixed_packet_declared") is True
    assert pending[0].get("packet_kind") == "packet_single_chain"
    assert packet_needs_review(body)


def test_situation_a_analyze_and_confirm_to_classified(tmp_path: Path, monkeypatch):
    texts = [CONTRACT_P1, ORDER_P, INVOICE_A, RECEIPT_P]

    def fake_load(file_name, path, **_kw):
        return _pages_from_texts(file_name, path, texts)

    monkeypatch.setattr("src.workflow.packet_engine.load_file_pages", fake_load)

    job = JOB_STORE.create(title="pack-a")
    job_id = job["job_id"]
    JOB_STORE.set_goals(job_id, ["gospd01010"])
    pdf = tmp_path / "凭证包A.pdf"
    _blank_pdf(pdf, 4)
    client = TestClient(app)
    with pdf.open("rb") as fh:
        r = client.post(
            f"/api/v1/workflow/jobs/{job_id}/upload",
            files=[("files", (pdf.name, fh, "application/pdf"))],
            data={"process": "false", "mixed_packet": "true"},
        )
    assert r.status_code == 200
    pending = r.json().get("pending_files") or []
    assert pending[0].get("packet_kind") == "packet_single_chain"

    r_plan = client.put(
        f"/api/v1/workflow/jobs/{job_id}/field-plan?confirm=true",
        json={"by_type": {}, "global_extra": [], "confirmed": True},
    )
    assert r_plan.status_code == 200

    r_proc = client.post(f"/api/v1/workflow/jobs/{job_id}/process")
    assert r_proc.status_code == 409
    detail = r_proc.json().get("detail")
    assert "拆包" in str(detail)
    job_from_err = detail.get("job") if isinstance(detail, dict) else None
    assert job_from_err
    units = job_from_err.get("packet_units") or []
    types = {u.get("doc_type") for u in units}
    assert "contract" in types
    assert "invoice" in types
    chains = {u.get("chain_id") for u in units}
    assert len(chains) == 1
    _human_confirm_units(units)

    r_confirm = client.post(
        f"/api/v1/workflow/jobs/{job_id}/packet/confirm",
        json={"units": units, "start_ocr": False},
    )
    assert r_confirm.status_code == 200
    confirmed = r_confirm.json()
    assert confirmed.get("packet_confirmed") is True
    pending2 = confirmed.get("pending_files") or []
    names = [str(p.get("file_name") or "") for p in pending2]
    assert any("_contract_" in n for n in names)
    assert any("_invoice_" in n for n in names)
    assert not packet_needs_review(confirmed)


def test_situation_b_multi_so_not_one_chain(tmp_path: Path, monkeypatch):
    so1 = "销售合同\n合同编号 HT25-0001\n甲方 甲\n乙方 乙\n服务范围 实施\n合同金额 100\n订单号 SO25-0001"
    so2 = "销售合同\n合同编号 HT25-0002\n甲方 甲\n乙方 乙\n服务范围 实施\n合同金额 200\n订单号 SO25-0002"
    inv1 = INVOICE_A + "\n订单号 SO25-0001"
    inv2 = (
        "增值税专用发票\n发票代码 222\n发票号码 22222222\n价税合计 800\n税额 104\n"
        "购买方 甲\n销售方 乙\n开票日期 2025-12-03\n订单号 SO25-0002"
    )
    texts = [so1, inv1, so2, inv2]

    def fake_load(file_name, path, **_kw):
        return _pages_from_texts(file_name, path, texts)

    monkeypatch.setattr("src.workflow.packet_engine.load_file_pages", fake_load)

    job = JOB_STORE.create(title="pack-b")
    job_id = job["job_id"]
    JOB_STORE.set_goals(job_id, ["gospd01010"])
    pdf = tmp_path / "混装多笔.pdf"
    _blank_pdf(pdf, 4)
    client = TestClient(app)
    with pdf.open("rb") as fh:
        r = client.post(
            f"/api/v1/workflow/jobs/{job_id}/upload",
            files=[("files", (pdf.name, fh, "application/pdf"))],
            data={"process": "false", "mixed_packet": "true"},
        )
    assert r.status_code == 200
    analyzed = client.post(f"/api/v1/workflow/jobs/{job_id}/packet/analyze").json()
    units = analyzed.get("packet_units") or []
    chains = {u.get("chain_id") for u in units}
    assert "SO25-0001" in chains
    assert "SO25-0002" in chains
    assert len(chains) >= 2

    # 人工把未识别的拖到 SO25-0002 后再确认
    for u in units:
        if u.get("chain_id") not in {"SO25-0001", "SO25-0002"}:
            u["chain_id"] = "SO25-0002"
    _human_confirm_units(units)
    r_conf = client.post(
        f"/api/v1/workflow/jobs/{job_id}/packet/confirm",
        json={"units": units, "start_ocr": False},
    )
    assert r_conf.status_code == 200
    pending = r_conf.json().get("pending_files") or []
    assert pending
    assert all(p.get("from_packet") for p in pending)
    names = " ".join(str(p.get("file_name")) for p in pending)
    assert "SO25-0001" in names
    assert "SO25-0002" in names


def test_dropped_page_is_covered_but_not_materialized(tmp_path: Path, monkeypatch):
    texts = [CONTRACT_P1, ORDER_P, INVOICE_A, "模糊空白"]

    def fake_load(file_name, path, **_kw):
        return _pages_from_texts(file_name, path, texts)

    monkeypatch.setattr("src.workflow.packet_engine.load_file_pages", fake_load)

    job = JOB_STORE.create(title="pack-drop")
    job_id = job["job_id"]
    JOB_STORE.set_goals(job_id, ["gospd01010"])
    pdf = tmp_path / "带空白页.pdf"
    _blank_pdf(pdf, 4)
    client = TestClient(app)
    with pdf.open("rb") as fh:
        client.post(
            f"/api/v1/workflow/jobs/{job_id}/upload",
            files=[("files", (pdf.name, fh, "application/pdf"))],
            data={"process": "false", "mixed_packet": "true"},
        )
    units = client.post(f"/api/v1/workflow/jobs/{job_id}/packet/analyze").json().get("packet_units") or []
    assert units
    src = units[0]
    for u in units:
        pages = [p for p in (u.get("pages") or []) if p != 4]
        u["pages"] = pages
        if pages:
            u["page_start"] = pages[0]
            u["page_end"] = pages[-1]
    units = [u for u in units if u.get("pages")]
    _human_confirm_units(units)
    dropped = {
        "unit_id": "du_drop4",
        "source_file": src.get("source_file"),
        "source_path": src.get("source_path"),
        "pages": [4],
        "doc_type": "unresolved",
        "card_type": "unresolved",
        "chain_id": src.get("chain_id") or "未识别业务号",
        "dropped": True,
        "keys": {},
    }
    silent = client.post(
        f"/api/v1/workflow/jobs/{job_id}/packet/confirm",
        json={"units": units, "start_ocr": False},
    )
    assert silent.status_code == 400
    ok = client.post(
        f"/api/v1/workflow/jobs/{job_id}/packet/confirm",
        json={"units": units + [dropped], "start_ocr": False},
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    pending = body.get("pending_files") or []
    names = " ".join(str(p.get("file_name") or "") for p in pending)
    assert "p4-4" not in names
    assert (body.get("packet_run") or {}).get("dropped_pages")


def test_packet_analyze_is_exclusive_per_job(tmp_path: Path, monkeypatch):
    import threading
    import time

    from src.api.workflow_router import _execute_packet_analyze

    started = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    def fake_analyze(job, **_kw):
        calls.append(1)
        started.set()
        assert release.wait(timeout=3)
        return {
            "packet_run": {"status": "needs_review", "files": []},
            "packet_units": [{"unit_id": "u1", "pages": [1]}],
            "pending_files": job.get("pending_files") or [],
            "packet_confirmed": False,
        }

    monkeypatch.setattr("src.workflow.packet_engine.analyze_pending_packets", fake_analyze)

    job = JOB_STORE.create(title="pack-exclusive")
    job_id = job["job_id"]
    JOB_STORE.set_goals(job_id, ["gospd01010"])
    pdf = tmp_path / "凭证包互斥.pdf"
    _blank_pdf(pdf, 2)
    client = TestClient(app)
    with pdf.open("rb") as fh:
        client.post(
            f"/api/v1/workflow/jobs/{job_id}/upload",
            files=[("files", (pdf.name, fh, "application/pdf"))],
            data={"process": "false", "mixed_packet": "true"},
        )

    winners: list[bool] = []

    def hit():
        out = _execute_packet_analyze(job_id, use_vlm=False)
        winners.append(out is not None)

    t1 = threading.Thread(target=hit)
    t2 = threading.Thread(target=hit)
    t1.start()
    assert started.wait(timeout=3)
    t2.start()
    time.sleep(0.2)
    assert len(calls) == 1
    release.set()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert winners.count(True) == 1
    assert winners.count(False) == 1
    assert len(calls) == 1

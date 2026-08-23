from pathlib import Path

from src.workflow.demo_ocr_cache import (
    apply_demo_hit,
    harvest_from_job,
    is_demo_filename,
    lookup_demo_ocr,
)
from src.workflow.pipeline import _process_one_file, file_fingerprint


def test_demo_filename_detects_six_samples():
    assert is_demo_filename("SO25-0281_HT25-0281_05_增值税发票.pdf")
    assert is_demo_filename("SO25-0296_HT25-0296_05_增值税发票.png")
    assert is_demo_filename("SO25-0286_EXKJHT25-0286_01_销售合同.pdf")
    assert not is_demo_filename("random.pdf")


def test_harvest_and_process_skips_remote_ocr(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("src.workflow.demo_ocr_cache.CACHE_DIR", tmp_path)
    monkeypatch.setattr("src.workflow.demo_ocr_cache.demo_cache_enabled", lambda: True)
    job = {
        "classified": [
            {
                "file_name": "SO25-0281_HT25-0281_02_销售订单.pdf",
                "doc_type": "order",
                "fields": {"orderNo": "SO25-0281", "totalAmount": "10942.90"},
                "raw_text": "销售订单 SO25-0281 价税合计 10,942.90",
            }
        ]
    }
    index = harvest_from_job(job)
    assert index["count"] == 1
    hit = lookup_demo_ocr("SO25-0281_HT25-0281_02_销售订单.pdf")
    assert hit and hit["fields"]["orderNo"] == "SO25-0281"

    def _boom(*_a, **_k):
        raise AssertionError("命中演示缓存时不应调用远程 OCR")

    monkeypatch.setattr("src.legacy_ocr.ocr_adapter.LegacyOcrAdapter.recognize_document", _boom)
    content = b"%PDF-1.4 demo"
    out = _process_one_file(
        filename="SO25-0281_HT25-0281_02_销售订单.pdf",
        content=content,
        folder=tmp_path / "work",
        fingerprint=file_fingerprint("SO25-0281_HT25-0281_02_销售订单.pdf", content),
        demo_delay_sec=0,
    )
    assert out["ocr_source"] == "demo_cache"
    assert out["demo_ocr_cache"] is True
    assert out["fields"]["orderNo"] == "SO25-0281"
    assert Path(out["path"]).is_file()


def test_apply_demo_hit_uses_uploaded_path(tmp_path: Path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")
    item = apply_demo_hit(
        filename="SO25-0296_HT25-0296_05_增值税发票.png",
        path=str(pdf),
        fingerprint="abc",
        slot_hint="",
        payload={
            "doc_type": "invoice",
            "fields": {"amount": 68400, "taxAmount": 8405.9, "totalAmount": 73066.7},
            "raw_text": "价税合计 73,066.70",
        },
        delay_sec=0,
    )
    assert item["path"] == str(pdf)
    assert item["ocr_source"] == "demo_cache"


def test_harvest_replays_ocr_raw_not_confirmed_amount(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("src.workflow.demo_ocr_cache.CACHE_DIR", tmp_path)
    monkeypatch.setattr("src.workflow.demo_ocr_cache.demo_cache_enabled", lambda: True)
    harvest_from_job(
        {
            "classified": [
                {
                    "file_name": "SO25-0296_HT25-0296_05_增值税发票.png",
                    "doc_type": "invoice",
                    "fields": {"amount": 64660.8, "taxAmount": 8405.9, "totalAmount": 73066.7},
                    "raw_text": "折扣前商品金额68，400.00 价税合计 73,066.70",
                    "_field_meta": {
                        "amount": {"raw_value": 68400, "status": "ACCEPTED"},
                        "taxAmount": {"raw_value": 8405.9, "status": "ACCEPTED"},
                        "totalAmount": {"raw_value": 73066.7, "status": "ACCEPTED"},
                    },
                    "_amount_ambiguities": [
                        {
                            "field_key": "amount",
                            "status": "CONFIRMED",
                            "candidates": [
                                {"candidate_id": "C1", "value": 68400.0},
                                {"candidate_id": "C2", "value": 64660.8},
                            ],
                            "ai_recommendation": {
                                "candidate_id": "C2",
                                "reason": "旧说明",
                                "review_status": "RECOMMENDED",
                            },
                        }
                    ],
                }
            ]
        }
    )
    hit = lookup_demo_ocr("SO25-0296_HT25-0296_05_增值税发票.png")
    assert hit and float(hit["fields"]["amount"]) == 68400
    assert hit.get("amount_ai_seeds")

    from src.workflow.amount_ambiguity import scan_document

    item = apply_demo_hit(
        filename="SO25-0296_HT25-0296_05_增值税发票.png",
        path=str(tmp_path / "inv.png"),
        fingerprint="x",
        slot_hint="",
        payload=hit,
        delay_sec=0,
    )
    (tmp_path / "inv.png").write_bytes(b"x")
    rows = [r for r in (item.get("_amount_ambiguities") or []) if r.get("field_key") == "amount"]
    assert rows
    rec = rows[0].get("ai_recommendation") or {}
    assert float(rec.get("recommended_value") or 0) == 64660.8
    assert rec.get("candidate_id")
    assert "64,660.80" in str(rec.get("reason") or "") or "64660" in str(rec.get("reason") or "")


def test_demo_delay_scales_to_five_seconds():
    from src.workflow.demo_ocr_cache import demo_file_delay_sec

    assert abs(demo_file_delay_sec(1, workers=6) - 5.0) < 1e-6
    assert abs(demo_file_delay_sec(25, workers=6) * (25 / 6) - 5.0) < 1e-6

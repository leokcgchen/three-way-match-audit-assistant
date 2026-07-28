"""FastAPI /upload 三单数据能力测试。"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from docx import Document
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.api.main import app

client = TestClient(app)

CUTOFF_CONTRACT = """
软件开发服务合同
合同编号：HT-2026-API-CUTOFF
合同名称：软件开发服务合同（含账期）
甲方：云创科技
乙方：智汇数据
签订日期：2026-03-15
合同金额：500万元
一、乙方应向甲方提供软件开发服务，并按约定交付产品。
二、货物签收后10日，控制权转移至甲方并确认收入。
三、本合同收入按时点法确认。
"""


def _make_docx(text: str) -> Path:
    tmp = Path(tempfile.mkdtemp()) / "api_cutoff.docx"
    doc = Document()
    for line in text.strip().splitlines():
        doc.add_paragraph(line)
    doc.save(str(tmp))
    return tmp


def test_upload_only_contract() -> None:
    path = _make_docx(CUTOFF_CONTRACT)
    with path.open("rb") as f:
        resp = client.post(
            "/upload",
            files={
                "file": (
                    path.name,
                    f,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data.get("cutoff_test_result") is None
    assert data["to_downstream_json"].get("cutoff_test_status") is None
    print("test_upload_only_contract: PASS")


def test_upload_with_ledger_only() -> None:
    path = _make_docx(CUTOFF_CONTRACT)
    ledger = {
        "entry_date": "2026-06-11",
        "entry_amount": 500.0,
        "voucher_id": "记-126",
        "customer_name": "云创科技",
    }
    with path.open("rb") as f:
        resp = client.post(
            "/upload",
            files={
                "file": (
                    path.name,
                    f,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            data={"ledger_entry": json.dumps(ledger, ensure_ascii=False)},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    cutoff = data.get("cutoff_test_result")
    assert cutoff is not None
    assert cutoff["test_status"] == "WARNING"
    assert "缺少签收日期或入账日期" in cutoff["issue_description"]
    print("test_upload_with_ledger_only: PASS")


def test_upload_full_data() -> None:
    path = _make_docx(CUTOFF_CONTRACT)
    ledger = {
        "entry_date": "2026-06-11",
        "entry_amount": 500.0,
        "voucher_id": "记-126",
        "customer_name": "云创科技",
    }
    receipt = {
        "receipt_date": "2026-06-01",
        "received_quantity": 1.0,
        "receiver_name": "张三",
        "notes": "验收合格",
    }
    with path.open("rb") as f:
        resp = client.post(
            "/upload",
            files={
                "file": (
                    path.name,
                    f,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            data={
                "ledger_entry": json.dumps(ledger, ensure_ascii=False),
                "delivery_receipt": json.dumps(receipt, ensure_ascii=False),
            },
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    cutoff = data.get("cutoff_test_result")
    assert cutoff is not None
    assert cutoff["test_status"] == "PASS"
    assert cutoff["deviation_days"] == 0
    assert cutoff["expected_revenue_date"] == "2026-06-11"
    assert data["to_downstream_json"]["cutoff_test_status"] == "PASS"
    assert data["to_downstream_json"]["expected_revenue_date"] == "2026-06-11"
    print("test_upload_full_data: PASS")


def test_invalid_json() -> None:
    path = _make_docx(CUTOFF_CONTRACT)
    with path.open("rb") as f:
        resp = client.post(
            "/upload",
            files={
                "file": (
                    path.name,
                    f,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            data={"ledger_entry": "{not-valid-json"},
        )
    assert resp.status_code == 422, resp.text
    detail = resp.json().get("detail", "")
    assert "ledger_entry" in str(detail)
    print("test_invalid_json: PASS")


if __name__ == "__main__":
    test_upload_only_contract()
    test_upload_with_ledger_only()
    test_upload_full_data()
    test_invalid_json()
    print("全部测试通过：API 三单数据上传能力正常。")

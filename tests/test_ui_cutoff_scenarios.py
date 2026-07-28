"""Phase B 场景验证：模拟 Streamlit 提交的 FormData 四种模式。"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import requests
from docx import Document

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

API = "http://127.0.0.1:8000"

TEXT = """
软件开发服务合同
合同编号：HT-2026-UI-CUTOFF
合同名称：软件开发服务合同（含账期）
甲方：云创科技
乙方：智汇数据
签订日期：2026-03-15
合同金额：500万元
一、乙方应向甲方提供软件开发服务，并按约定交付产品。
二、货物签收后10日，控制权转移至甲方并确认收入。
三、本合同收入按时点法确认。
"""


def _docx() -> Path:
    path = Path(tempfile.mkdtemp()) / "ui_cutoff.docx"
    doc = Document()
    for line in TEXT.strip().splitlines():
        doc.add_paragraph(line)
    doc.save(str(path))
    return path


def _upload(path: Path, data: dict | None = None) -> dict:
    with path.open("rb") as f:
        resp = requests.post(
            f"{API}/upload",
            files={
                "file": (
                    path.name,
                    f,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            data=data,
            timeout=120,
        )
    assert resp.status_code == 200, resp.text
    return resp.json()


def main() -> None:
    path = _docx()

    # 1) 只上传合同
    r1 = _upload(path)
    assert r1.get("cutoff_test_result") is None
    print("场景1 只上传合同: PASS（无 cutoff_test_result）")

    # 2) 完整三单
    r2 = _upload(
        path,
        {
            "ledger_entry": json.dumps(
                {
                    "entry_date": "2026-06-11",
                    "entry_amount": 500.0,
                    "voucher_id": "记-126",
                    "customer_name": "云创科技",
                },
                ensure_ascii=False,
            ),
            "delivery_receipt": json.dumps(
                {
                    "receipt_date": "2026-06-01",
                    "received_quantity": 1.0,
                    "receiver_name": "张三",
                    "notes": "验收合格",
                },
                ensure_ascii=False,
            ),
        },
    )
    assert r2["cutoff_test_result"]["test_status"] == "PASS"
    print("场景2 完整三单: PASS")

    # 3) 只填序时账
    r3 = _upload(
        path,
        {
            "ledger_entry": json.dumps(
                {"entry_date": "2026-06-11", "entry_amount": 500.0},
                ensure_ascii=False,
            )
        },
    )
    assert r3["cutoff_test_result"]["test_status"] == "WARNING"
    print("场景3 仅序时账: PASS（WARNING）")

    # 4) 提前确认 FAIL
    r4 = _upload(
        path,
        {
            "ledger_entry": json.dumps(
                {"entry_date": "2026-06-05", "entry_amount": 500.0},
                ensure_ascii=False,
            ),
            "delivery_receipt": json.dumps(
                {"receipt_date": "2026-06-01"},
                ensure_ascii=False,
            ),
        },
    )
    assert r4["cutoff_test_result"]["test_status"] == "FAIL"
    assert r4["cutoff_test_result"]["deviation_days"] == -6
    print("场景4 提前入账: PASS（FAIL / -6天）")
    print("Phase B 四场景后端链路验证通过。")


if __name__ == "__main__":
    main()

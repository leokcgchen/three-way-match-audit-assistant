"""API 冒烟验证脚本。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import requests
from docx import Document

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:8000"

TEXT = """
软件开发服务合同
合同编号：HT-2026-API-001
合同名称：软件开发服务合同
甲方：云创科技
乙方：智汇数据
签订日期：2026-03-15
合同金额：500万元
一、乙方应向甲方提供软件开发服务，并按约定交付产品。
二、货物验收合格后，控制权转移至甲方。
"""


def main() -> None:
    health = requests.get(f"{BASE}/health", timeout=10)
    assert health.status_code == 200 and health.json()["status"] == "ok"
    print("health: PASS")

    docs = requests.get(f"{BASE}/docs", timeout=10)
    assert docs.status_code == 200
    print("docs: PASS")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "api_test.docx"
        doc = Document()
        for line in TEXT.strip().splitlines():
            doc.add_paragraph(line)
        doc.save(str(path))
        with path.open("rb") as f:
            resp = requests.post(
                f"{BASE}/upload",
                files={
                    "file": (
                        "api_test.docx",
                        f,
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
                timeout=60,
            )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data.get("report_id")
    assert "contract_info" in data
    assert "to_downstream_json" in data
    print(f"upload: PASS report_id={data['report_id']}")

    get_resp = requests.get(f"{BASE}/report/{data['report_id']}", timeout=10)
    assert get_resp.status_code == 200
    print("get_report: PASS")

    bad = requests.post(
        f"{BASE}/upload",
        files={"file": ("x.txt", b"hello", "text/plain")},
        timeout=10,
    )
    assert bad.status_code == 400
    print("bad_format: PASS")
    print("全部 API 冒烟测试通过。")


if __name__ == "__main__":
    main()

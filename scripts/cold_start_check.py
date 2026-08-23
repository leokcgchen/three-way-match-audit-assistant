"""冷启验收：依赖导入、正式 Mock 默认、health 契约。

用法（在项目根）:
  set PYTHONPATH=.
  python scripts/cold_start_check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    errors: list[str] = []

    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        errors.append("缺依赖 pdfplumber（请 pip install -r requirements.txt）")
    try:
        import pypdfium2  # noqa: F401
    except ImportError:
        errors.append("缺依赖 pypdfium2（请 pip install -r requirements.txt）")

    from src.legacy_ocr.ocr_adapter import LegacyOcrAdapter

    adapter = LegacyOcrAdapter()
    if adapter.use_mock_when_unavailable:
        errors.append(
            "AUDIT_ALLOW_OCR_MOCK 当前允许 Mock；正式审计请设为 0"
        )

    from src.api.main import app, health

    payload = health()
    if payload.get("status") != "ok":
        errors.append(f"health.status 异常: {payload}")
    if "audit" not in payload:
        errors.append("health 缺少 audit 快照")
    if not str(app.version).startswith("0.8"):
        errors.append(f"期望版本 0.8.x，实际 {app.version}")

    from src.audit.program_matrix import get_program_matrix

    m = get_program_matrix("gospd01010")
    if not m.get("found"):
        errors.append("program_matrix gospd01010 未找到")
    m30 = get_program_matrix("gospd01030")
    if not m30.get("found"):
        errors.append("program_matrix gospd01030 未找到")
    else:
        ids = [a.get("id") for a in (m30.get("assertions") or [])]
        if "ar_period" not in ids:
            errors.append("program_matrix gospd01030 缺 ar_period（步骤3）")

    audit = payload.get("audit") or {}
    warns: list[str] = []
    if audit.get("formal_ocr") and not (
        audit.get("require_fields_confirmed_api")
        and audit.get("require_matching_confirmed_api")
        and audit.get("require_conclusion_confirmed_api")
    ):
        warns.append(
            "正式 OCR 已开，但 REQUIRE_* 未全开（.env 可能显式=0）；"
            "独立三单/金额 API 无 job 确认也可调用。建议 REQUIRE_*=auto 或 1"
        )

    if errors:
        print("FAIL cold_start_check:")
        for e in errors:
            print(" -", e)
        return 1
    print("OK cold_start_check")
    print(" version=", app.version)
    print(" formal_ocr=", payload["audit"].get("formal_ocr"))
    print(" allow_ocr_mock=", payload["audit"].get("allow_ocr_mock"))
    print(
        " require_fields/matching/conclusion=",
        payload["audit"].get("require_fields_confirmed_api"),
        payload["audit"].get("require_matching_confirmed_api"),
        payload["audit"].get("require_conclusion_confirmed_api"),
    )
    for w in warns:
        print(" WARN", w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""GOSPD01030 真 OCR 端到端（upload→field-plan→process→门禁→导出）。

依赖 OCR Key，并使用明确的本地 PDF 夹具执行正式上传与识别。

用法:
  set PYTHONPATH=.
  set ACCEPT_01030_OCR=1
  python scripts/accept_gospd01030_ocr_e2e.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MOCK = ROOT / "data" / "mock" / "SO25-0281"
PERIOD_END = "2025-12-15"
USE_HTTP = os.environ.get("ACCEPT_01030_HTTP", "").strip() in {"1", "true", "yes"}
BASE = os.environ.get("ACCEPT_01030_BASE", "http://127.0.0.1:8000").rstrip("/")

_client = None


def _client_obj():
    global _client
    if _client is None:
        from fastapi.testclient import TestClient
        from src.api.main import app

        _client = TestClient(app)
    return _client


def call(method: str, path: str, data=None, *, timeout: int = 600):
    if not USE_HTTP:
        c = _client_obj()
        kw = {}
        if data is not None:
            kw["json"] = data
        r = c.request(method.upper(), path, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text}")
        return r.json() if r.content else None
    import urllib.error
    import urllib.request

    body = None if data is None else json.dumps(data).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return json.loads(raw.decode("utf-8")) if raw else None


def upload_pdfs(job_id: str, paths: list[Path]) -> dict:
    if USE_HTTP:
        raise RuntimeError("HTTP multipart 请用 e2e_p0_p4；本脚本默认 TestClient")
    c = _client_obj()
    files = []
    for p in paths:
        files.append(("files", (p.name, p.read_bytes(), "application/pdf")))
    r = c.post(
        f"/api/v1/workflow/jobs/{job_id}/upload",
        files=files,
        data={"process": "false"},
    )
    if r.status_code >= 400:
        raise RuntimeError(f"upload -> {r.status_code}: {r.text}")
    return r.json()


def ok(msg: str) -> None:
    print(f"  OK  {msg}")


def main() -> int:
    if os.environ.get("ACCEPT_01030_OCR", "").strip() not in {"1", "true", "yes"}:
        print("SKIP: set ACCEPT_01030_OCR=1 to run real OCR e2e")
        return 0
    pdfs = sorted(MOCK.glob("*.pdf"))
    if len(pdfs) < 4:
        print(f"FAIL mock pdfs under {MOCK}")
        return 1

    health = call("GET", "/health")
    assert health.get("status") == "ok"
    ok(f"health version={health.get('version')}")

    job = call("POST", "/api/v1/workflow/jobs", {"title": "01030-ocr-e2e"})
    jid = job["job_id"]
    call(
        "PUT",
        f"/api/v1/workflow/jobs/{jid}/goals",
        {
            "goal_ids": ["gospd01030"],
            "period_end": PERIOD_END,
            "entity_name": "OCR端到端",
            "calendar_mode": "natural_month",
        },
    )
    # 抽样清单（模块30）
    call(
        "PUT",
        f"/api/v1/workflow/jobs/{jid}/sample-population",
        {"business_ids": ["SO25-0281"], "source": "ocr_e2e"},
    )
    ok("goals + sample-population")

    job = upload_pdfs(jid, pdfs)
    pending = job.get("pending_files") or []
    assert len(pending) >= 4
    ok(f"uploaded pending={len(pending)}")

    plan = job.get("field_plan") or {}
    job = call(
        "PUT",
        f"/api/v1/workflow/jobs/{jid}/field-plan?confirm=true",
        {
            "by_type": plan.get("by_type"),
            "global_extra": plan.get("global_extra") or [],
            "confirmed": True,
        },
    )
    call("POST", f"/api/v1/workflow/jobs/{jid}/process")
    # 等待 OCR 后台完成
    for i in range(120):
        job = call("GET", f"/api/v1/workflow/jobs/{jid}")
        if not job.get("ocr_processing") and (job.get("classified") or []):
            break
        time.sleep(2)
    classified = job.get("classified") or []
    if not classified:
        raise RuntimeError("OCR 未产出 classified")
    sources = {c.get("ocr_source") for c in classified}
    if sources <= {"mock"}:
        raise RuntimeError(f"期望真 OCR，实际 sources={sources}")
    ok(f"OCR docs={len(classified)} sources={sorted(sources)}")

    # 后续复用 seed e2e 的 HITL 路径：直接调子脚本逻辑太重，这里走最小确认
    call("POST", f"/api/v1/workflow/jobs/{jid}/hitl/fields/confirm")
    chains = call("GET", f"/api/v1/workflow/jobs/{jid}/chains")
    primary = next(
        (
            c
            for c in (chains.get("chains") or [])
            if str(c.get("chain_id") or "").upper().startswith("SO")
        ),
        (chains.get("chains") or [{}])[0],
    )
    cid = primary.get("chain_id")
    assert cid
    call("PUT", f"/api/v1/workflow/jobs/{jid}/active-chain", {"chain_id": cid})
    call("POST", f"/api/v1/workflow/jobs/{jid}/evidence-match")
    # 清顾问/关系
    try:
        adv = call("GET", f"/api/v1/workflow/jobs/{jid}/advisory")
        for p in adv.get("pending") or []:
            if p.get("candidate_id"):
                call(
                    "POST",
                    f"/api/v1/workflow/jobs/{jid}/advisory/{urllib.parse.quote(p['candidate_id'])}/decide",
                    {"status": "REJECTED", "reason": "ocr-e2e", "auto_replay": False},
                )
    except Exception as exc:  # noqa: BLE001
        print("  WARN advisory", exc)
    job = call("GET", f"/api/v1/workflow/jobs/{jid}")
    for r in job.get("relations") or []:
        if r.get("status") == "PROPOSED":
            call(
                "POST",
                f"/api/v1/workflow/jobs/{jid}/relations/{urllib.parse.quote(r['relation_id'])}/decide",
                {"status": "VERIFIED", "reason": "ocr-e2e"},
            )
    call(
        "POST",
        f"/api/v1/workflow/jobs/{jid}/hitl/matching/confirm",
        {"reason": "ocr-e2e"},
    )
    call("POST", f"/api/v1/workflow/jobs/{jid}/three-way-cutoff", {})
    try:
        trace = call("GET", f"/api/v1/workflow/jobs/{jid}/conclusion-trace")
        for f in trace.get("findings") or []:
            if f.get("blocking") and not f.get("acknowledged"):
                call(
                    "POST",
                    f"/api/v1/workflow/jobs/{jid}/hitl/finding/acknowledge",
                    {
                        "finding_id": f["finding_id"],
                        "genuine": True,
                        "reason": "ocr-e2e",
                    },
                )
    except Exception as exc:  # noqa: BLE001
        print("  WARN trace", exc)
    call(
        "POST",
        f"/api/v1/workflow/jobs/{jid}/hitl/conclusion/confirm",
        {"reason": "ocr-e2e"},
    )
    job = call("POST", f"/api/v1/workflow/jobs/{jid}/workbook/export")
    path = job.get("workbook_path") or ""
    if not path:
        raise RuntimeError("export missing path")
    ok(f"export {path}")
    print("\nALL 01030 OCR E2E CHECKS PASSED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"\nFAIL: {exc}")
        raise

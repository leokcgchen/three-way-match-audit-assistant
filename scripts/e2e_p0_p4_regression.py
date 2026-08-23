"""P0–P4 回归：上传→轻量分类→字段清单→OCR→高亮/取证→Gate5 行结论。

用法（API 已起在 8000）：
  python scripts/e2e_p0_p4_regression.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8000"
MOCK = ROOT / "data" / "mock" / "SO25-0281"


def call(method: str, path: str, data=None, *, timeout: int = 300):
    body = None if data is None else json.dumps(data).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if not raw:
                return None
            ct = r.headers.get("Content-Type") or ""
            if "application/json" in ct:
                return json.loads(raw.decode("utf-8"))
            return raw
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} -> {exc.code}: {detail}") from exc


def call_expect_status(method: str, path: str, data=None, *, expect: int, timeout: int = 60):
    body = None if data is None else json.dumps(data).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raise RuntimeError(f"expected {expect}, got {r.status} for {method} {path}")
    except urllib.error.HTTPError as exc:
        if exc.code != expect:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"{method} {path} expected {expect}, got {exc.code}: {detail}"
            ) from exc
        return exc.read().decode("utf-8", errors="replace")


def upload_files(job_id: str, paths: list[Path]) -> dict:
    boundary = "----WebKitFormBoundaryE2E7"
    parts: list[bytes] = []
    for p in paths:
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            (
                f'Content-Disposition: form-data; name="files"; filename="{p.name}"\r\n'
                f"Content-Type: application/pdf\r\n\r\n"
            ).encode()
        )
        parts.append(p.read_bytes())
        parts.append(b"\r\n")
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(b'Content-Disposition: form-data; name="process"\r\n\r\nfalse\r\n')
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(
        f"{BASE}/api/v1/workflow/jobs/{job_id}/upload",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def get_bytes(path: str, *, timeout: int = 120) -> tuple[bytes, dict]:
    req = urllib.request.Request(BASE + path, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        # HTTPMessage 大小写不敏感；转 dict 后键变小写
        headers = {str(k).lower(): v for k, v in r.headers.items()}
        return r.read(), headers


def ok(msg: str) -> None:
    print(f"  OK  {msg}")


def step(title: str) -> None:
    print(f"\n== {title} ==")


def main() -> int:
    health = call("GET", "/health")
    if not health or health.get("status") != "ok":
        print("FAIL health", health)
        return 1
    ok(f"health phase={health.get('phase')}")

    pdfs = sorted(MOCK.glob("*.pdf"))
    if len(pdfs) < 3:
        print(f"FAIL mock PDFs missing under {MOCK}")
        return 1

    step("创建 job + goals gospd01030")
    job = call("POST", "/api/v1/workflow/jobs", {"title": "P0-P4回归"})
    jid = job["job_id"]
    job = call(
        "PUT",
        f"/api/v1/workflow/jobs/{jid}/goals",
        {
            "goal_ids": ["gospd01030"],
            "period_end": "2025-12-31",
            "entity_name": "回归测试主体",
        },
    )
    assert "gospd01030" in (job.get("goal_ids") or [])
    ok(f"job={jid} steps={job['plan']['required_steps']}")

    step("P2 upload → light classify (no OCR)")
    # 用合同/订单/发票三份足够覆盖轻量分类与后续
    sample = [
        next(p for p in pdfs if "合同" in p.name),
        next(p for p in pdfs if "订单" in p.name),
        next(p for p in pdfs if "发票" in p.name),
    ]
    job = upload_files(jid, sample)
    pending = job.get("pending_files") or []
    assert len(pending) >= 3, pending
    types = {p.get("file_name"): p.get("doc_type") for p in pending}
    assert any(t == "contract" for t in types.values()), types
    assert any(t == "order" for t in types.values()), types
    assert any(t == "invoice" for t in types.values()), types
    ok(f"pending={len(pending)} types={sorted(set(types.values()))}")
    assert not (job.get("field_plan") or {}).get("confirmed")
    ok("field_plan not confirmed yet")

    step("P2 process without confirm → 400")
    call_expect_status("POST", f"/api/v1/workflow/jobs/{jid}/process", expect=400)
    ok("process blocked until field plan confirmed")

    step("P2 confirm field plan then process")
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
    assert (job.get("field_plan") or {}).get("confirmed") is True
    ok("field_plan confirmed")
    job = call("POST", f"/api/v1/workflow/jobs/{jid}/process", timeout=600)
    classified = job.get("classified") or []
    assert len(classified) >= 3, len(classified)
    assert not (job.get("pending_files") or [])
    assert job.get("ocr_processing") is False
    ok(f"OCR done docs={len(classified)} sources={[c.get('ocr_source') for c in classified]}")

    # 挑订单做高亮/取证
    order = next((c for c in classified if c.get("doc_type") == "order"), classified[0])
    fn = order["file_name"]
    enc = urllib.parse.quote(fn)
    base_doc = f"/api/v1/workflow/jobs/{jid}/documents/{enc}"

    step("P0 highlight variants")
    fields = order.get("fields") or {}
    amt = fields.get("totalAmount") or fields.get("amount") or "10942.9"
    data, headers = get_bytes(
        f"{base_doc}/highlight?field=totalAmount&value={urllib.parse.quote(str(amt))}"
    )
    assert len(data) > 500, len(data)
    assert "image" in (headers.get("content-type") or "").lower()
    ok(f"highlight png bytes={len(data)}")

    step("P4 preview-page / text-blocks / capture-text")
    data, headers = get_bytes(f"{base_doc}/preview-page?page=0")
    assert len(data) > 500
    assert "image/png" in (headers.get("content-type") or "").lower()
    ok(f"preview-page png={len(data)} pages={headers.get('x-page-count')}")
    blocks = call("GET", f"{base_doc}/text-blocks?page=0")
    n_blocks = len(blocks.get("blocks") or [])
    ok(f"text-blocks={n_blocks}")
    cap = call(
        "POST",
        f"{base_doc}/capture-text",
        {
            "page_index": 0,
            "x0": 0.05,
            "y0": 0.05,
            "x1": 0.95,
            "y1": 0.95,
            "field": "totalAmount",
        },
    )
    assert cap.get("text"), cap
    ok(f"capture source={cap.get('source')} text_len={len(cap.get('text') or '')}")

    step("HITL fields confirm + evidence + gate4")
    job = call("POST", f"/api/v1/workflow/jobs/{jid}/hitl/fields/confirm")
    assert job.get("fields_confirmed")
    ok("fields confirmed")
    # 锁定业务链，避免分笔 Gate4 写到错误 active
    chains = call("GET", f"/api/v1/workflow/jobs/{jid}/chains")
    chain_list = chains.get("chains") or []
    assert chain_list, "应识别出业务链"
    primary = next(
        (c for c in chain_list if str(c.get("chain_id") or "").upper().startswith("SO")),
        chain_list[0],
    )
    primary_id = primary["chain_id"]
    job = call("PUT", f"/api/v1/workflow/jobs/{jid}/active-chain", {"chain_id": primary_id})
    ok(f"active_chain={job.get('active_chain_id')}")
    job = call("POST", f"/api/v1/workflow/jobs/{jid}/evidence-match")
    ok(f"evidence={(job.get('evidence') or {}).get('status')} rel={len(job.get('relations') or [])}")
    for r in job.get("relations") or []:
        if r.get("status") == "PROPOSED":
            call(
                "POST",
                f"/api/v1/workflow/jobs/{jid}/relations/{urllib.parse.quote(r['relation_id'])}/decide",
                {"status": "VERIFIED", "reason": "e2e"},
            )
    # 重复票号若挡 Gate4：知悉放行
    dup = job.get("duplicates") or {}
    if dup.get("blocks_downstream_hint"):
        try:
            job = call(
                "POST",
                f"/api/v1/workflow/jobs/{jid}/duplicates/acknowledge",
                {"reason": "e2e 知悉放行"},
            )
            ok("duplicates acknowledge")
        except Exception as exc:  # noqa: BLE001
            print(f"  WARN duplicates acknowledge: {exc}")
    job = call("POST", f"/api/v1/workflow/jobs/{jid}/hitl/matching/confirm", {"reason": "e2e"})
    assert job.get("matching_confirmed")
    sample = ((job.get("gospd_sample_results") or {}).get(primary_id) or {})
    assert sample.get("matching_confirmed"), f"分笔 Gate4 未写入 {primary_id}: {sample.keys()}"
    ok(f"gate4 ok chain={primary_id}")

    step("run tests required by gospd01030")
    for path, key in (
        ("three-way-cutoff", "three_way"),
        ("contract-terms", "contract_terms"),
        ("amount-test", "amount_test"),
    ):
        try:
            job = call("POST", f"/api/v1/workflow/jobs/{jid}/{path}", {})
            ok(
                f"{path} status="
                f"{(job.get(key) or {}).get('status') or (job.get(key) or {}).get('overall_status') or 'ran'}"
            )
        except Exception as exc:  # noqa: BLE001
            # 01030 计划外步骤预期 400
            print(f"  WARN {path}: {exc}")

    # 三单后 Gate4 分笔标志仍在
    sample = ((job.get("gospd_sample_results") or {}).get(primary_id) or {})
    assert sample.get("matching_confirmed"), "跑测后分笔 Gate4 被清掉了"
    assert sample.get("three_way"), "01030 应写入 three_way 到分笔"
    ok("sample still gate4 + three_way after tests")

    step("P3 Gate5 workbook row preview + edit")
    preview = call("GET", f"/api/v1/workflow/jobs/{jid}/workbook-rows/preview")
    if not preview.get("supported"):
        print(f"  WARN workbook preview unsupported: {preview.get('message')}")
    else:
        rows = preview.get("rows") or []
        ok(f"preview rows={len(rows)} format={preview.get('format')}")
        if rows:
            cid = rows[0]["chain_id"]
            job = call(
                "PUT",
                f"/api/v1/workflow/jobs/{jid}/workbook-rows/edits",
                {
                    "format": "gospd01030",
                    "chain_id": cid,
                    "edits": {
                        "all_ok": "YES 是",
                        "exception": "P0-P4回归人工确认无异常",
                    },
                },
            )
            assert job.get("conclusion_confirmed") is False
            edits = ((job.get("workbook_row_edits") or {}).get("gospd01030") or {}).get(cid) or {}
            assert edits.get("all_ok") == "YES 是"
            ok(f"row edit saved chain={cid}")
            # 公式列应被拒
            call_expect_status(
                "PUT",
                f"/api/v1/workflow/jobs/{jid}/workbook-rows/edits",
                {
                    "format": "gospd01030",
                    "chain_id": cid,
                    "edits": {"period_ok": "YES 是"},
                },
                expect=400,
            )
            ok("formula column edit rejected")

    # 顾问候选若阻塞：拒绝即可（勿 auto_replay，否则会脏掉已确认的 Gate4）
    try:
        adv = call("GET", f"/api/v1/workflow/jobs/{jid}/advisory")
        for p in adv.get("pending") or []:
            cid = p.get("candidate_id")
            if cid:
                call(
                    "POST",
                    f"/api/v1/workflow/jobs/{jid}/advisory/{urllib.parse.quote(cid)}/decide",
                    {"status": "REJECTED", "reason": "e2e", "auto_replay": False},
                )
        ok("advisory cleared or empty")
    except Exception as exc:  # noqa: BLE001
        print(f"  WARN advisory: {exc}")

    # 确认 Gate5 前再核分笔 Gate4（若被副作用清掉则重确认）
    job = call("GET", f"/api/v1/workflow/jobs/{jid}")
    sample = ((job.get("gospd_sample_results") or {}).get(primary_id) or {})
    if not sample.get("matching_confirmed"):
        job = call(
            "POST",
            f"/api/v1/workflow/jobs/{jid}/hitl/matching/confirm",
            {"reason": "e2e re-gate4"},
        )
        sample = ((job.get("gospd_sample_results") or {}).get(primary_id) or {})
        assert sample.get("matching_confirmed"), sample
        ok("gate4 re-confirmed before gate5")
    else:
        ok("gate4 still on sample before gate5")

    # 方案 A：阻塞性不通过须「确认为单据问题」后才能 Gate5
    step("Gate5 conclusion-trace acknowledge")
    trace = call("GET", f"/api/v1/workflow/jobs/{jid}/conclusion-trace")
    unacked = int(trace.get("unacked_blocking_count") or 0)
    ok(f"trace blocking={trace.get('blocking_count')} unacked={unacked}")
    for f in trace.get("findings") or []:
        if not f.get("blocking") or f.get("acknowledged"):
            continue
        call(
            "POST",
            f"/api/v1/workflow/jobs/{jid}/hitl/finding/acknowledge",
            {
                "finding_id": f["finding_id"],
                "genuine": True,
                "reason": "e2e 确认为单据问题",
            },
        )
    trace2 = call("GET", f"/api/v1/workflow/jobs/{jid}/conclusion-trace")
    assert int(trace2.get("unacked_blocking_count") or 0) == 0, trace2
    ok("all blocking findings acknowledged")

    job = call(
        "POST",
        f"/api/v1/workflow/jobs/{jid}/hitl/conclusion/confirm",
        {"reason": "P0-P4回归"},
    )
    assert job.get("conclusion_confirmed") is True
    ok(f"gate5 confirmed={job.get('conclusion_confirmed')}")

    job = call("POST", f"/api/v1/workflow/jobs/{jid}/workbook/export")
    assert job.get("workbook_path"), job
    ok(f"export path={job.get('workbook_path')}")

    step("frontend source presence (P1/P4)")
    web_checks = [
        ROOT / "web" / "src" / "components" / "CapturePreview.tsx",
        ROOT / "web" / "src" / "components" / "FieldPlanPanel.tsx",
        ROOT / "web" / "src" / "components" / "WorkbookConclusionEditor.tsx",
        ROOT / "web" / "src" / "App.tsx",
    ]
    for p in web_checks:
        text = p.read_text(encoding="utf-8")
        assert len(text) > 200, p
        ok(f"ui file {p.name} bytes={len(text.encode('utf-8'))}")
    app = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "runOcrProcess" in app and "ocr-banner" in app
    ok("P0 App ocr banner / global process present")
    assert "railCollapsed" in app
    ok("P1 rail collapse present")

    print("\nALL CRITICAL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"\nFAIL: {exc}")
        raise

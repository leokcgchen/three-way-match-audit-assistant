"""工作台 API 端到端冒烟。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


def call(method: str, path: str, data=None):
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:8000" + path, data=body, method=method
    )
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.load(r)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} -> {exc.code}: {detail}") from exc


def main() -> None:
    job = call("POST", "/api/v1/workflow/jobs", {"title": "e2e"})
    jid = job["job_id"]
    job = call(
        "PUT",
        f"/api/v1/workflow/jobs/{jid}/goals",
        {"goal_ids": ["gospd01010"]},
    )
    print("steps", job["plan"]["required_steps"])
    raise RuntimeError("演示注入入口已移除；请改用 scripts/accept_gospd01030_ocr_e2e.py 的真实文件上传流程。")
    print("docs", len(job["classified"]))
    # 高亮 / 原件链路
    import urllib.parse
    import urllib.request

    doc0 = (job.get("classified") or [{}])[0]
    fn = doc0.get("file_name") or ""
    if fn and doc0.get("path"):
        base = f"/api/v1/workflow/jobs/{jid}/documents/{urllib.parse.quote(fn)}"
        for suffix in (
            "/file",
            f"/highlight?field={urllib.parse.quote('documentNo')}",
            f"/highlight?field={urllib.parse.quote('documentNo')}&value={urllib.parse.quote('SO25-0281')}",
        ):
            req = urllib.request.Request("http://127.0.0.1:8000" + base + suffix, method="GET")
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
                ctype = r.headers.get("Content-Type", "")
                print("preview", suffix.split("?")[0], r.status, len(data), ctype.split(";")[0])
                if len(data) < 100:
                    raise RuntimeError(f"preview too small: {suffix}")
    job = call("POST", f"/api/v1/workflow/jobs/{jid}/hitl/fields/confirm")
    print("fields", job["fields_confirmed"])
    job = call("POST", f"/api/v1/workflow/jobs/{jid}/evidence-match")
    print(
        "evidence",
        (job.get("evidence") or {}).get("status"),
        "rel",
        len(job.get("relations") or []),
    )
    for r in job.get("relations") or []:
        if r.get("status") == "PROPOSED":
            call(
                "POST",
                f"/api/v1/workflow/jobs/{jid}/relations/{r['relation_id']}/decide",
                {"status": "VERIFIED", "reason": "e2e"},
            )
    job = call("POST", f"/api/v1/workflow/jobs/{jid}/hitl/matching/confirm")
    print("gate4", job["matching_confirmed"])
    try:
        job = call("POST", f"/api/v1/workflow/jobs/{jid}/amount-test")
        print("amount", (job.get("amount_test") or {}).get("status"))
    except Exception as exc:  # noqa: BLE001
        print("amount_err", exc)
    try:
        job = call("POST", f"/api/v1/workflow/jobs/{jid}/three-way-cutoff", {})
        print("three", bool(job.get("three_way")))
    except Exception as exc:  # noqa: BLE001
        print("three_err", exc)
    job = call("POST", f"/api/v1/workflow/jobs/{jid}/hitl/conclusion/confirm")
    print("gate5", job["conclusion_confirmed"])
    try:
        job = call("POST", f"/api/v1/workflow/jobs/{jid}/workbook/export")
        print("workbook", job.get("workbook_path"))
    except Exception as exc:  # noqa: BLE001
        print("wb_err", exc)
    cat = call("GET", "/api/v1/workflow/prompts/catalog")
    print("prompts", len(cat.get("entries") or []))
    print("ocr", call("GET", "/api/v1/workflow/ocr-status"))
    print("OK")


if __name__ == "__main__":
    main()

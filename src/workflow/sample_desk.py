"""工作台样本笔灯：清单立笔 + 缺字段/多金额红 + 测试失败红。"""

from __future__ import annotations

from typing import Any

from src.audit.sample_population import chain_in_population, desk_sample_ids
from src.workflow.amount_ambiguity import list_open_ambiguities
from src.workflow.chain_workspace import (
    all_chains_conclusion_confirmed,
    docs_for_chain,
    get_sample,
    is_gospd_mode,
    list_business_chains,
    merge_sample,
    resolve_active_chain_id,
    sample_matching_ok,
    sample_test_complete,
)
from src.workflow.required_docs import (
    has_unresolved_units,
    missing_request_lines,
    missing_required_docs,
    present_doc_labels,
    slot_completeness_matrix,
)
from src.workflow.recipes import STEP_RELATIONS
from src.workflow.sample_required_fields import missing_required_fields, required_fields_for_docs
from src.workflow.signatures import conclusion_signature, fields_signature
from src.workflow.test_diff_summary import sample_diff_lines


def _status_fail(blob: Any) -> bool:
    if not isinstance(blob, dict):
        return False
    text = str(blob.get("overall_status") or blob.get("status") or blob.get("cutoff_test_status") or "").upper()
    return "FAIL" in text or "未通过" in text or "ERROR" in text


def sample_tests_failed(sample: dict[str, Any]) -> bool:
    return any(
        _status_fail(sample.get(k))
        for k in ("three_way", "three_way_match", "cutoff_test", "evidence", "amount_test")
    )


def _goal_ids(job: dict[str, Any]) -> list[str]:
    return list((job.get("plan") or {}).get("goal_ids") or job.get("goal_ids") or [])


def _required_steps(job: dict[str, Any]) -> set[str]:
    return set((job.get("plan") or {}).get("required_steps") or [])


def _doc_inventory(job: dict[str, Any], docs: list[dict[str, Any]], chain_id: str = "") -> dict[str, Any]:
    present = present_doc_labels(docs)
    missing_docs = missing_required_docs(docs, job)
    matrix = slot_completeness_matrix(docs, job)
    uncertain = [str(s["label"]) for s in matrix if s.get("status") == "uncertain"]
    return {
        "present_labels": present,
        "missing_doc_labels": missing_docs,
        "uncertain_doc_labels": uncertain,
        "doc_slots": matrix,
        "request_docs": missing_request_lines(chain_id, matrix) if chain_id else [],
        "doc_count": len(docs),
    }


def desk_row_status(job: dict[str, Any], chain_id: str) -> dict[str, Any]:
    docs = docs_for_chain(list(job.get("classified") or []), chain_id)
    goals = _goal_ids(job)
    sample = get_sample(job, chain_id)
    required = required_fields_for_docs(docs, goal_ids=goals) if docs else []
    missing = [r["key"] for r in required if not r["filled"]] if docs else []
    missing_labels = [r["label"] for r in required if not r["filled"]] if docs else []
    open_amb = list_open_ambiguities(job, chain_id=chain_id) if docs else []
    inv = _doc_inventory(job, docs, chain_id)
    diffs = sample_diff_lines(sample)

    if not docs:
        return {
            "light": "wait",
            "reason": "wait_docs",
            "label": "待上传凭证",
            "missing_fields": [],
            "missing_labels": [],
            "required_fields": [],
            "diff_lines": [],
            **inv,
        }
    if open_amb:
        extra = f"；还缺：{'、'.join(missing_labels)}" if missing_labels else ""
        return {
            "light": "red",
            "reason": "amount_ambiguity",
            "label": f"多金额待确认{extra}",
            "missing_fields": missing,
            "missing_labels": missing_labels,
            "required_fields": required,
            "diff_lines": [],
            **inv,
        }
    if inv.get("missing_doc_labels"):
        miss = "、".join(inv["missing_doc_labels"])
        return {
            "light": "red",
            "reason": "missing_docs",
            "label": f"缺单据：{miss}",
            "missing_fields": missing,
            "missing_labels": missing_labels,
            "required_fields": required,
            "diff_lines": [],
            **inv,
        }
    if inv.get("uncertain_doc_labels") or has_unresolved_units(docs):
        unc = "、".join(inv.get("uncertain_doc_labels") or []) or "未识别单元"
        return {
            "light": "yellow",
            "reason": "docs_uncertain",
            "label": f"单据类型存疑：{unc}",
            "missing_fields": missing,
            "missing_labels": missing_labels,
            "required_fields": required,
            "diff_lines": [],
            **inv,
        }
    if missing:
        return {
            "light": "red",
            "reason": "fields_gap",
            "label": f"缺：{'、'.join(missing_labels)}",
            "missing_fields": missing,
            "missing_labels": missing_labels,
            "required_fields": required,
            "diff_lines": [],
            **inv,
        }
    if not sample_test_complete(sample, job):
        return {
            "light": "wait",
            "reason": "tests_pending",
            "label": "测试进行中",
            "missing_fields": [],
            "missing_labels": [],
            "required_fields": required,
            "diff_lines": [],
            **inv,
        }
    if sample_tests_failed(sample):
        summary = diffs[0] if diffs else "测试未通过"
        if sample.get("conclusion_confirmed"):
            if str(sample.get("conclusion_disposition") or "") == "fail":
                return {
                    "light": "red",
                    "reason": "fail_closed",
                    "label": f"测试未通过 · 已人工确认 · {summary}",
                    "missing_fields": [],
                    "missing_labels": [],
                    "required_fields": required,
                    "diff_lines": diffs,
                    **inv,
                }
            return {
                "light": "green",
                "reason": "ok",
                "label": "已通过（单据问题）",
                "missing_fields": [],
                "missing_labels": [],
                "required_fields": required,
                "diff_lines": diffs,
                **inv,
            }
        return {
            "light": "red",
            "reason": "test_fail",
            "label": f"测试未通过 · {summary}",
            "missing_fields": [],
            "missing_labels": [],
            "required_fields": required,
            "diff_lines": diffs,
            **inv,
        }
    return {
        "light": "green",
        "reason": "ok",
        "label": "已通过",
        "missing_fields": [],
        "missing_labels": [],
        "required_fields": required,
        "diff_lines": [],
        **inv,
    }


# 与老师口径一致：绿可继续、黄需人裁、红缺件/冲突、灰待办
LIGHT_LEGEND = {
    "green": "单据已识别、必要字段齐、规则无异常 → 可继续 / 已通过",
    "yellow": "分类存疑、模糊匹配或需专业判断 → 由审计师裁决",
    "red": "缺单据、关键字段缺失、无法归属同一笔，或规则明确冲突 → 须处理",
    "wait": "待上传凭证或测试进行中 → 尚未出灯",
}


def desk_progress_breakdown(rows: list[dict[str, Any]]) -> dict[str, int]:
    """全局进度拆分：样本笔互斥归类。

    「已完成」含绿灯通过 + 红灯但已人工确认收口（fail_closed）。
    仍亮红的异常笔用 fail_confirmed 单独计数，避免和「待办」混淆。
    """
    out = {
        "sample_total": len(rows),
        "done": 0,
        "docs_missing": 0,
        "fields_missing": 0,
        "match_exception": 0,
        "fail_confirmed": 0,
        "await_human": 0,
        "in_progress": 0,
    }
    for row in rows:
        reason = str(row.get("reason") or "")
        light = str(row.get("light") or "wait")
        if light == "green" or reason in {"ok"}:
            out["done"] += 1
        elif reason == "fail_closed":
            # 人已确认「确认为不通过」→ 工作流收口，计入已完成
            out["done"] += 1
            out["fail_confirmed"] += 1
        elif reason in {"wait_docs", "missing_docs"}:
            out["docs_missing"] += 1
        elif reason in {"fields_gap"}:
            out["fields_missing"] += 1
        elif reason in {"docs_uncertain", "amount_ambiguity"}:
            out["await_human"] += 1
        elif reason in {"test_fail"}:
            out["match_exception"] += 1
        elif reason in {"tests_pending"} or light == "wait":
            out["in_progress"] += 1
        elif light == "yellow":
            out["await_human"] += 1
        elif light == "red":
            out["match_exception"] += 1
        else:
            out["in_progress"] += 1
    return out


def desk_light_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"green": 0, "yellow": 0, "red": 0, "wait": 0}
    issues: list[str] = []
    request_docs: list[str] = []
    for row in rows:
        light = str(row.get("light") or "wait")
        if light not in counts:
            light = "wait"
        counts[light] += 1
        if light in {"red", "yellow"}:
            cid = str(row.get("chain_id") or "")
            label = str(row.get("label") or row.get("reason") or "")
            if cid and label:
                issues.append(f"{cid}：{label}")
        for line in row.get("request_docs") or []:
            if line not in request_docs:
                request_docs.append(str(line))
    return {
        **counts,
        "issues": issues[:20],
        "request_docs": request_docs,
        "progress": desk_progress_breakdown(rows),
        "legend": dict(LIGHT_LEGEND),
    }


def apply_auto_pass_on_job(job: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """字段齐且无多金额：自动记本笔 fields_confirmed。不写勾稽（交给一键审阅）。"""
    from src.models.field_values import accept_all_current_fields

    if not is_gospd_mode(job):
        return job, []
    classified = list(job.get("classified") or [])
    goals = _goal_ids(job)
    samples = dict(job.get("gospd_sample_results") or {})
    passed: list[str] = []
    changed_docs = False
    for cid in desk_sample_ids(job):
        docs = docs_for_chain(classified, cid)
        if not docs:
            continue
        if list_open_ambiguities(job, chain_id=cid):
            continue
        if missing_required_docs(docs, job):
            continue
        if has_unresolved_units(docs):
            continue
        if missing_required_fields(docs, goal_ids=goals):
            continue
        if get_sample({"gospd_sample_results": samples}, cid).get("fields_confirmed"):
            passed.append(cid)
            continue
        touch = {str(d.get("file_name") or "") for d in docs}
        for item in classified:
            if not isinstance(item, dict):
                continue
            if str(item.get("file_name") or "") in touch:
                accept_all_current_fields(item, source="auto_fields_ok")
                changed_docs = True
        sig = fields_signature(docs)
        samples = merge_sample(
            samples,
            chain_id=cid,
            patch={
                "fields_confirmed": True,
                "fields_confirm_sig": sig,
                "matching_confirmed": True,
            },
        )
        passed.append(cid)
    out = dict(job)
    out["gospd_sample_results"] = samples
    if changed_docs:
        out["classified"] = classified
    active = resolve_active_chain_id(out) or ""
    if active and active in passed:
        cur = get_sample(out, active)
        out["fields_confirmed"] = True
        out["fields_confirm_sig"] = cur.get("fields_confirm_sig")
        out["matching_confirmed"] = True
    return out, passed


def apply_auto_conclusions_on_job(job: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """必测已齐且无 FAIL：自动记本笔 conclusion_confirmed。FAIL 保持红灯等人。"""
    if not is_gospd_mode(job):
        return job, []
    samples = dict(job.get("gospd_sample_results") or {})
    passed: list[str] = []
    need_match = STEP_RELATIONS in _required_steps(job)
    for cid in desk_sample_ids(job):
        docs = docs_for_chain(list(job.get("classified") or []), cid)
        if not docs:
            continue
        probe = {**job, "gospd_sample_results": samples}
        sample = get_sample(probe, cid)
        if sample.get("conclusion_confirmed"):
            passed.append(cid)
            continue
        if not sample.get("fields_confirmed"):
            continue
        if need_match and not sample_matching_ok(probe, cid):
            samples = merge_sample(samples, chain_id=cid, patch={"matching_confirmed": True})
            probe = {**job, "gospd_sample_results": samples}
            sample = get_sample(probe, cid)
        if not sample_test_complete(sample, job):
            continue
        if sample_tests_failed(sample):
            continue
        sig = conclusion_signature(
            evidence=sample.get("evidence") if isinstance(sample.get("evidence"), dict) else None,
            amount=sample.get("amount_test") if isinstance(sample.get("amount_test"), dict) else None,
            contract=sample.get("contract_terms") if isinstance(sample.get("contract_terms"), dict) else None,
            three_way=sample.get("three_way") if isinstance(sample.get("three_way"), dict) else None,
        )
        samples = merge_sample(
            samples,
            chain_id=cid,
            patch={"conclusion_confirmed": True, "conclusion_confirm_sig": sig},
        )
        passed.append(cid)
    out = dict(job)
    out["gospd_sample_results"] = samples
    out["conclusion_confirmed"] = all_chains_conclusion_confirmed(out)
    return out, passed


def persist_auto_job(job_id: str, job: dict[str, Any]) -> dict[str, Any]:
    from src.workflow.job_store import JOB_STORE

    patch: dict[str, Any] = {
        "gospd_sample_results": job.get("gospd_sample_results") or {},
        "conclusion_confirmed": bool(job.get("conclusion_confirmed")),
    }
    if "classified" in job:
        patch["classified"] = job["classified"]
    if job.get("fields_confirmed"):
        patch["fields_confirmed"] = True
        patch["fields_confirm_sig"] = job.get("fields_confirm_sig")
    if job.get("matching_confirmed"):
        patch["matching_confirmed"] = True
    return JOB_STORE.update(job_id, **patch)


def auto_confirm_passing_conclusions(job_id: str) -> dict[str, Any]:
    from src.workflow.job_store import JOB_STORE

    job = JOB_STORE.get(job_id) or {}
    nxt, _ids = apply_auto_conclusions_on_job(job)
    return persist_auto_job(job_id, nxt)


def replay_after_sample_replace(job_id: str) -> dict[str, Any]:
    """换裁剪序时账后：OCR 单据保留，按新账重绑并重跑识别之后的逻辑。"""
    from src.workflow.job_store import JOB_STORE
    from src.workflow.pipeline import apply_ledger_to_classified_list

    job = JOB_STORE.get(job_id) or {}
    classified = list(job.get("classified") or [])
    if classified and job.get("ledger_rows") and job.get("ledger_mapping"):
        try:
            classified = apply_ledger_to_classified_list(
                classified,
                list(job.get("ledger_rows") or []),
                dict(job.get("ledger_mapping") or {}),
            )
        except Exception:
            pass
        JOB_STORE.update(job_id, classified=classified)
    JOB_STORE.reset_downstream_keep_ocr(job_id)
    if classified:
        return finish_after_classify(job_id)
    return JOB_STORE.get(job_id) or job


def finish_after_classify(job_id: str) -> dict[str, Any]:
    """识别落库后：齐且无多金额的笔自动确认字段 → 复用一键审阅 → 全过自动结论。"""
    from src.workflow.batch_review import run_batch_review
    from src.workflow.job_store import JOB_STORE

    job = JOB_STORE.get(job_id) or {}
    if not is_gospd_mode(job):
        return job
    try:
        from src.workflow.amount_ambiguity import enrich_job_ambiguities, scan_job_documents

        scan_job_documents(job)
        enrich_job_ambiguities(job)
        JOB_STORE.update(job_id, classified=list(job.get("classified") or []))
        job = JOB_STORE.get(job_id) or job
    except Exception:
        pass
    nxt, passed = apply_auto_pass_on_job(job)
    persist_auto_job(job_id, nxt)
    if passed:
        try:
            run_batch_review(job_id)
        except Exception:
            pass
    return auto_confirm_passing_conclusions(job_id)


def build_desk_chains(job: dict[str, Any]) -> list[dict[str, Any]]:
    classified = list_business_chains(list(job.get("classified") or []))
    by_id = {c["chain_id"]: c for c in classified}
    pop = job.get("sample_population")
    rows: list[dict[str, Any]] = []
    for cid in desk_sample_ids(job):
        base = dict(by_id.get(cid) or {"chain_id": cid, "doc_count": 0, "doc_types": [], "file_names": []})
        st = desk_row_status(job, cid)
        rows.append(
            {
                **base,
                **st,
                "in_sample_population": chain_in_population(cid, pop if isinstance(pop, dict) else None),
            }
        )
    return rows


def build_desk_overview(job: dict[str, Any]) -> dict[str, Any]:
    rows = build_desk_chains(job)
    return {"chains": rows, "lights": desk_light_summary(rows)}

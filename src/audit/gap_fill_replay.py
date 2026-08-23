"""顾问候选人工决议后的定向失效与可安全复跑。

VERIFIED：按 invalidates 脏相关结果；在门禁仍满足时自动复跑测项。
REJECTED：只改候选状态，不脏规则结果（主张从未进正式层）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from src.audit.hitl_log import append_hitl_event
from src.evidence_match.disambiguation import apply_disambiguation_proposal
from src.models.advisory_candidates import (
    decide_candidate,
    default_invalidates_for_task,
    invalidation_targets_for,
)
from src.models.field_values import set_candidate
from src.workflow.chain_workspace import (
    docs_for_chain,
    is_gospd_mode,
    resolve_active_chain_id,
)
from src.workflow.job_store import JOB_STORE
from src.workflow.pipeline import run_amount, run_contract, run_evidence, run_three_way
from src.workflow.three_way_persist import three_way_sample_patch


def _uniq(items: Sequence[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _active_docs(job: Dict[str, Any]) -> tuple[List[Dict[str, Any]], Optional[str]]:
    classified = list(job.get("classified") or [])
    if not is_gospd_mode(job):
        return classified, None
    cid = resolve_active_chain_id(job)
    if not cid:
        return classified, None
    docs = docs_for_chain(classified, cid)
    return (docs or classified), cid


def _plan_steps(job: Dict[str, Any]) -> set[str]:
    return set((job.get("plan") or {}).get("required_steps") or [])


def _apply_verified_side_effects(
    job: Dict[str, Any],
    candidate: Dict[str, Any],
) -> Dict[str, Any]:
    """VERIFIED 时把可落地的 payload 写入工作副本（不写规则终态）。"""
    task = str(candidate.get("task_type") or "").upper()
    payload = candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
    classified = list(job.get("classified") or [])

    if task == "MATCHING_DISAMBIGUATION" and payload:
        classified = apply_disambiguation_proposal(classified, payload)
        job["classified"] = classified
        return {"applied": "matching_proposal", "file_name": payload.get("file_name")}

    if task in {"FIELD_GAP_FILL", "AMOUNT_GAP_FILL", "CUTOFF_SEMANTIC_EXTRACTION"}:
        field_name = str(payload.get("field_name") or payload.get("key") or "").strip()
        value = payload.get("normalized_candidate", payload.get("value"))
        source_doc = str(
            (candidate.get("evidence") or {}).get("source_doc")
            or payload.get("file_name")
            or ""
        )
        if field_name and value is not None:
            touched = False
            for item in classified:
                fname = str(item.get("file_name") or "")
                if source_doc and fname != source_doc:
                    continue
                set_candidate(
                    item,
                    field_name,
                    value,
                    source="llm_advisory",
                    extractor=task,
                )
                touched = True
                if source_doc:
                    break
            if touched:
                job["classified"] = classified
                return {"applied": "field_candidate", "field_name": field_name}
    return {"applied": None}


def _can_run_tests(job: Dict[str, Any], plan: set[str]) -> bool:
    if not job.get("fields_confirmed"):
        return False
    if "evidence_match" in plan and not job.get("matching_confirmed"):
        return False
    return True


def _replay_dirty(
    job_id: str,
    targets: Sequence[str],
) -> Dict[str, Any]:
    expanded = {str(t).lower() for t in targets}
    replayed: List[str] = []
    skipped: List[str] = []

    job = JOB_STORE.get(job_id) or {}
    plan = _plan_steps(job)
    docs, chain_id = _active_docs(job)

    if "evidence" in expanded:
        if not (job.get("fields_confirmed") or job.get("classified")):
            skipped.append("evidence:not_ready")
        else:
            try:
                evidence = run_evidence(
                    docs,
                    existing_advisory=list(job.get("advisory_candidates") or []),
                    with_llm_disambiguation=False,  # 复跑跳过 LLM，避免卡顿与重复顾问行
                )
                patch: Dict[str, Any] = {"evidence": evidence}
                if "advisory_candidates" in evidence:
                    patch["advisory_candidates"] = evidence["advisory_candidates"]
                if chain_id and is_gospd_mode(job):
                    JOB_STORE.save_chain_sample(
                        job_id,
                        chain_id,
                        {
                            "evidence": evidence,
                            "matching_confirmed": False,
                            "matching_confirm_sig": None,
                        },
                    )
                JOB_STORE.update(job_id, **patch)
                replayed.append("evidence")
            except Exception as exc:  # noqa: BLE001
                skipped.append(f"evidence:{exc}")

    job = JOB_STORE.get(job_id) or {}
    docs, chain_id = _active_docs(job)
    plan = _plan_steps(job)
    can_tests = _can_run_tests(job, plan)
    test_patch: Dict[str, Any] = {}

    if "amount" in expanded:
        if can_tests and ("amount_test" in plan or not plan):
            try:
                test_patch["amount_test"] = run_amount(docs)
                replayed.append("amount")
            except Exception as exc:  # noqa: BLE001
                skipped.append(f"amount:{exc}")
        else:
            skipped.append("amount:gate_blocked")

    if "terms" in expanded:
        if can_tests and ("contract_terms" in plan or not plan):
            try:
                test_patch["contract_terms"] = run_contract(docs)
                replayed.append("terms")
            except Exception as exc:  # noqa: BLE001
                skipped.append(f"terms:{exc}")
        else:
            skipped.append("terms:gate_blocked")

    if expanded & {"cutoff", "three_way"}:
        if can_tests and ("three_way_cutoff" in plan or not plan):
            try:
                tw = run_three_way(docs)
                test_patch.update(three_way_sample_patch(tw))
                replayed.append("three_way")
            except Exception as exc:  # noqa: BLE001
                skipped.append(f"three_way:{exc}")
        else:
            skipped.append("three_way:gate_blocked")

    if test_patch:
        if chain_id and is_gospd_mode(job):
            # save_chain_sample 会 mirror 整笔；须带上未脏的 Gate4/证据，避免被默认 False 冲掉
            sample_patch = {
                "evidence": job.get("evidence"),
                "relations": list(job.get("relations") or []),
                "duplicates": dict(job.get("duplicates") or {}),
                "matching_confirmed": bool(job.get("matching_confirmed")),
                "matching_confirm_sig": job.get("matching_confirm_sig"),
                "amount_test": job.get("amount_test"),
                "contract_terms": job.get("contract_terms"),
                "three_way": job.get("three_way"),
                "three_way_match": job.get("three_way_match"),
                "cutoff_test": job.get("cutoff_test"),
            }
            sample_patch.update(test_patch)
            JOB_STORE.save_chain_sample(job_id, chain_id, sample_patch)
        JOB_STORE.update(job_id, **test_patch)

    return {
        "replayed": _uniq(replayed),
        "skipped": _uniq(skipped),
        "job": JOB_STORE.get(job_id),
    }


def apply_advisory_decision(
    job_id: str,
    candidate_id: str,
    status: str,
    *,
    actor: str = "manual",
    reason: str = "",
    auto_replay: bool = True,
) -> Dict[str, Any]:
    """人工决议候选：写回 store →（VERIFIED）落地副作用 → 定向失效 → 可选复跑。"""
    job = JOB_STORE.get(job_id)
    if not job:
        raise KeyError(job_id)

    store = list(job.get("advisory_candidates") or [])
    store, before, after = decide_candidate(
        store,
        candidate_id,
        status,
        actor=actor,
        reason=reason,
    )
    if after is None:
        raise ValueError(f"未找到候选: {candidate_id}")

    status_u = str(status or "").upper()
    side = {"applied": None}
    targets: List[str] = []
    expanded: List[str] = []
    replay_info: Dict[str, Any] = {"replayed": [], "skipped": []}

    JOB_STORE.update(job_id, advisory_candidates=store)
    job = JOB_STORE.get(job_id) or job

    if status_u == "VERIFIED":
        side = _apply_verified_side_effects(job, after)
        # 人工采纳可能改写字段工作副本：刷新确认签名，避免「字段已变化」假阳性死循环
        from src.workflow.signatures import fields_signature

        classified = list(job.get("classified") or [])
        docs, chain_id = _active_docs(job)
        sample = {}
        if chain_id and is_gospd_mode(job):
            sample = dict((job.get("gospd_sample_results") or {}).get(chain_id) or {})
        gate3_ok = bool(job.get("fields_confirmed")) or bool(sample.get("fields_confirmed"))
        # 兼容旧样本：已跑过下游但未写 fields_confirmed 时，视为本笔已过 Gate3
        if not gate3_ok and is_gospd_mode(job) and sample:
            gate3_ok = bool(
                sample.get("matching_confirmed")
                or sample.get("evidence")
                or sample.get("amount_test")
                or sample.get("contract_terms")
                or sample.get("three_way")
            )

        patch_cls: Dict[str, Any] = {
            "classified": classified,
            "advisory_candidates": store,
        }
        if gate3_ok:
            # 顶层签：全量单据；分笔签：当前链（require_fields 分笔校验用）
            patch_cls["fields_confirmed"] = True
            patch_cls["fields_confirm_sig"] = fields_signature(classified)
        JOB_STORE.update(job_id, **patch_cls)
        if gate3_ok and chain_id and is_gospd_mode(job):
            JOB_STORE.save_chain_sample(
                job_id,
                chain_id,
                {
                    "fields_confirmed": True,
                    "fields_confirm_sig": fields_signature(docs or classified),
                },
            )
        job = JOB_STORE.get(job_id) or job

        targets = invalidation_targets_for(after)
        if not targets:
            targets = list(default_invalidates_for_task(str(after.get("task_type") or "")))
        task_u = str(after.get("task_type") or "").upper()
        # Gate3 已确认后再接受字段/匹配类顾问：落地副作用 + 刷新签名即可，勿清字段门禁逼用户重走 Gate3
        if gate3_ok and "fields" in targets:
            soft = [t for t in targets if t != "fields"]
            for extra in ("amount", "cutoff", "terms", "three_way", "gate5", "workbook"):
                if extra not in soft:
                    soft.append(extra)
            targets = soft
        # Gate4 已确认后再接受「匹配消歧」：落地副作用即可，勿清 matching 逼用户重点勾稽
        match_ok = bool(job.get("matching_confirmed")) or bool(
            sample.get("matching_confirmed")
        )
        if (
            match_ok
            and task_u == "MATCHING_DISAMBIGUATION"
            and "evidence" in targets
        ):
            soft = [
                t
                for t in list(targets) + ["amount", "cutoff", "terms", "three_way", "gate5"]
                if t not in {"evidence", "fields"}
            ]
            seen_t: set[str] = set()
            targets = []
            for t in soft:
                if t in seen_t:
                    continue
                seen_t.add(t)
                targets.append(t)
        # 字段补缺已过 Gate4：勿因默认 invalidates 含 evidence 清掉勾稽，只脏金额/条款/三单
        if match_ok and task_u in {"FIELD_GAP_FILL", "AMOUNT_GAP_FILL", "CUTOFF_SEMANTIC_EXTRACTION"}:
            targets = [t for t in targets if t not in {"evidence", "fields"}]
            for extra in ("amount", "cutoff", "terms", "three_way", "gate5", "workbook"):
                if extra not in targets:
                    targets.append(extra)
        if targets:
            expanded = JOB_STORE.invalidate_by_targets(job_id, targets)
            # invalidate 可能误清顶层 fields；Gate3 软路径下恢复分笔确认
            if gate3_ok:
                job = JOB_STORE.get(job_id) or job
                heal: Dict[str, Any] = {
                    "fields_confirmed": True,
                    "fields_confirm_sig": fields_signature(
                        list(job.get("classified") or [])
                    ),
                }
                JOB_STORE.update(job_id, **heal)
                if chain_id and is_gospd_mode(job):
                    docs2, _ = _active_docs(JOB_STORE.get(job_id) or job)
                    JOB_STORE.save_chain_sample(
                        job_id,
                        chain_id,
                        {
                            "fields_confirmed": True,
                            "fields_confirm_sig": fields_signature(
                                docs2 or list((JOB_STORE.get(job_id) or {}).get("classified") or [])
                            ),
                        },
                    )
        if auto_replay and expanded:
            replay_info = _replay_dirty(job_id, expanded)
            job = replay_info.get("job") or JOB_STORE.get(job_id)
        else:
            job = JOB_STORE.get(job_id)

    append_hitl_event(
        action="decide_advisory_candidate",
        entity_type="advisory",
        entity_id=candidate_id,
        before=before,
        after=after,
        reason=reason or status_u,
        extra={
            "job_id": job_id,
            "invalidates": targets,
            "expanded": expanded,
            "side_effects": side,
            "replayed": replay_info.get("replayed") or [],
            "skipped": replay_info.get("skipped") or [],
        },
    )

    return {
        "job": job or JOB_STORE.get(job_id),
        "before": before,
        "after": after,
        "invalidates": targets,
        "expanded_invalidates": expanded,
        "side_effects": side,
        "replayed": replay_info.get("replayed") or [],
        "skipped": replay_info.get("skipped") or [],
    }

"""全笔一键审阅 / 一键串单确认（工作台主路径）。

字段确认仍为人机门禁；本模块只跑「可自动」步骤并汇总结果。
"""

from __future__ import annotations

import copy
from typing import Any, Optional

from src.models.relation_candidates import pending_proposed
from src.workflow.amount_ambiguity import list_open_ambiguities
from src.workflow.chain_workspace import (
    docs_for_chain,
    get_sample,
    is_gospd_mode,
    list_business_chains,
    resolve_active_chain_id,
    sample_matching_ok,
)
from src.workflow.job_store import JOB_STORE
from src.workflow.pipeline import cutoff_calendar_mode, run_amount, run_contract, run_evidence, run_three_way
from src.workflow.recipes import (
    STEP_AMOUNT,
    STEP_CONTRACT,
    STEP_EVIDENCE,
    STEP_RELATIONS,
    STEP_THREE_WAY,
)
from src.workflow.required_docs import missing_required_docs
from src.workflow.three_way_persist import three_way_sample_patch


def _plan_steps(job: dict[str, Any]) -> set[str]:
    return set((job.get("plan") or {}).get("required_steps") or [])


def _strong_chain_ids(job: dict[str, Any]) -> list[str]:
    chains = list_business_chains(list(job.get("classified") or []))
    return [
        str(c["chain_id"])
        for c in chains
        if c.get("chain_id") and str(c["chain_id"]) != "未识别业务号"
    ]


def _sample_fields_ok(job: dict[str, Any], chain_id: str) -> bool:
    """本笔自己确认过字段，且无未决金额、无缺必需单据。禁止借用整单 fields_confirmed。"""
    sample = get_sample(job, chain_id)
    if not sample.get("fields_confirmed"):
        return False
    if list_open_ambiguities(job, chain_id=chain_id):
        return False
    docs = docs_for_chain(list(job.get("classified") or []), chain_id)
    if missing_required_docs(docs, job):
        return False
    return True


def _accept_pending_relations(
    job_id: str,
    chain_id: str,
    *,
    reason: str = "一键串单顺带确认建议关系",
) -> tuple[dict[str, Any], int]:
    """切到该笔后采纳建议关系（委托 job_store，保持分笔镜像一致）。"""
    JOB_STORE.set_active_chain(job_id, chain_id)
    return JOB_STORE.accept_pending_relations(job_id, reason=reason)


def _try_auto_confirm_matching(job_id: str, chain_id: str) -> tuple[dict[str, Any], Optional[str]]:
    """证据已齐时：顺带采纳建议关系，再写入 Gate4。仅强拦（重复票等）失败。"""
    job = JOB_STORE.get(job_id) or {}
    if not chain_id:
        return job, "无业务笔"
    sample = get_sample(job, chain_id)
    evidence = sample.get("evidence") if isinstance(sample.get("evidence"), dict) else None
    if evidence is None and isinstance(job.get("evidence"), dict):
        evidence = job.get("evidence")
    if not evidence:
        return job, "尚未运行匹配"
    duplicates = (
        sample.get("duplicates")
        if isinstance(sample.get("duplicates"), dict)
        else (job.get("duplicates") or {})
    )
    if duplicates.get("blocks_downstream_hint"):
        return job, "存在重复票号风险，需在「本笔作业」处理"
    # 与本笔勾稽主路径一致：待决建议关系一并采纳，不把用户赶出工作台
    job, _accepted = _accept_pending_relations(job_id, chain_id)
    relations = list(job.get("relations") or [])
    pending = pending_proposed(relations)
    if pending:
        return job, f"还有 {len(pending)} 条关系无法自动确认，请在「本笔作业」处理"
    JOB_STORE.set_active_chain(job_id, chain_id)
    try:
        job = JOB_STORE.confirm_matching(job_id)
    except ValueError as exc:
        return JOB_STORE.get(job_id) or job, str(exc)
    return job, None


def run_batch_review(job_id: str, *, force_rerun: bool = False) -> dict[str, Any]:
    """对所有强业务笔：缺啥跑啥（匹配→在可自动时确认勾稽→必测）。"""
    from src.workflow.sample_desk import apply_auto_pass_on_job, persist_auto_job

    job = JOB_STORE.get(job_id)
    if not job:
        raise KeyError(job_id)
    # 字段已齐但未点确认（例如刚消掉误报多金额）时先自动记确认，否则会跳过还显示「测试在自动跑」
    if is_gospd_mode(job):
        nxt, passed = apply_auto_pass_on_job(job)
        if passed:
            persist_auto_job(job_id, nxt)
            job = JOB_STORE.get(job_id) or nxt
    plan = _plan_steps(job)
    need_evidence = (STEP_EVIDENCE in plan) or (STEP_RELATIONS in plan) or not plan
    need_gate4 = STEP_RELATIONS in plan
    need_amount = STEP_AMOUNT in plan
    need_contract = STEP_CONTRACT in plan
    need_three = STEP_THREE_WAY in plan

    chain_ids = _strong_chain_ids(job)
    if not chain_ids:
        # 非 GOSPD / 单批：用当前 active 或整单
        cid = resolve_active_chain_id(job) or "_job"
        chain_ids = [cid]

    ran: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    need_gate4_list: list[str] = []

    for cid in chain_ids:
        job = JOB_STORE.get(job_id) or job
        if cid != "_job" and is_gospd_mode(job):
            try:
                JOB_STORE.set_active_chain(job_id, cid)
            except ValueError as exc:
                failed.append({"chain_id": cid, "step": "switch", "error": str(exc)})
                continue
            job = JOB_STORE.get(job_id) or job

        if cid != "_job" and not _sample_fields_ok(job, cid):
            skipped.append({"chain_id": cid, "reason": "字段未确认"})
            continue
        if cid == "_job" and not job.get("fields_confirmed"):
            skipped.append({"chain_id": cid, "reason": "字段未确认"})
            continue

        sample = get_sample(job, cid) if cid != "_job" else {
            "evidence": job.get("evidence"),
            "amount_test": job.get("amount_test"),
            "contract_terms": job.get("contract_terms"),
            "three_way": job.get("three_way"),
            "matching_confirmed": job.get("matching_confirmed"),
        }
        docs = (
            docs_for_chain(list(job.get("classified") or []), cid)
            if cid != "_job"
            else list(job.get("classified") or [])
        )
        if not docs:
            skipped.append({"chain_id": cid, "reason": "无单据"})
            continue

        actions: list[str] = []

        # 1) 证据匹配（已有结果但仍是旧版 WARNING 时也重算）
        ev_blob = sample.get("evidence") if isinstance(sample.get("evidence"), dict) else {}
        ev_stale_warning = str((ev_blob or {}).get("status") or "").upper() == "WARNING"
        if need_evidence and (force_rerun or not sample.get("evidence") or ev_stale_warning):
            try:
                evidence = run_evidence(
                    docs,
                    existing_advisory=list(job.get("advisory_candidates") or []),
                    with_llm_disambiguation=False,
                    # 无计划的旧任务沿用历史默认（合同属于核心证据）；
                    # 只有显式底稿计划未要求合同时，才把合同降为可选。
                    require_contract=need_contract or not plan,
                    require_ledger=True,
                )
                from src.workflow.pipeline import seed_phase2

                phase2 = seed_phase2(docs, evidence, existing_relations=[])
                patch = {
                    "evidence": evidence,
                    "relations": phase2.get("relations") or [],
                    "duplicates": phase2.get("duplicates") or {},
                    "matching_confirmed": False,
                    "matching_confirm_sig": None,
                }
                if "advisory_candidates" in evidence:
                    JOB_STORE.update(
                        job_id,
                        advisory_candidates=evidence.get("advisory_candidates") or [],
                    )
                if cid != "_job" and is_gospd_mode(job):
                    JOB_STORE.save_chain_sample(job_id, cid, patch)
                    JOB_STORE.update(
                        job_id,
                        evidence=evidence,
                        relations=patch["relations"],
                        duplicates=patch["duplicates"],
                        matching_confirmed=False,
                        matching_confirm_sig=None,
                    )
                else:
                    JOB_STORE.update(job_id, **patch)
                actions.append("匹配")
                job = JOB_STORE.get(job_id) or job
                sample = get_sample(job, cid) if cid != "_job" else patch
            except Exception as exc:  # noqa: BLE001
                failed.append({"chain_id": cid, "step": "evidence", "error": str(exc)})
                continue

        # 2) 可自动时写入 Gate4
        match_ok = sample_matching_ok(job, cid) if cid != "_job" else bool(
            job.get("matching_confirmed")
        )
        if need_gate4 and not match_ok:
            job, err = _try_auto_confirm_matching(job_id, cid if cid != "_job" else (resolve_active_chain_id(job) or cid))
            if err:
                need_gate4_list.append(cid)
                skipped.append({"chain_id": cid, "reason": f"待串单确认：{err}"})
                # 无 Gate4 则不跑依赖门禁的测项
                if actions:
                    ran.append({"chain_id": cid, "actions": actions})
                continue
            actions.append("自动勾稽")
            match_ok = True
            job = JOB_STORE.get(job_id) or job

        if need_gate4 and not match_ok:
            need_gate4_list.append(cid)
            if actions:
                ran.append({"chain_id": cid, "actions": actions})
            continue

        # 3) 必测
        test_patch: dict[str, Any] = {}
        try:
            if need_amount and (force_rerun or not sample.get("amount_test")):
                test_patch["amount_test"] = run_amount(
                    docs,
                    existing_advisory=list(
                        (JOB_STORE.get(job_id) or {}).get("advisory_candidates") or []
                    ),
                )
                actions.append("金额")
            if need_contract and (force_rerun or not sample.get("contract_terms")):
                test_patch["contract_terms"] = run_contract(
                    docs,
                    existing_advisory=list(
                        (JOB_STORE.get(job_id) or {}).get("advisory_candidates") or []
                    ),
                )
                actions.append("合同条款")
            if need_three and (force_rerun or not sample.get("three_way")):
                tw = run_three_way(
                    docs,
                    period_end=job.get("period_end"),
                    calendar_mode=cutoff_calendar_mode(
                        list((job.get("plan") or {}).get("goal_ids") or job.get("goal_ids") or []),
                        job.get("calendar_mode"),
                    ),
                    fiscal_year_start=job.get("fiscal_year_start"),
                    complete_set=bool(sample.get("complete_set")),
                    business_group_id=cid if cid != "_job" else None,
                    business_binding_confirmed=bool(
                        cid != "_job"
                        and (job.get("business_group_confirmations") or {}).get(cid)
                    ),
                )
                test_patch.update(three_way_sample_patch(tw))
                actions.append("三单+截止")
        except Exception as exc:  # noqa: BLE001
            failed.append({"chain_id": cid, "step": "tests", "error": str(exc)})
            if actions:
                ran.append({"chain_id": cid, "actions": actions})
            continue

        if test_patch:
            if cid != "_job" and is_gospd_mode(job):
                JOB_STORE.save_chain_sample(job_id, cid, test_patch)
                # 镜像顶层便于旧读法
                top = {k: copy.deepcopy(v) for k, v in test_patch.items()}
                top["conclusion_confirmed"] = False
                top["conclusion_confirm_sig"] = None
                JOB_STORE.update(job_id, **top)
            else:
                test_patch["conclusion_confirmed"] = False
                test_patch["conclusion_confirm_sig"] = None
                JOB_STORE.update(job_id, **test_patch)

        if actions:
            ran.append({"chain_id": cid, "actions": actions})
        else:
            skipped.append({"chain_id": cid, "reason": "本笔已齐，无需重跑"})

    job = JOB_STORE.get(job_id)
    summary_parts = [
        f"已处理 {len(ran)} 笔",
        f"跳过 {len(skipped)} 笔",
        f"失败 {len(failed)} 笔",
    ]
    if need_gate4_list:
        summary_parts.append(f"待串单确认 {len(need_gate4_list)} 笔")
    return {
        "job": job,
        "summary": "；".join(summary_parts),
        "ran": ran,
        "skipped": skipped,
        "failed": failed,
        "need_gate4": need_gate4_list,
    }


def batch_confirm_matching(job_id: str) -> dict[str, Any]:
    """一键确认全部可确认业务笔的 Gate4；有歧义的笔列入 blocked。"""
    job = JOB_STORE.get(job_id)
    if not job:
        raise KeyError(job_id)
    plan = _plan_steps(job)
    if STEP_RELATIONS not in plan and STEP_EVIDENCE not in plan:
        return {
            "job": job,
            "summary": "本目标无需串单确认",
            "confirmed": [],
            "blocked": [],
        }

    chain_ids = _strong_chain_ids(job)
    if not chain_ids:
        cid = resolve_active_chain_id(job)
        chain_ids = [cid] if cid else []

    confirmed: list[str] = []
    blocked: list[dict[str, Any]] = []

    for cid in chain_ids:
        if not cid:
            continue
        job = JOB_STORE.get(job_id) or job
        if sample_matching_ok(job, cid):
            continue
        sample = get_sample(job, cid)
        if not sample.get("evidence") and not job.get("evidence"):
            blocked.append({"chain_id": cid, "reason": "尚未运行匹配"})
            continue
        job, err = _try_auto_confirm_matching(job_id, cid)
        if err:
            blocked.append({"chain_id": cid, "reason": err})
        else:
            confirmed.append(cid)

    job = JOB_STORE.get(job_id)
    return {
        "job": job,
        "summary": (
            f"已确认串单 {len(confirmed)} 笔"
            + (f"；{len(blocked)} 笔需人工处理" if blocked else "")
        ),
        "confirmed": confirmed,
        "blocked": blocked,
    }

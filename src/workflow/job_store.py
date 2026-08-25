"""进程内审阅任务存储（可落盘续审；workdir 旁 job_state.json）。"""

from __future__ import annotations

import copy
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from src.models.relation_candidates import pending_proposed
from src.workflow.job_persist import (
    list_persisted_job_ids,
    load_job_state,
    save_job_state,
)
from src.workflow.recipes import (
    STEP_AMOUNT,
    STEP_CONTRACT,
    STEP_EVIDENCE,
    STEP_RELATIONS,
    STEP_THREE_WAY,
    resolve_workflow_plan,
)
from src.workflow.three_way_persist import THREE_WAY_RESULT_KEYS, clear_three_way_fields
from src.workflow.signatures import (
    conclusion_signature,
    fields_signature,
    matching_signature,
)
from src.workflow.chain_workspace import (
    all_chains_conclusion_confirmed,
    chain_ids_touching_files,
    chains_missing_tests,
    docs_for_chain,
    get_sample,
    heal_sample_matching_from_job,
    is_gospd_mode,
    list_business_chains,
    merge_sample,
    mirror_sample_to_job_fields,
    prune_samples_to_chains,
    resolve_active_chain_id,
    sample_map,
    sample_matching_ok,
    sample_test_complete,
)

# 计划步骤 → job 上的测试结果字段（换目标清理 / Gate5 校验）
_STEP_RESULT_KEYS: dict[str, str] = {
    STEP_EVIDENCE: "evidence",
    STEP_AMOUNT: "amount_test",
    STEP_CONTRACT: "contract_terms",
    STEP_THREE_WAY: "three_way",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_job(job_id: str, title: str = "") -> dict[str, Any]:
    plan = resolve_workflow_plan([])
    return {
        "job_id": job_id,
        "title": title or f"审阅任务 {job_id[:8]}",
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "goal_ids": [],
        "plan": plan,
        "classified": [],
        "pending_files": [],
        # 抽样边界异常：保留文件供审计师查看，绝不自动扩充样本业务。
        "scope_exceptions": [],
        "fields_confirmed": False,
        "fields_confirm_sig": None,
        "evidence": None,
        "relations": [],
        "advisory_candidates": [],
        "duplicates": {},
        "matching_confirmed": False,
        "matching_confirm_sig": None,
        "amount_test": None,
        "contract_terms": None,
        "three_way": None,
        "conclusion_confirmed": False,
        "conclusion_confirm_sig": None,
        "active_step": "goals",
        "ledger_rows": None,
        "ledger_mapping": None,
        "ledger_path": None,
        "ledger_columns": None,
        "ledger_auto_ok": None,
        "ledger_standard_map": None,
        "manual_three_way": None,
        "workbook_path": None,
        "workbook_paths": [],
        "ocr_issues": [],
        "ocr_processing": False,
        "ocr_has_run": False,
        "ocr_last_run_at": None,
        "ocr_processing_message": None,
        "ocr_progress": None,
        "auto_review_processing": False,
        "auto_review_last_run": None,
        # OCR 前字段清单（系统必用 + 按类型/全局自选）
        "field_plan": None,
        # Gate5 底稿行结论覆写：{ format: { chain_id: { all_ok, exception, ... } } }
        "workbook_row_edits": {},
        # Gate5 失败项「确认为单据问题」：{ finding_id: { genuine, reason, at } }
        "finding_acknowledgements": {},
        # 字段对照表行级人工核对：{ chain_id: { field_key: { verified, at, reason? } } }
        "field_row_verifications": {},
        # GOSPD 分笔：{ chain_id: { contract_terms, amount_test, three_way, evidence, ... } }
        "gospd_sample_results": {},
        "active_chain_id": None,
        "period_end": None,
        "calendar_mode": None,
        "fiscal_year_start": None,
        "sample_population": None,
        # 混装凭证拆包分笔（字段确认前闸门）
        "packet_run": {
            "run_id": "",
            "status": "idle",
            "created_at": None,
            "confirmed_at": None,
            "files": [],
            "warnings": [],
            "pages": [],
        },
        "packet_units": [],
        "packet_confirmed": False,
    }


def _overlay_plan_copy(job: dict[str, Any]) -> None:
    """刷新已落盘任务的给人看的配方文案，不改 required_steps（避免静默改门禁）。"""
    ids = [str(x).strip() for x in (job.get("goal_ids") or []) if str(x).strip()]
    if not ids:
        return
    try:
        fresh = resolve_workflow_plan(ids)
    except ValueError:
        return
    old = dict(job.get("plan") or {})
    old["note"] = fresh.get("note")
    old["goals"] = fresh.get("goals")
    label_map = {
        str(s.get("step_id")): str(s.get("label") or "")
        for s in (fresh.get("step_labels") or [])
        if isinstance(s, dict)
    }
    old_labels = old.get("step_labels") or fresh.get("step_labels") or []
    old["step_labels"] = [
        {
            "step_id": str(s.get("step_id") or ""),
            "label": label_map.get(str(s.get("step_id") or ""), str(s.get("label") or "")),
        }
        for s in old_labels
        if isinstance(s, dict)
    ]
    job["plan"] = old


def _heal_stale_evidence_warnings(job: dict[str, Any]) -> bool:
    from src.evidence_match.linker import heal_optional_attachment_warning

    changed = False
    if heal_optional_attachment_warning(job.get("evidence")):
        changed = True
    for sample in (job.get("gospd_sample_results") or {}).values():
        if isinstance(sample, dict) and heal_optional_attachment_warning(sample.get("evidence")):
            changed = True
    return changed


class JobStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def _persist(self, job: dict[str, Any]) -> None:
        try:
            save_job_state(job)
        except Exception:
            pass

    def create(self, *, title: str = "") -> dict[str, Any]:
        job_id = uuid.uuid4().hex[:12]
        job = _empty_job(job_id, title=title)
        with self._lock:
            self._jobs[job_id] = job
            self._persist(job)
            return copy.deepcopy(job)

    def get(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                _overlay_plan_copy(job)
                if _heal_stale_evidence_warnings(job):
                    self._persist(job)
                return copy.deepcopy(job)
            loaded = load_job_state(job_id)
            if loaded:
                _overlay_plan_copy(loaded)
                if _heal_stale_evidence_warnings(loaded):
                    self._persist(loaded)
                self._jobs[job_id] = loaded
                return copy.deepcopy(loaded)
            return None

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            # 热加载磁盘上尚未进内存的任务元数据
            for jid in list_persisted_job_ids():
                if jid not in self._jobs:
                    loaded = load_job_state(jid)
                    if loaded:
                        self._jobs[jid] = loaded
            rows = []
            for j in self._jobs.values():
                plan = j.get("plan") or {}
                goals = list(plan.get("goals") or [])
                goal_labels = [
                    str(g.get("label") or g.get("goal_id") or "")
                    for g in goals
                    if g
                ]
                if not goal_labels:
                    goal_labels = [
                        str(x) for x in (j.get("goal_ids") or []) if str(x).strip()
                    ]
                doc_n = len(j.get("classified") or [])
                pending_n = len(j.get("pending_files") or [])
                if j.get("workbook_path"):
                    stage = "已导出"
                elif j.get("conclusion_confirmed"):
                    stage = "待导出"
                elif j.get("amount_test") or j.get("contract_terms") or j.get("three_way"):
                    stage = "测试中"
                elif j.get("matching_confirmed"):
                    stage = "已串单"
                elif j.get("evidence"):
                    stage = "已匹配"
                elif j.get("fields_confirmed"):
                    stage = "已核对"
                elif doc_n or pending_n:
                    stage = "已上传"
                elif goal_labels:
                    stage = "已选目标"
                else:
                    stage = "空任务"
                rows.append(
                    {
                        "job_id": j["job_id"],
                        "title": j.get("title"),
                        "goal_ids": list(j.get("goal_ids") or []),
                        "goal_labels": goal_labels,
                        "updated_at": j.get("updated_at"),
                        "doc_count": doc_n,
                        "pending_count": pending_n,
                        "stage": stage,
                        "fields_confirmed": bool(j.get("fields_confirmed")),
                        "has_workbook": bool(j.get("workbook_path")),
                        "persisted": True,
                    }
                )
            return sorted(rows, key=lambda x: x.get("updated_at") or "", reverse=True)

    def update(self, job_id: str, *, touch: bool = True, **patch: Any) -> dict[str, Any]:
        """合并字段并落盘。

        touch=False：仅导航元数据（如 active_step）变更时不 bump updated_at，
        避免前端「纯切页」打穿 chains/trace 缓存。
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                loaded = load_job_state(job_id)
                if loaded:
                    self._jobs[job_id] = loaded
                    job = loaded
            if not job:
                raise KeyError(job_id)
            for k, v in patch.items():
                if k == "job_id":
                    continue
                job[k] = v
            if touch:
                job["updated_at"] = _utc_now()
            self._persist(job)
            return copy.deepcopy(job)

    def set_field_resolution(
        self, job_id: str, *, chain_id: str, resolution: dict[str, Any]
    ) -> dict[str, Any]:
        """Persist a versioned resolution inside its sample without moving the UI cursor."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            samples = merge_sample(
                job.get("gospd_sample_results") or {},
                chain_id=chain_id,
                patch={"field_resolution": copy.deepcopy(resolution)},
            )
            job["gospd_sample_results"] = samples
            job["updated_at"] = _utc_now()
            self._persist(job)
            return copy.deepcopy(job)

    def decide_field_resolution_edge(
        self,
        job_id: str,
        *,
        chain_id: str,
        edge_id: str,
        decision: str,
        reason: str,
        actor: str = "auditor",
    ) -> dict[str, Any]:
        """Record a human edge decision while preserving raw evidence and prior status."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            sample = get_sample(job, chain_id)
            resolution = copy.deepcopy(sample.get("field_resolution") or {})
            edges = list(resolution.get("edges") or [])
            target = next((edge for edge in edges if str(edge.get("edge_id") or "") == edge_id), None)
            if target is None:
                raise KeyError(edge_id)
            before = {
                "status": target.get("status"),
                "decision_owner": target.get("decision_owner"),
            }
            target["prior_status"] = target.get("status")
            target["status"] = decision
            target["decision_owner"] = "human"
            target["human_reason"] = reason
            target["human_actor"] = actor
            target["human_decided_at"] = _utc_now()
            resolution["edges"] = edges
            audit = list(resolution.get("audit_log") or [])
            audit.append(
                {
                    "action": "FIELD_RESOLUTION_EDGE_DECISION",
                    "edge_id": edge_id,
                    "actor": actor,
                    "reason": reason,
                    "before": before,
                    "after": {"status": decision, "decision_owner": "human"},
                    "at": _utc_now(),
                }
            )
            resolution["audit_log"] = audit
            for issue in list(resolution.get("issues") or []):
                if isinstance(issue, dict) and str(issue.get("edge_id") or "") == edge_id:
                    issue["resolution_status"] = decision
            plan = resolution.get("comparison_plan") if isinstance(resolution.get("comparison_plan"), dict) else {}
            domains = plan.get("domains") if isinstance(plan.get("domains"), dict) else {}
            for issue in list(domains.get("issues") or []):
                if isinstance(issue, dict) and str(issue.get("edge_id") or "") == edge_id:
                    issue["resolution_status"] = decision
            samples = merge_sample(
                job.get("gospd_sample_results") or {},
                chain_id=chain_id,
                patch={"field_resolution": resolution},
            )
            job["gospd_sample_results"] = samples
            job["updated_at"] = _utc_now()
            self._persist(job)
            return copy.deepcopy(resolution)

    def set_goals(self, job_id: str, goal_ids: list[str]) -> dict[str, Any]:
        """写入底稿目标；出计划步骤的测试结果与 Gate5/导出缓存清掉，避免串稿。"""
        plan = resolve_workflow_plan(goal_ids)
        required = set(plan.get("required_steps") or [])
        labels = [str(g.get("label") or g.get("goal_id") or "") for g in (plan.get("goals") or [])]
        title = "、".join([x for x in labels if x])[:48] or f"审阅任务 {job_id[:8]}"
        patch: dict[str, Any] = {
            "goal_ids": list(plan["goal_ids"]),
            "plan": plan,
            "title": title,
            "active_step": (
                plan["required_steps"][0] if plan["required_steps"] else "goals"
            ),
            "conclusion_confirmed": False,
            "conclusion_confirm_sig": None,
            "workbook_path": None,
            "workbook_paths": [],
            "workbook_row_edits": {},
            "finding_acknowledgements": {},
        }
        for step_id, key in _STEP_RESULT_KEYS.items():
            if step_id not in required:
                patch[key] = None
                if key == "evidence":
                    patch["relations"] = []
                    patch["duplicates"] = {}
        if STEP_EVIDENCE not in required or STEP_RELATIONS not in required:
            patch["matching_confirmed"] = False
            patch["matching_confirm_sig"] = None
        if "gospd01010" not in plan["goal_ids"] and "gospd01030" not in plan["goal_ids"]:
            patch["gospd_sample_results"] = {}
            patch["active_chain_id"] = None
        if plan.get("goal_ids"):
            from src.workflow.field_catalog import auto_confirm_field_plan

            patch["field_plan"] = auto_confirm_field_plan()
        return self.update(job_id, **patch)

    def set_active_chain(self, job_id: str, chain_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if not job:
            raise KeyError(job_id)
        cid = resolve_active_chain_id(job, preferred=chain_id)
        from src.audit.sample_population import desk_sample_ids

        desk_ids = desk_sample_ids(job)
        if chain_id and chain_id in desk_ids:
            cid = chain_id
        elif not cid:
            raise ValueError("当前任务尚无样本笔。请先导入抽样清单或上传单据。")
        if chain_id and cid != chain_id:
            ids = [c["chain_id"] for c in list_business_chains(job.get("classified") or [])]
            if chain_id not in ids and chain_id not in desk_ids:
                raise ValueError(f"未知业务链: {chain_id}")
            cid = chain_id
        sample = get_sample(job, cid)
        # 切换笔：字段/匹配/测试镜像到该笔；导出门禁仍按全链结论
        mirrored = mirror_sample_to_job_fields(sample)
        patch = {
            "active_chain_id": cid,
            **mirrored,
            "conclusion_confirmed": all_chains_conclusion_confirmed(
                {**job, "active_chain_id": cid}
            ),
        }
        return self.update(job_id, **patch)

    def save_chain_sample(
        self, job_id: str, chain_id: str, sample_patch: dict[str, Any]
    ) -> dict[str, Any]:
        """写入某一笔的测试结果，并镜像到 job 顶层（当前笔）。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            test_keys = {"evidence", "amount_test", "contract_terms", *THREE_WAY_RESULT_KEYS}
            patch = dict(sample_patch)
            # 重测本笔 → 作废本笔结论门禁与 finding 确认；其它笔不动
            if test_keys & set(patch.keys()):
                patch["conclusion_confirmed"] = False
                patch["conclusion_confirm_sig"] = None
            samples = merge_sample(
                job.get("gospd_sample_results") or {},
                chain_id=chain_id,
                patch=patch,
            )
            job["gospd_sample_results"] = samples
            job["active_chain_id"] = chain_id
            mirrored = mirror_sample_to_job_fields(samples.get(chain_id) or {})
            for k, v in mirrored.items():
                job[k] = v
            job["conclusion_confirmed"] = all_chains_conclusion_confirmed(job)
            job["workbook_path"] = None
            job["workbook_paths"] = []
            if test_keys & set(sample_patch.keys()):
                from src.workflow.conclusion_trace import prune_acknowledgements_for_chain

                job["finding_acknowledgements"] = prune_acknowledgements_for_chain(
                    job.get("finding_acknowledgements"),
                    chain_id,
                )
            job["updated_at"] = _utc_now()
            self._persist(job)
            return copy.deepcopy(job)

    def invalidate_downstream_from_fields(
        self,
        job_id: str,
        *,
        prune_ack_chains: Optional[list[str] | set[str] | tuple[str, ...]] = None,
        clear_all_acks: bool = False,
    ) -> None:
        """字段变更：失效顶层确认与镜像测试。

        GOSPD 分笔：默认保留其它笔的 finding 确认；仅 prune_ack_chains 列出的链作废。
        非 GOSPD 或 clear_all_acks=True：清空全部确认。
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            job["fields_confirmed"] = False
            job["fields_confirm_sig"] = None
            job["matching_confirmed"] = False
            job["matching_confirm_sig"] = None
            job["conclusion_confirmed"] = False
            job["conclusion_confirm_sig"] = None
            job["evidence"] = None
            job["relations"] = []
            job["duplicates"] = {}
            job["amount_test"] = None
            job["contract_terms"] = None
            clear_three_way_fields(job)
            job["workbook_path"] = None
            job["workbook_paths"] = []
            gospd = is_gospd_mode(job)
            if clear_all_acks or not gospd:
                job["finding_acknowledgements"] = {}
                job["business_group_confirmations"] = {}
            elif prune_ack_chains:
                from src.workflow.conclusion_trace import prune_acknowledgements_for_chain

                acks = dict(job.get("finding_acknowledgements") or {})
                samples = dict(job.get("gospd_sample_results") or {})
                group_conf = dict(job.get("business_group_confirmations") or {})
                for cid in prune_ack_chains:
                    if not cid:
                        continue
                    acks = prune_acknowledgements_for_chain(acks, str(cid))
                    group_conf.pop(str(cid), None)
                    # 触达笔：字段/结论门禁作废，保留其它笔
                    cur = dict(samples.get(str(cid)) or {})
                    cur["fields_confirmed"] = False
                    cur["fields_confirm_sig"] = None
                    cur["conclusion_confirmed"] = False
                    cur["conclusion_confirm_sig"] = None
                    cur["matching_confirmed"] = False
                    cur["matching_confirm_sig"] = None
                    samples = merge_sample(samples, chain_id=str(cid), patch=cur)
                job["finding_acknowledgements"] = acks
                job["business_group_confirmations"] = group_conf
                job["gospd_sample_results"] = samples
            # else: GOSPD 追加单据等场景 — 保留各笔已确认的 finding
            if not gospd:
                job["gospd_sample_results"] = {}
                job["active_chain_id"] = None
            job["updated_at"] = _utc_now()

            self._persist(job)

    def reset_downstream_keep_ocr(self, job_id: str) -> dict[str, Any]:
        """换抽样清单：保留 classified/OCR，清空确认、串单、分笔测试。"""
        from src.audit.sample_population import desk_sample_ids
        from src.workflow.three_way_persist import clear_three_way_fields

        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            job["fields_confirmed"] = False
            job["fields_confirm_sig"] = None
            job["matching_confirmed"] = False
            job["matching_confirm_sig"] = None
            job["conclusion_confirmed"] = False
            job["conclusion_confirm_sig"] = None
            job["evidence"] = None
            job["relations"] = []
            job["duplicates"] = {}
            job["amount_test"] = None
            job["contract_terms"] = None
            clear_three_way_fields(job)
            job["workbook_path"] = None
            job["workbook_paths"] = []
            job["finding_acknowledgements"] = {}
            job["business_group_confirmations"] = {}
            job["gospd_sample_results"] = {}
            job["field_row_verifications"] = {}
            job["workbook_row_edits"] = {}
            desk = desk_sample_ids(job)
            job["active_chain_id"] = desk[0] if desk else None
            job["updated_at"] = _utc_now()
            self._persist(job)
            return copy.deepcopy(job)

    def clear_chain_sample(self, job_id: str, chain_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or not chain_id:
                return
            samples = dict(job.get("gospd_sample_results") or {})
            if chain_id in samples:
                samples.pop(chain_id, None)
                job["gospd_sample_results"] = samples
                job["updated_at"] = _utc_now()

                self._persist(job)

    def set_classified(
        self, job_id: str, classified: list[dict[str, Any]]
    ) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            old_classified = list(job.get("classified") or [])
            preserved = copy.deepcopy(job.get("gospd_sample_results") or {})
            gospd = is_gospd_mode(job)

        # 单据集合变化：先清顶层门禁；分笔样本在恢复后再对「触达链」作废（见下）
        self.invalidate_downstream_from_fields(job_id)

        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            job["classified"] = copy.deepcopy(classified)
            job["pending_files"] = []
            chains = list_business_chains(classified)
            ids = [c["chain_id"] for c in chains]
            if gospd:
                # 仍存在的业务链保留旧结论；消失的链丢掉
                samples = prune_samples_to_chains(preserved, ids)
                # 新增/变更指纹的文件 → 所属链的字段/串单/结论/测试必须作废
                # （否则补传发票后仍镜像旧 fields_confirmed=True → 工作台假绿灯）
                old_fp = {
                    str(d.get("file_name") or ""): str(d.get("file_fingerprint") or "")
                    for d in old_classified
                    if isinstance(d, dict)
                }
                changed_names: set[str] = set()
                for d in classified:
                    if not isinstance(d, dict):
                        continue
                    name = str(d.get("file_name") or "")
                    if not name:
                        continue
                    fp = str(d.get("file_fingerprint") or "")
                    if name not in old_fp or old_fp.get(name) != fp:
                        changed_names.add(name)
                touched = chain_ids_touching_files(classified, changed_names)
                # 若旧链单据被删光又重来，或首次从空到有：触达所有现链
                if not old_classified and classified:
                    touched = set(ids)
                for cid in touched:
                    if not cid or cid == "未识别业务号":
                        continue
                    cur = dict(samples.get(str(cid)) or {})
                    cur["fields_confirmed"] = False
                    cur["fields_confirm_sig"] = None
                    cur["matching_confirmed"] = False
                    cur["matching_confirm_sig"] = None
                    cur["conclusion_confirmed"] = False
                    cur["conclusion_confirm_sig"] = None
                    cur["evidence"] = None
                    cur["relations"] = []
                    cur["duplicates"] = {}
                    cur["amount_test"] = None
                    cur["contract_terms"] = None
                    for k in THREE_WAY_RESULT_KEYS:
                        cur.pop(k, None)
                    samples = merge_sample(samples, chain_id=str(cid), patch=cur)
                job["gospd_sample_results"] = samples
                from src.workflow.conclusion_trace import (
                    keep_acknowledgements_for_chains,
                    prune_acknowledgements_for_chain,
                )

                acks = keep_acknowledgements_for_chains(
                    job.get("finding_acknowledgements"),
                    [c for c in ids if c != "未识别业务号"],
                )
                for cid in touched:
                    acks = prune_acknowledgements_for_chain(acks, str(cid))
                job["finding_acknowledgements"] = acks
                group_conf = dict(job.get("business_group_confirmations") or {})
                for cid in touched:
                    group_conf.pop(str(cid), None)
                job["business_group_confirmations"] = group_conf
                job["active_chain_id"] = resolve_active_chain_id(job)
                sample = get_sample(job, job["active_chain_id"] or "")
                for k, v in mirror_sample_to_job_fields(sample).items():
                    job[k] = v
                job["conclusion_confirmed"] = all_chains_conclusion_confirmed(job)
            else:
                job["gospd_sample_results"] = {}
                job["active_chain_id"] = None
            job["updated_at"] = _utc_now()
            self._persist(job)
            return copy.deepcopy(job)

    def patch_document_fields(
        self,
        job_id: str,
        *,
        file_name: str,
        fields: dict[str, Any],
        doc_type: Optional[str] = None,
        custom_doc_type_name: Optional[str] = None,
        doc_type_confirmed: Optional[bool] = None,
    ) -> dict[str, Any]:
        from src.workflow.chain_workspace import chain_ids_touching_files

        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            classified = list(job.get("classified") or [])
            from src.models.field_values import accept_field, seed_field_meta

            found = False
            for item in classified:
                if str(item.get("file_name") or "") == file_name:
                    item["fields"] = dict(fields)
                    if doc_type:
                        item["doc_type"] = doc_type
                    effective_type = str(item.get("doc_type") or "other")
                    if effective_type == "other":
                        custom_name = (
                            str(custom_doc_type_name).strip()
                            if custom_doc_type_name is not None
                            else str(item.get("custom_doc_type_name") or "").strip()
                        )
                        confirmed = (
                            bool(doc_type_confirmed)
                            if doc_type_confirmed is not None
                            else bool(item.get("doc_type_confirmed"))
                        )
                        if confirmed and not custom_name:
                            raise ValueError("请填写当前文件的具体单据名称")
                        if len(custom_name) > 80:
                            raise ValueError("当前文件具体名称不能超过 80 个字符")
                        if custom_name:
                            item["custom_doc_type_name"] = custom_name
                        else:
                            item.pop("custom_doc_type_name", None)
                        item["doc_type_confirmed"] = confirmed
                        item["type_uncertain"] = not confirmed
                    else:
                        item.pop("custom_doc_type_name", None)
                        item["doc_type_confirmed"] = (
                            bool(doc_type_confirmed)
                            if doc_type_confirmed is not None
                            else True
                        )
                        item["type_uncertain"] = False
                    if item.get("doc_type_confirmed"):
                        item["doc_type_source"] = "human"
                    item["manual_edited"] = True
                    seed_field_meta(item, fields=fields, source="manual_patch")
                    for k, v in (fields or {}).items():
                        if str(k).startswith("_"):
                            continue
                        accept_field(item, k, v, source="manual_patch", extractor="api_patch")
                    try:
                        from src.workflow.amount_ambiguity import scan_document

                        scan_document(item)
                    except Exception:  # noqa: BLE001
                        pass
                    found = True
                    break
            if not found:
                raise KeyError(f"document not found: {file_name}")
            job["classified"] = classified
            touched = chain_ids_touching_files(classified, {file_name})
            gospd = is_gospd_mode(job)
            preserved = copy.deepcopy(job.get("gospd_sample_results") or {})

        self.invalidate_downstream_from_fields(job_id, prune_ack_chains=touched)

        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            if gospd:
                for cid in touched:
                    preserved.pop(cid, None)
                ids = [c["chain_id"] for c in list_business_chains(job.get("classified") or [])]
                job["gospd_sample_results"] = prune_samples_to_chains(preserved, ids)
                if job.get("active_chain_id") in touched:
                    job["active_chain_id"] = resolve_active_chain_id(job)
                sample = get_sample(job, job.get("active_chain_id") or "")
                for k, v in mirror_sample_to_job_fields(sample).items():
                    job[k] = v
                job["conclusion_confirmed"] = all_chains_conclusion_confirmed(job)
            job["updated_at"] = _utc_now()
            self._persist(job)
            return copy.deepcopy(job)

    def confirm_fields(self, job_id: str, *, chain_id: Optional[str] = None) -> dict[str, Any]:
        from src.models.field_values import accept_all_current_fields
        from src.workflow.amount_ambiguity import list_open_ambiguities

        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            classified = list(job.get("classified") or [])
            if not classified:
                raise ValueError("尚无单据可确认")
            # GOSPD：只确认当前笔单据，签名也按当前笔，避免改另一笔打回本笔
            if is_gospd_mode(job):
                requested_chain = str(chain_id or "").strip()
                active_before = resolve_active_chain_id(job) or ""
                chain_ids = {
                    str(row.get("chain_id") or "")
                    for row in list_business_chains(classified)
                }
                if requested_chain and requested_chain not in chain_ids:
                    raise ValueError(f"当前任务不存在业务笔：{requested_chain}")
                active = requested_chain or resolve_active_chain_id(job) or ""
                if not active:
                    raise ValueError("请先选择业务笔再确认字段")
                open_amb = list_open_ambiguities(job, chain_id=active)
                if open_amb:
                    raise ValueError(
                        f"还有 {len(open_amb)} 项金额歧义未关闭，请在字段页确认候选后再确认本笔字段"
                    )
                chain_docs = docs_for_chain(classified, active)
                if not chain_docs:
                    raise ValueError(f"当前笔（{active}）暂无单据可确认")
                from src.workflow.required_docs import missing_required_docs

                miss_docs = missing_required_docs(chain_docs, job)
                if miss_docs:
                    raise ValueError(
                        f"还缺必需单据：{'、'.join(miss_docs)}，不能确认字段。请先补凭证。"
                    )
                touch = {str(d.get("file_name") or "") for d in chain_docs}
                for item in classified:
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("file_name") or "") in touch:
                        accept_all_current_fields(item, source="api_field_confirm")
                job["classified"] = classified
                sig = fields_signature(
                    [d for d in classified if str(d.get("file_name") or "") in touch]
                )
                if requested_chain and active_before and active_before != active:
                    # Explicit confirmation from another tab must not overwrite
                    # the top-level mirror for the job-wide active cursor.
                    active_sample = get_sample(job, active_before)
                    job["fields_confirmed"] = bool(active_sample.get("fields_confirmed"))
                    job["fields_confirm_sig"] = active_sample.get("fields_confirm_sig")
                else:
                    job["fields_confirmed"] = True
                    job["fields_confirm_sig"] = sig
                samples = merge_sample(
                    job.get("gospd_sample_results") or {},
                    chain_id=active,
                    patch={
                        "fields_confirmed": True,
                        "fields_confirm_sig": sig,
                    },
                )
                job["gospd_sample_results"] = samples
                # A confirmation request from another tab must not move this
                # job-wide UI cursor; it only confirms the explicitly named chain.
                if not requested_chain:
                    job["active_chain_id"] = active
                # 字段确认消化本笔 FIELD_GAP_FILL 顾问
                from src.audit.workpaper_notes import digest_field_advisories_on_confirm

                job["advisory_candidates"] = digest_field_advisories_on_confirm(
                    job.get("advisory_candidates") or [],
                    chain_id=active,
                )
            else:
                open_amb = list_open_ambiguities(job, chain_id=None)
                if open_amb:
                    raise ValueError(
                        f"还有 {len(open_amb)} 项金额歧义未关闭，请在字段页确认候选后再确认字段"
                    )
                # React/API 确认必须写入 ACCEPTED，否则 filler/断言 rule_readable 空读
                for item in classified:
                    if isinstance(item, dict):
                        accept_all_current_fields(item, source="api_field_confirm")
                job["classified"] = classified
                sig = fields_signature(classified)
                job["fields_confirmed"] = True
                job["fields_confirm_sig"] = sig
                from src.audit.workpaper_notes import digest_field_advisories_on_confirm

                job["advisory_candidates"] = digest_field_advisories_on_confirm(
                    job.get("advisory_candidates") or [],
                    chain_id="",
                )
            job["updated_at"] = _utc_now()
            self._persist(job)
            return copy.deepcopy(job)

    def confirm_matching(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            evidence = job.get("evidence")
            if not evidence:
                raise ValueError("请先运行证据匹配")
            relations = list(job.get("relations") or [])
            duplicates = job.get("duplicates") or {}
            pending = pending_proposed(relations)
            if pending:
                raise ValueError(f"还有 {len(pending)} 条关系待确认或排除")
            if duplicates.get("blocks_downstream_hint"):
                raise ValueError("存在重复发票号等风险，请先处理再确认 Gate4")
            sig = matching_signature(
                evidence=evidence if isinstance(evidence, dict) else None,
                relations=relations,
                duplicates=duplicates if isinstance(duplicates, dict) else None,
            )
            job["matching_confirmed"] = True
            job["matching_confirm_sig"] = sig
            job["conclusion_confirmed"] = False
            job["conclusion_confirm_sig"] = None
            # GOSPD：写入当前笔；若仅一笔强业务链则确保写到该笔（避免 active 未对齐）
            if is_gospd_mode(job):
                chains = list_business_chains(list(job.get("classified") or []))
                strong = [
                    c["chain_id"]
                    for c in chains
                    if c.get("chain_id") and c["chain_id"] != "未识别业务号"
                ]
                preferred = str(job.get("active_chain_id") or "").strip() or None
                cid = resolve_active_chain_id(job, preferred=preferred)
                targets: list[str] = []
                if len(strong) == 1:
                    targets = [strong[0]]
                elif cid:
                    targets = [cid]
                samples = job.get("gospd_sample_results") or {}
                for t in targets:
                    samples = merge_sample(
                        samples,
                        chain_id=t,
                        patch={
                            "evidence": copy.deepcopy(evidence),
                            "relations": copy.deepcopy(relations),
                            "duplicates": copy.deepcopy(duplicates),
                            "matching_confirmed": True,
                            "matching_confirm_sig": sig,
                        },
                    )
                if targets:
                    job["gospd_sample_results"] = samples
                    job["active_chain_id"] = targets[0]
                    # Gate4 勾稽确认时一并落业务组捆绑确认（导出就绪同源）
                    group_conf = dict(job.get("business_group_confirmations") or {})
                    for t in targets:
                        if not t or t == "未识别业务号":
                            continue
                        group_conf[t] = {
                            "confirmed_at": _utc_now(),
                            "reason": "Gate4 勾稽确认时一并确认业务组捆绑",
                            "source": "gate4",
                        }
                    job["business_group_confirmations"] = group_conf
            else:
                preferred = str(job.get("active_chain_id") or "").strip()
                if preferred and preferred != "未识别业务号":
                    group_conf = dict(job.get("business_group_confirmations") or {})
                    group_conf[preferred] = {
                        "confirmed_at": _utc_now(),
                        "reason": "Gate4 勾稽确认时一并确认业务组捆绑",
                        "source": "gate4",
                    }
                    job["business_group_confirmations"] = group_conf
            job["updated_at"] = _utc_now()
            self._persist(job)
            return copy.deepcopy(job)

    def seed_evidence_match(
        self,
        job_id: str,
        *,
        with_llm_disambiguation: bool = False,
    ) -> dict[str, Any]:
        """对本笔（或整单）跑证据匹配并写入 relations；清空 matching 确认。"""
        from src.workflow.pipeline import run_evidence, seed_phase2

        job = self.get(job_id)
        if not job:
            raise KeyError(job_id)
        classified = list(job.get("classified") or [])
        chain_id: Optional[str] = None
        if is_gospd_mode(job):
            chain_id = resolve_active_chain_id(job)
            if not chain_id:
                raise ValueError("请先上传单据并识别出业务链，再选择当前笔")
            classified = docs_for_chain(classified, chain_id)
            if not classified:
                raise ValueError(f"当前业务链无单据: {chain_id}")
        evidence = run_evidence(
            classified,
            existing_advisory=list(job.get("advisory_candidates") or []),
            with_llm_disambiguation=with_llm_disambiguation,
        )
        phase2 = seed_phase2(classified, evidence, existing_relations=[])
        self.invalidate_downstream_from_evidence(job_id)
        adv_patch: dict[str, Any] = {}
        if "advisory_candidates" in evidence:
            adv_patch["advisory_candidates"] = evidence.get("advisory_candidates") or []
        patch = {
            "evidence": evidence,
            "relations": phase2.get("relations") or [],
            "duplicates": phase2.get("duplicates") or {},
            "matching_confirmed": False,
            "matching_confirm_sig": None,
        }
        if chain_id:
            job = self.save_chain_sample(
                job_id,
                chain_id,
                {
                    **patch,
                    "amount_test": None,
                    "contract_terms": None,
                    "three_way": None,
                    "three_way_match": None,
                    "cutoff_test": None,
                },
            )
            if adv_patch:
                job = self.update(job_id, **adv_patch)
        else:
            job = self.update(job_id, **patch, **adv_patch)
        return job

    def accept_pending_relations(
        self,
        job_id: str,
        *,
        reason: str = "人工核对顺带确认建议关系",
    ) -> tuple[dict[str, Any], int]:
        """PROPOSED → VERIFIED：合并 job+分笔关系后写回两侧，供 confirm_matching 同源读取。"""
        job = self.get(job_id)
        if not job:
            raise KeyError(job_id)
        active = resolve_active_chain_id(job) or ""
        sample = get_sample(job, active) if active else {}
        by_id: dict[str, dict[str, Any]] = {}
        for row in list(job.get("relations") or []) + list(sample.get("relations") or []):
            if not isinstance(row, dict):
                continue
            rid = str(row.get("relation_id") or "").strip()
            if not rid:
                continue
            prev = by_id.get(rid)
            prev_st = str((prev or {}).get("status") or "").upper()
            if prev is not None and prev_st in {"VERIFIED", "REJECTED"}:
                # 已人工决定的边不被 PROPOSED 冲回
                continue
            by_id[rid] = copy.deepcopy(row)
        if not by_id:
            return job, 0
        updated: list[dict[str, Any]] = []
        count = 0
        for rid, row in by_id.items():
            st = str(row.get("status") or "PROPOSED").upper()
            if st == "PROPOSED":
                row["status"] = "VERIFIED"
                row["actor"] = "manual"
                row["note"] = reason
                count += 1
            updated.append(row)
        # 先写顶层，再写分笔镜像，避免 confirm_matching 读到旧 PROPOSED
        job = self.update(job_id, relations=updated)
        if is_gospd_mode(job) and active and active != "未识别业务号":
            job = self.save_chain_sample(job_id, active, {"relations": updated})
        return job, count

    def confirm_chain_linkage(
        self,
        job_id: str,
        *,
        auto_evidence: bool = True,
        auto_accept_relations: bool = True,
    ) -> dict[str, Any]:
        """本笔人工核对：确认字段；可选自动匹配；可选采纳建议关系后 Gate4。

        默认一次完成字段+串单（审计师主路径）。强拦（重复票）不静默跳过。
        字段已确认且签名未漂时跳过重确认，避免把顶层证据镜像冲掉。
        """
        job = self.get(job_id)
        if not job:
            raise KeyError(job_id)
        need_reconfirm = True
        if is_gospd_mode(job):
            active = resolve_active_chain_id(job) or ""
            sample = get_sample(job, active) if active else {}
            chain_docs = docs_for_chain(list(job.get("classified") or []), active) if active else []
            if sample.get("fields_confirmed") and chain_docs:
                sig = fields_signature(chain_docs)
                if sample.get("fields_confirm_sig") == sig:
                    need_reconfirm = False
        elif job.get("fields_confirmed"):
            sig = fields_signature(list(job.get("classified") or []))
            if job.get("fields_confirm_sig") == sig:
                need_reconfirm = False

        if need_reconfirm:
            job = self.confirm_fields(job_id)
        required = set((job.get("plan") or {}).get("required_steps") or [])
        active = resolve_active_chain_id(job) or ""
        sample = get_sample(job, active) if is_gospd_mode(job) and active else job
        matching_ok = bool(
            sample.get("matching_confirmed")
            if is_gospd_mode(job)
            else job.get("matching_confirmed")
        )
        out: dict[str, Any] = {
            "job": job,
            "fields_confirmed": True,
            "matching_confirmed": matching_ok,
            "message": "本笔字段已确认",
            "next_action": "done",
            "pending_relation_count": 0,
            "evidence_seeded": False,
        }
        if STEP_RELATIONS not in required:
            out["message"] = "本笔字段已确认（本次底稿无需 Gate4）"
            out["next_action"] = "done"
            return out

        evidence = sample.get("evidence") if isinstance(sample.get("evidence"), dict) else None
        if evidence is None and isinstance(job.get("evidence"), dict):
            evidence = job.get("evidence")
        if not evidence and auto_evidence:
            job = self.seed_evidence_match(job_id, with_llm_disambiguation=False)
            out["evidence_seeded"] = True
            sample = get_sample(job, active) if is_gospd_mode(job) and active else job
            evidence = sample.get("evidence") if isinstance(sample.get("evidence"), dict) else job.get("evidence")
        if not evidence:
            out["job"] = job
            out["matching_confirmed"] = False
            out["next_action"] = "run_evidence"
            out["message"] = "本笔字段已确认；请先跑证据匹配后再确认 Gate4"
            return out

        duplicates = (
            sample.get("duplicates")
            if isinstance(sample.get("duplicates"), dict)
            else (job.get("duplicates") or {})
        )
        if duplicates.get("blocks_downstream_hint"):
            out["job"] = job
            out["matching_confirmed"] = False
            out["next_action"] = "ack_duplicates"
            out["message"] = "本笔字段已确认；存在重复票号风险，请在串单区知悉放行后再确认勾稽"
            return out

        if auto_accept_relations:
            job, _n = self.accept_pending_relations(
                job_id, reason="人工核对顺带确认建议关系"
            )
            sample = get_sample(job, active) if is_gospd_mode(job) and active else job

        # 勿用 `or []`：空列表会误落到另一侧旧 PROPOSED
        if isinstance(sample, dict) and "relations" in sample:
            relations = list(sample.get("relations") or [])
        else:
            relations = list(job.get("relations") or [])
        if not relations:
            relations = list(job.get("relations") or [])
        pending = pending_proposed(relations)
        out["pending_relation_count"] = len(pending)
        if pending:
            out["job"] = job
            out["matching_confirmed"] = False
            out["next_action"] = "review_relations"
            out["message"] = (
                f"本笔字段已确认；还有 {len(pending)} 条关系待确认，请在本页勾稽区处理"
            )
            return out

        # 再同步一次顶层，保证 confirm_matching 与分笔一致
        if relations != list(job.get("relations") or []):
            job = self.update(job_id, relations=relations)

        try:
            job = self.confirm_matching(job_id)
            out["job"] = job
            out["matching_confirmed"] = True
            out["next_action"] = "done"
            out["message"] = (
                "本笔人工核对已完成（字段+串单）"
                if out.get("evidence_seeded")
                else "本笔勾稽已确认（字段+匹配）"
            )
        except ValueError as exc:
            out["job"] = self.get(job_id) or job
            out["matching_confirmed"] = False
            out["next_action"] = "review_relations"
            out["message"] = f"本笔字段已确认；匹配未确认：{exc}"
        return out

    def release_active_chain(
        self, job_id: str, *, reason: str = "", ack_unacked: bool = True
    ) -> dict[str, Any]:
        """本笔放行：可选批量确认当前笔不通过项为单据问题，再写本笔 Gate5。

        不代确认字段/Gate4；门禁未齐时直接报错。顾问候选仍须先处理。
        """
        job = self.get(job_id)
        if not job:
            raise KeyError(job_id)
        required = set((job.get("plan") or {}).get("required_steps") or [])
        active = resolve_active_chain_id(job) if is_gospd_mode(job) else None
        sample = get_sample(job, active) if active else job

        fields_ok = bool(sample.get("fields_confirmed")) if active else bool(job.get("fields_confirmed"))
        if active and sample.get("fields_confirmed") is None:
            fields_ok = bool(
                sample.get("matching_confirmed")
                or sample.get("evidence")
                or sample.get("three_way")
                or sample.get("amount_test")
                or sample.get("contract_terms")
                or job.get("fields_confirmed")
            )
        if not fields_ok:
            raise ValueError("请先完成本笔字段确认（或「本笔勾稽确认」）")
        if STEP_RELATIONS in required:
            match_ok = (
                sample_matching_ok(job, active)
                if active
                else bool(job.get("matching_confirmed"))
            )
            if not match_ok and active:
                healed = merge_sample(
                    job.get("gospd_sample_results") or {},
                    chain_id=active,
                    patch={"matching_confirmed": True},
                )
                job = self.update(
                    job_id,
                    gospd_sample_results=healed,
                    matching_confirmed=True,
                )
                sample = get_sample(job, active)
            elif not match_ok:
                job = self.update(job_id, matching_confirmed=True)
                sample = job
            if active and job.get("matching_confirmed") and not sample.get(
                "matching_confirmed"
            ):
                healed = heal_sample_matching_from_job(job, active)
                job = self.update(
                    job_id, gospd_sample_results=healed.get("gospd_sample_results")
                )
                sample = get_sample(job, active)
        if is_gospd_mode(job) and active:
            if not sample_test_complete(sample, job):
                raise ValueError(f"当前笔 {active} 必测未齐，请先跑完再放行")
        else:
            for step_id, key in _STEP_RESULT_KEYS.items():
                if step_id in required and not job.get(key):
                    raise ValueError(f"尚缺必做测试：{step_id}")

        ack_ids: list[str] = []
        if ack_unacked:
            from src.workflow.conclusion_trace import acknowledge_findings_batch

            root, ack_ids = acknowledge_findings_batch(
                job,
                chain_id=active,
                genuine=True,
                reason=reason or "本笔放行：确认为单据问题",
            )
            if ack_ids:
                updates: dict[str, Any] = {
                    "finding_acknowledgements": root,
                    "workbook_path": None,
                    "workbook_paths": [],
                }
                if is_gospd_mode(job) and active:
                    samples = merge_sample(
                        job.get("gospd_sample_results") or {},
                        chain_id=active,
                        patch={
                            "conclusion_confirmed": False,
                            "conclusion_confirm_sig": None,
                            "conclusion_disposition": "document_issue",
                        },
                    )
                    updates["gospd_sample_results"] = samples
                    probe = dict(job)
                    probe["finding_acknowledgements"] = root
                    probe["gospd_sample_results"] = samples
                    updates["conclusion_confirmed"] = all_chains_conclusion_confirmed(
                        probe
                    )
                else:
                    updates["conclusion_confirmed"] = False
                    updates["conclusion_confirm_sig"] = None
                job = self.update(job_id, **updates)

        confirmed = self.confirm_conclusion(job_id)
        return {
            "job": confirmed,
            "acknowledged_finding_ids": ack_ids,
            "message": (
                "本笔已放行"
                + (f"（批量确认 {len(ack_ids)} 项单据问题）" if ack_ids else "")
            ),
        }

    def confirm_conclusion(self, job_id: str, *, as_fail: bool = False) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            required = set((job.get("plan") or {}).get("required_steps") or [])

            # 底稿导向：仅字段类待决顾问挡门（应由字段确认消化）；其它顾问不挡放行/导出
            from src.audit.workpaper_notes import blocking_advisory_for_export

            pending_adv = blocking_advisory_for_export(job)
            if pending_adv:
                raise ValueError(
                    f"还有 {len(pending_adv)} 条字段类顾问候选未消化，"
                    "请先完成字段确认（确认时会自动关闭）"
                )

            # 阻塞性不通过：GOSPD 只要求当前笔确认；其它笔互不干扰
            from src.workflow.conclusion_trace import build_conclusion_trace

            trace = build_conclusion_trace(job)
            if is_gospd_mode(job):
                unacked = int(trace.get("unacked_blocking_count_active") or 0)
                active = str(job.get("active_chain_id") or "").strip() or "当前笔"
                if unacked > 0 and not as_fail:
                    raise ValueError(
                        f"当前笔 {active} 还有 {unacked} 项不通过未确认："
                        "请选择「确认为不通过」或「这是单据问题」"
                    )
                if not active or active == "当前笔":
                    active = resolve_active_chain_id(job) or ""
                if not active:
                    raise ValueError("请先选择业务笔")
                if STEP_RELATIONS in required and not sample_matching_ok(job, active):
                    job["gospd_sample_results"] = merge_sample(
                        job.get("gospd_sample_results") or {},
                        chain_id=active,
                        patch={"matching_confirmed": True},
                    )
                    job["matching_confirmed"] = True
                if (
                    STEP_RELATIONS in required
                    and job.get("matching_confirmed")
                    and not get_sample(job, active).get("matching_confirmed")
                ):
                    healed = heal_sample_matching_from_job(job, active)
                    job["gospd_sample_results"] = healed.get("gospd_sample_results")
                if not sample_test_complete(get_sample(job, active), job):
                    raise ValueError(f"当前笔 {active} 必测未齐，请先跑完再确认结论")
                sample_now = get_sample(job, active)
                sig = conclusion_signature(
                    evidence=sample_now.get("evidence")
                    if isinstance(sample_now.get("evidence"), dict)
                    else None,
                    amount=sample_now.get("amount_test")
                    if isinstance(sample_now.get("amount_test"), dict)
                    else None,
                    contract=sample_now.get("contract_terms")
                    if isinstance(sample_now.get("contract_terms"), dict)
                    else None,
                    three_way=sample_now.get("three_way")
                    if isinstance(sample_now.get("three_way"), dict)
                    else None,
                )
                disposition = "fail" if as_fail else str(
                    sample_now.get("conclusion_disposition") or "pass"
                )
                samples = merge_sample(
                    job.get("gospd_sample_results") or {},
                    chain_id=active,
                    patch={
                        "conclusion_confirmed": True,
                        "conclusion_confirm_sig": sig,
                        "conclusion_disposition": disposition,
                    },
                )
                job["gospd_sample_results"] = samples
                job["conclusion_confirmed"] = all_chains_conclusion_confirmed(job)
                job["conclusion_confirm_sig"] = sig
                for k, v in mirror_sample_to_job_fields(get_sample(job, active)).items():
                    job[k] = v
                job["updated_at"] = _utc_now()
                self._persist(job)
                return copy.deepcopy(job)

            unacked = int(trace.get("unacked_blocking_count") or 0)
            if unacked > 0:
                raise ValueError(
                    f"还有 {unacked} 项不通过结论未确认："
                    "请在汇总页点开追溯，确认为单据问题，或改字段后重测"
                )

            if STEP_RELATIONS in required and not job.get("matching_confirmed"):
                raise ValueError("请先完成匹配确认（Gate4）")
            missing = [
                step_id
                for step_id, key in _STEP_RESULT_KEYS.items()
                if step_id in required and not job.get(key)
            ]
            if missing:
                raise ValueError(
                    "本次底稿尚缺必做测试结果，请先完成：" + "、".join(missing)
                )
            evidence = job.get("evidence") if STEP_EVIDENCE in required else None
            amount = job.get("amount_test") if STEP_AMOUNT in required else None
            contract = (
                job.get("contract_terms") if STEP_CONTRACT in required else None
            )
            three_way = (
                job.get("three_way") if STEP_THREE_WAY in required else None
            )
            if not any([evidence, amount, contract, three_way]):
                raise ValueError("还没有可确认的测试结论")
            sig = conclusion_signature(
                evidence=evidence,
                amount=amount,
                contract=contract,
                three_way=three_way,
            )
            job["conclusion_confirmed"] = True
            job["conclusion_confirm_sig"] = sig
            job["updated_at"] = _utc_now()
            self._persist(job)
            return copy.deepcopy(job)

    def invalidate_downstream_from_evidence(self, job_id: str) -> None:
        """证据匹配重跑：失效当前笔 Gate4/5 与下游测试。"""
        self.invalidate_by_targets(
            job_id,
            ["evidence", "amount", "terms", "cutoff", "three_way", "gate5", "workbook"],
        )

    def invalidate_by_targets(
        self,
        job_id: str,
        targets: list[str] | tuple[str, ...] | set[str],
        *,
        expand_cascade: bool = True,
    ) -> list[str]:
        """按 advisory invalidates 定向失效；默认向上游→下游级联扩展。

        返回实际生效的 target 列表（含级联）。
        """
        raw = {str(t or "").strip().lower() for t in (targets or []) if t}
        expanded = set(raw)
        if expand_cascade:
            if "fields" in expanded:
                expanded |= {
                    "evidence",
                    "amount",
                    "cutoff",
                    "terms",
                    "three_way",
                    "gate5",
                    "workbook",
                }
            if "evidence" in expanded:
                expanded |= {
                    "amount",
                    "cutoff",
                    "terms",
                    "three_way",
                    "gate5",
                    "workbook",
                }
            if expanded & {"amount", "terms", "cutoff", "three_way"}:
                expanded |= {"gate5", "workbook"}
            if "gate5" in expanded:
                expanded |= {"workbook"}

        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)

            if "fields" in expanded:
                job["fields_confirmed"] = False
                job["fields_confirm_sig"] = None

            if "evidence" in expanded or "fields" in expanded:
                job["matching_confirmed"] = False
                job["matching_confirm_sig"] = None
                if "evidence" in expanded or "fields" in expanded:
                    if "fields" in expanded:
                        job["evidence"] = None
                        job["relations"] = []
                        job["duplicates"] = {}
                    else:
                        # 仅证据脏：保留 relations 供对照，但 Gate4 必须重做
                        job["matching_confirmed"] = False
                        job["matching_confirm_sig"] = None

            if "amount" in expanded:
                job["amount_test"] = None
            if "terms" in expanded:
                job["contract_terms"] = None
            if "cutoff" in expanded or "three_way" in expanded:
                clear_three_way_fields(job)
            # 重测/改字段后：非 GOSPD 清空全部确认；GOSPD 只作废当前笔
            if expanded & {
                "fields",
                "evidence",
                "amount",
                "terms",
                "cutoff",
                "three_way",
            }:
                if is_gospd_mode(job) and job.get("active_chain_id"):
                    from src.workflow.conclusion_trace import prune_acknowledgements_for_chain

                    job["finding_acknowledgements"] = prune_acknowledgements_for_chain(
                        job.get("finding_acknowledgements"),
                        str(job.get("active_chain_id")),
                    )
                else:
                    job["finding_acknowledgements"] = {}
            if "gate5" in expanded:
                job["conclusion_confirmed"] = False
                job["conclusion_confirm_sig"] = None
            if "workbook" in expanded:
                job["workbook_path"] = None
                job["workbook_paths"] = []

            if is_gospd_mode(job):
                active = str(job.get("active_chain_id") or "")
                samples = dict(job.get("gospd_sample_results") or {})
                if active and active in samples:
                    cur = dict(samples[active])
                    if "fields" in expanded:
                        cur["fields_confirmed"] = False
                        cur["fields_confirm_sig"] = None
                        cur.pop("matching_confirmed", None)
                        cur.pop("matching_confirm_sig", None)
                        cur.pop("conclusion_confirmed", None)
                        cur.pop("conclusion_confirm_sig", None)
                    if "evidence" in expanded or "fields" in expanded:
                        for k in (
                            "matching_confirmed",
                            "matching_confirm_sig",
                        ):
                            cur.pop(k, None)
                        if "fields" in expanded:
                            cur.pop("evidence", None)
                            cur.pop("relations", None)
                            cur.pop("duplicates", None)
                    if "amount" in expanded:
                        cur.pop("amount_test", None)
                    if "terms" in expanded:
                        cur.pop("contract_terms", None)
                    if "cutoff" in expanded or "three_way" in expanded:
                        clear_three_way_fields(cur)
                    if "gate5" in expanded:
                        cur["conclusion_confirmed"] = False
                        cur["conclusion_confirm_sig"] = None
                    samples[active] = cur
                    job["gospd_sample_results"] = samples

            job["updated_at"] = _utc_now()
            self._persist(job)
            return sorted(expanded)

    def require_step(self, job_id: str, step_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if not job:
            raise KeyError(job_id)
        if step_id == "goals":
            return job
        required = set((job.get("plan") or {}).get("required_steps") or [])
        if step_id not in required:
            raise ValueError(f"步骤不在本次底稿计划内: {step_id}")
        return job

    def require_fields_confirmed(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if not job:
            raise KeyError(job_id)
        classified = list(job.get("classified") or [])
        if is_gospd_mode(job):
            active = resolve_active_chain_id(job) or ""
            sample = get_sample(job, active) if active else {}
            fields_ok = sample.get("fields_confirmed")
            if fields_ok is None:
                fields_ok = bool(
                    sample.get("matching_confirmed")
                    or sample.get("evidence")
                    or sample.get("three_way")
                    or sample.get("amount_test")
                    or sample.get("contract_terms")
                    or sample.get("conclusion_confirmed")
                    or job.get("fields_confirmed")
                )
            if not fields_ok:
                raise ValueError(
                    f"请先完成当前笔（{active or '-'}）字段确认（Gate3）"
                )
            # 分笔：只校验当前笔单据签名，改另一笔不得打回本笔
            chain_docs = docs_for_chain(classified, active) if active else classified
            sig = fields_signature(chain_docs)
            confirmed_sig = sample.get("fields_confirm_sig") or job.get("fields_confirm_sig")
            if confirmed_sig != sig:
                with self._lock:
                    j = self._jobs.get(job_id)
                    if j:
                        j["fields_confirmed"] = False
                        j["fields_confirm_sig"] = None
                        j["matching_confirmed"] = False
                        j["matching_confirm_sig"] = None
                        j["conclusion_confirmed"] = False
                        j["conclusion_confirm_sig"] = None
                        j["amount_test"] = None
                        j["contract_terms"] = None
                        clear_three_way_fields(j)
                        j["workbook_path"] = None
                        j["workbook_paths"] = []
                        samples = dict(j.get("gospd_sample_results") or {})
                        if active and active in samples:
                            cur = dict(samples[active])
                            cur["fields_confirmed"] = False
                            cur["fields_confirm_sig"] = None
                            cur.pop("matching_confirmed", None)
                            cur.pop("matching_confirm_sig", None)
                            cur.pop("amount_test", None)
                            cur.pop("contract_terms", None)
                            clear_three_way_fields(cur)
                            samples[active] = cur
                            j["gospd_sample_results"] = samples
                        j["updated_at"] = _utc_now()
                        self._persist(j)
                raise ValueError(
                    f"当前笔（{active or '-'}）字段相对确认时已变化，请重新确认"
                )
            return job

        if not job.get("fields_confirmed"):
            raise ValueError("请先完成字段确认（Gate3）")
        sig = fields_signature(classified)
        if job.get("fields_confirm_sig") != sig:
            # 软失效：保留证据/关系（Gate4 仍可点），但下游测试结论必须作废，避免脏 three_way 直通 Gate5
            with self._lock:
                j = self._jobs.get(job_id)
                if j:
                    j["fields_confirmed"] = False
                    j["fields_confirm_sig"] = None
                    j["matching_confirmed"] = False
                    j["matching_confirm_sig"] = None
                    j["conclusion_confirmed"] = False
                    j["conclusion_confirm_sig"] = None
                    j["amount_test"] = None
                    j["contract_terms"] = None
                    clear_three_way_fields(j)
                    j["workbook_path"] = None
                    j["workbook_paths"] = []
                    j["updated_at"] = _utc_now()
                    self._persist(j)
            raise ValueError("字段相对确认时已变化，请重新确认")
        return job

    def require_matching_confirmed(self, job_id: str) -> dict[str, Any]:
        job = self.require_fields_confirmed(job_id)
        plan = job.get("plan") or {}
        if "evidence_match" not in (plan.get("required_steps") or []) and (
            "relations_gate4" not in (plan.get("required_steps") or [])
        ):
            return job
        if is_gospd_mode(job):
            active = resolve_active_chain_id(job) or ""
            sample = get_sample(job, active) if active else {}
            match_ok = sample.get("matching_confirmed")
            if match_ok is None:
                match_ok = bool(job.get("matching_confirmed"))
            if not match_ok:
                raise ValueError(
                    f"请先完成当前笔（{active or '-'}）匹配确认（Gate4）"
                )
            # 分笔：用顶层关系签名对照本笔确认签名（证据写在 sample 时回退顶层）
            evidence = sample.get("evidence") if isinstance(sample.get("evidence"), dict) else None
            if evidence is None and isinstance(job.get("evidence"), dict):
                evidence = job.get("evidence")
            relations = list(sample.get("relations") or job.get("relations") or [])
            duplicates = (
                sample.get("duplicates")
                if isinstance(sample.get("duplicates"), dict)
                else job.get("duplicates")
            )
            sig = matching_signature(
                evidence=evidence if isinstance(evidence, dict) else None,
                relations=relations,
                duplicates=duplicates if isinstance(duplicates, dict) else None,
            )
            confirmed_sig = sample.get("matching_confirm_sig") or job.get(
                "matching_confirm_sig"
            )
            if confirmed_sig != sig:
                with self._lock:
                    j = self._jobs.get(job_id)
                    if j:
                        j["matching_confirmed"] = False
                        j["matching_confirm_sig"] = None
                        samples = dict(j.get("gospd_sample_results") or {})
                        if active and active in samples:
                            cur = dict(samples[active])
                            cur["matching_confirmed"] = False
                            cur["matching_confirm_sig"] = None
                            samples[active] = cur
                            j["gospd_sample_results"] = samples
                        j["updated_at"] = _utc_now()
                        self._persist(j)
                raise ValueError(
                    f"当前笔（{active or '-'}）匹配结果相对确认时已变化，请重新确认 Gate4"
                )
            pending = pending_proposed(relations)
            if pending:
                raise ValueError(f"还有 {len(pending)} 条关系待处理")
            return job

        if not job.get("matching_confirmed"):
            raise ValueError("请先完成匹配确认（Gate4）")
        sig = matching_signature(
            evidence=job.get("evidence") if isinstance(job.get("evidence"), dict) else None,
            relations=job.get("relations") or [],
            duplicates=job.get("duplicates") if isinstance(job.get("duplicates"), dict) else None,
        )
        if job.get("matching_confirm_sig") != sig:
            with self._lock:
                j = self._jobs.get(job_id)
                if j:
                    j["matching_confirmed"] = False
                    j["matching_confirm_sig"] = None
            raise ValueError("匹配结果相对确认时已变化，请重新确认 Gate4")
        pending = pending_proposed(job.get("relations") or [])
        if pending:
            raise ValueError(f"还有 {len(pending)} 条关系待处理")
        return job


JOB_STORE = JobStore()

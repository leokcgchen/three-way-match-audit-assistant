"""One auditable export-readiness view for the controlled review workflow.

This module deliberately does not mutate a job.  It translates persisted
workflow facts into the exact, human-readable actions that still block a
workpaper export.  The same result powers the UI and the export endpoint so
the button can never disagree with the explanation shown to an auditor.

Action steps are mapped to this repo's existing shell (sample_desk / field_confirm /
relations_gate4 / three_way_cutoff / conclusion_gate5), not the colleague share's
alternate navigation.
"""

from __future__ import annotations

from typing import Any

from src.audit.workpaper_notes import blocking_advisory_for_export
from src.workflow.chain_workspace import (
    all_chains_conclusion_confirmed,
    get_sample,
    is_gospd_mode,
    list_business_chains,
    sample_matching_ok,
)


def _stage(
    stage_id: str,
    label: str,
    *,
    status: str,
    reason: str,
    action_step: str | None = None,
    action_label: str | None = None,
    affected_groups: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": stage_id,
        "label": label,
        "status": status,
        "blocking": status != "DONE",
        "reason": reason,
        "action": (
            {"step": action_step, "label": action_label or "去处理"}
            if action_step
            else None
        ),
        "affected_groups": affected_groups or [],
    }


def _three_way_blob(sample: dict[str, Any]) -> dict[str, Any]:
    twm = sample.get("three_way_match")
    if isinstance(twm, dict) and twm:
        return twm
    legacy = sample.get("three_way") or {}
    if not isinstance(legacy, dict):
        return {}
    return {
        "status": legacy.get("three_way_status")
        or legacy.get("overall_status")
        or legacy.get("status"),
        "summary": legacy.get("three_way_summary")
        or legacy.get("summary")
        or legacy.get("human_readable_summary"),
        "document_binding": legacy.get("document_binding") or {},
        "field_consistency": legacy.get("field_consistency") or {},
        "decision_trace": legacy.get("decision_trace") or [],
        "failure_category": legacy.get("three_way_failure_category"),
    }


def _cutoff_blob(sample: dict[str, Any]) -> dict[str, Any]:
    cut = sample.get("cutoff_test")
    if isinstance(cut, dict) and cut:
        return cut
    legacy = sample.get("three_way") or {}
    if not isinstance(legacy, dict):
        return {}
    return {
        "status": legacy.get("cutoff_status")
        or (
            (legacy.get("cutoff_result") or {}).get("测试状态")
            if isinstance(legacy.get("cutoff_result"), dict)
            else None
        )
        or ("SKIPPED" if not legacy.get("cutoff_available", True) else None),
        "summary": legacy.get("cutoff_summary")
        or legacy.get("cutoff_skipped_reason")
        or "",
        "result": legacy.get("cutoff_result"),
        "available": bool(legacy.get("cutoff_available")),
        "skipped_reason": legacy.get("cutoff_skipped_reason"),
    }


def _ok_status(status: Any) -> bool:
    # WARNING 可见但默认不挡导出；FAIL 须经 Gate5 放行（见 build_export_readiness）。
    return str(status or "").upper() in {"PASS", "NOT_TESTED", "SKIPPED", "OK", "WARNING"}


def build_export_readiness(job: dict[str, Any]) -> dict[str, Any]:
    """Return a single source of truth for all export prerequisites."""
    docs = list(job.get("classified") or [])
    gospd = is_gospd_mode(job)
    stages: list[dict[str, Any]] = []
    required = list((job.get("plan") or {}).get("required_steps") or [])
    # GOSPD 且计划未写步骤时，按官方目标默认全开，避免漏门禁
    default_on = bool(gospd and not required)
    need_three_way = ("three_way_cutoff" in required) if required else default_on
    need_gate4 = ("relations_gate4" in required) if required else default_on
    need_fields = ("field_confirm" in required) if required else (default_on or bool(docs))
    need_gate5 = ("conclusion_gate5" in required) if required else True

    if not docs:
        stages.append(
            _stage(
                "upload_ocr",
                "上传凭证",
                status="TODO",
                reason="尚未识别任何凭证，无法开始审阅。请先在工作台立笔，再传 PDF/图片。",
                action_step="sample_desk",
                action_label="回工作台 / 去上传凭证",
            )
        )
    else:
        issues = list(job.get("ocr_issues") or [])
        stages.append(
            _stage(
                "upload_ocr",
                "上传凭证",
                status="DONE" if not issues else "NEEDS_REVIEW",
                reason=(
                    f"已识别 {len(docs)} 份单据。"
                    if not issues
                    else f"已识别 {len(docs)} 份单据，但有 {len(issues)} 份需要处理识别问题。"
                ),
                action_step="sample_desk" if issues else None,
                action_label="查看识别问题",
            )
        )

    try:
        from src.workflow.packet_engine import packet_blocks_process

        if packet_blocks_process(job):
            stages.append(
                _stage(
                    "packet_unpack",
                    "混装拆包确认",
                    status="NEEDS_REVIEW",
                    reason="仍有混装扫描件未确认拆包；确认前不得识别、不得导出。",
                    action_step="packet_unpack",
                    action_label="去拆包分笔",
                )
            )
    except Exception:
        pass

    if not gospd:
        from src.workflow.amount_ambiguity import list_open_ambiguities

        open_amb = list_open_ambiguities(job, chain_id=None)
        stages.append(
            _stage(
                "amount_ambiguity",
                "金额歧义确认",
                status="DONE" if not open_amb else "NEEDS_REVIEW",
                reason=(
                    "无未关闭的金额歧义。"
                    if not open_amb
                    else f"还有 {len(open_amb)} 项金额歧义未关闭。"
                ),
                action_step=None if not open_amb else "field_confirm",
                action_label="确认金额候选",
            )
        )
        conclusion_ok = bool(job.get("conclusion_confirmed"))
        stages.append(
            _stage(
                "conclusion",
                "导出前复核",
                status="DONE" if conclusion_ok else "TODO",
                reason="已确认导出结论。" if conclusion_ok else "尚未确认导出结论。",
                action_step=None if conclusion_ok else "conclusion_gate5",
                action_label="完成导出前复核",
            )
        )
    else:
        groups = list_business_chains(docs)
        recognised = [g for g in groups if g.get("chain_id") != "未识别业务号"]
        unrecognised = [g for g in groups if g.get("chain_id") == "未识别业务号"]
        confirmations = dict(job.get("business_group_confirmations") or {})

        def _chain_conclusion_ok(cid: str) -> bool:
            sample = get_sample(job, cid)
            if sample.get("conclusion_confirmed"):
                return True
            return bool(job.get("conclusion_confirmed")) and len(recognised) <= 1

        if need_gate4:
            group_missing: list[str] = []
            for g in recognised:
                cid = str(g["chain_id"])
                if confirmations.get(cid) or sample_matching_ok(job, cid):
                    continue
                group_missing.append(cid)

            if not docs:
                stages.append(
                    _stage(
                        "business_group",
                        "业务分组 / 串单确认",
                        status="TODO",
                        reason="需先在工作台立笔，再上传并识别凭证。",
                        action_step="sample_desk",
                        action_label="回工作台 / 去上传凭证",
                    )
                )
            elif unrecognised:
                stages.append(
                    _stage(
                        "business_group",
                        "业务分组 / 串单确认",
                        status="NEEDS_REVIEW",
                        reason="存在未识别业务号的单据；请核对字段或调整归属后再串单确认。",
                        action_step="field_confirm",
                        action_label="核对字段与分组",
                        affected_groups=[str(g["chain_id"]) for g in unrecognised],
                    )
                )
            elif group_missing:
                stages.append(
                    _stage(
                        "business_group",
                        "业务分组 / 串单确认",
                        status="TODO",
                        reason=f"还有 {len(group_missing)} 个业务组未完成串单确认。",
                        action_step="field_confirm",
                        action_label="去串单确认",
                        affected_groups=group_missing,
                    )
                )
            else:
                stages.append(
                    _stage(
                        "business_group",
                        "业务分组 / 串单确认",
                        status="DONE",
                        reason=f"{len(recognised)} 个业务组均已串单确认。",
                    )
                )

        if need_fields:
            fields_missing = [
                str(g["chain_id"])
                for g in recognised
                if not bool(get_sample(job, str(g["chain_id"])).get("fields_confirmed"))
            ]
            if fields_missing and job.get("fields_confirmed") and len(recognised) <= 1:
                fields_missing = []
            stages.append(
                _stage(
                    "field_mapping",
                    "字段映射与一致性",
                    status="DONE" if docs and not fields_missing else "TODO",
                    reason=(
                        "每个业务组的字段来源与映射均已人工确认。"
                        if docs and not fields_missing
                        else f"还有 {len(fields_missing)} 个业务组未确认字段映射。"
                    ),
                    action_step=None if docs and not fields_missing else "field_confirm",
                    action_label="核对字段映射",
                    affected_groups=fields_missing,
                )
            )

        # 金额歧义：当前全部业务组合计（与字段确认同页处理）
        from src.workflow.amount_ambiguity import list_open_ambiguities

        open_all: list[dict[str, Any]] = []
        for g in recognised:
            open_all.extend(list_open_ambiguities(job, chain_id=str(g["chain_id"])))
        # 去重 by ambiguity_id
        seen_ids: set[str] = set()
        unique_open: list[dict[str, Any]] = []
        for row in open_all:
            aid = str(row.get("ambiguity_id") or "")
            if aid and aid in seen_ids:
                continue
            if aid:
                seen_ids.add(aid)
            unique_open.append(row)
        if unique_open:
            stages.append(
                _stage(
                    "amount_ambiguity",
                    "金额歧义确认",
                    status="NEEDS_REVIEW",
                    reason=f"还有 {len(unique_open)} 项金额歧义未关闭（多候选或勾稽失败）。",
                    action_step="field_confirm",
                    action_label="确认金额候选",
                    affected_groups=sorted(
                        {
                            str(g["chain_id"])
                            for g in recognised
                            if list_open_ambiguities(job, chain_id=str(g["chain_id"]))
                        }
                    ),
                )
            )
        else:
            stages.append(
                _stage(
                    "amount_ambiguity",
                    "金额歧义确认",
                    status="DONE",
                    reason="无未关闭的金额歧义。",
                )
            )

        if need_three_way:
            three_way_missing: list[str] = []
            three_way_fail: list[str] = []
            three_way_released: list[str] = []
            cutoff_missing: list[str] = []
            cutoff_fail: list[str] = []
            cutoff_released: list[str] = []
            for group in recognised:
                group_id = str(group["chain_id"])
                sample = get_sample(job, group_id)
                three_way = _three_way_blob(sample)
                cutoff = _cutoff_blob(sample)
                released = _chain_conclusion_ok(group_id)

                if not three_way or three_way.get("status") in (None, ""):
                    three_way_missing.append(group_id)
                elif not _ok_status(three_way.get("status")):
                    if released:
                        three_way_released.append(group_id)
                    else:
                        three_way_fail.append(group_id)

                cut_status = cutoff.get("status") if cutoff else None
                if not cutoff or cut_status in (None, ""):
                    if sample.get("three_way"):
                        legacy_cut = str(
                            (sample.get("three_way") or {}).get("cutoff_status") or ""
                        ).upper()
                        if legacy_cut in {"PASS", "NOT_TESTED", "SKIPPED", "WARNING"}:
                            pass
                        elif legacy_cut == "FAIL":
                            (cutoff_released if released else cutoff_fail).append(group_id)
                        else:
                            cutoff_missing.append(group_id)
                    else:
                        cutoff_missing.append(group_id)
                elif not _ok_status(cut_status):
                    if released:
                        cutoff_released.append(group_id)
                    else:
                        cutoff_fail.append(group_id)

            if three_way_missing:
                stages.append(
                    _stage(
                        "three_way",
                        "三单匹配复核",
                        status="TODO",
                        reason=f"还有 {len(three_way_missing)} 个业务组尚未执行三单匹配。",
                        action_step="sample_desk",
                        action_label="执行三单匹配",
                        affected_groups=three_way_missing,
                    )
                )
            elif three_way_fail:
                stages.append(
                    _stage(
                        "three_way",
                        "三单匹配复核",
                        status="NEEDS_REVIEW",
                        reason=(
                            f"{len(three_way_fail)} 个业务组三单匹配不通过；"
                            "请复核后在确认结论页放行。"
                        ),
                        action_step="conclusion_gate5",
                        action_label="查看三单匹配原因",
                        affected_groups=three_way_fail,
                    )
                )
            else:
                note = (
                    f"全部业务组均已执行三单匹配"
                    + (
                        f"（其中 {len(three_way_released)} 组不通过已由结论放行）"
                        if three_way_released
                        else "。"
                    )
                )
                if not note.endswith("。"):
                    note += "。"
                stages.append(_stage("three_way", "三单匹配复核", status="DONE", reason=note))

            if cutoff_missing:
                stages.append(
                    _stage(
                        "cutoff",
                        "截止性测试",
                        status="TODO",
                        reason=f"还有 {len(cutoff_missing)} 个业务组尚未执行截止性测试。",
                        action_step="sample_desk",
                        action_label="执行截止性测试",
                        affected_groups=cutoff_missing,
                    )
                )
            elif cutoff_fail:
                stages.append(
                    _stage(
                        "cutoff",
                        "截止性测试",
                        status="NEEDS_REVIEW",
                        reason=(
                            f"{len(cutoff_fail)} 个业务组截止性测试不通过；"
                            "请复核后在确认结论页放行。"
                        ),
                        action_step="conclusion_gate5",
                        action_label="查看截止性测试原因",
                        affected_groups=cutoff_fail,
                    )
                )
            else:
                note = (
                    f"全部业务组均已执行截止性测试"
                    + (
                        f"（其中 {len(cutoff_released)} 组不通过已由结论放行）"
                        if cutoff_released
                        else "。"
                    )
                )
                if not note.endswith("。"):
                    note += "。"
                stages.append(_stage("cutoff", "截止性测试", status="DONE", reason=note))

        if need_gate5:
            conclusion_ok = all_chains_conclusion_confirmed(job)
            missing_conclusions = [
                str(g["chain_id"])
                for g in recognised
                if not bool(get_sample(job, str(g["chain_id"])).get("conclusion_confirmed"))
            ]
            pop = job.get("sample_population") if isinstance(job.get("sample_population"), dict) else None
            pop_ids = [str(x) for x in (pop.get("business_ids") or []) if x] if pop else []
            if pop_ids:
                from src.workflow.chain_workspace import docs_for_chain as _docs_for_chain

                classified = list(job.get("classified") or [])
                for cid in pop_ids:
                    if cid in missing_conclusions:
                        continue
                    if not _docs_for_chain(classified, cid) or not get_sample(job, cid).get(
                        "conclusion_confirmed"
                    ):
                        missing_conclusions.append(cid)
                conclusion_ok = conclusion_ok and not missing_conclusions
            if (
                missing_conclusions
                and not pop_ids
                and job.get("conclusion_confirmed")
                and len(recognised) <= 1
            ):
                missing_conclusions = []
                conclusion_ok = True
            waiting_docs = [
                cid
                for cid in missing_conclusions
                if not _docs_for_chain(list(job.get("classified") or []), cid)
            ] if pop_ids else []
            stages.append(
                _stage(
                    "conclusion",
                    "导出前复核",
                    status="DONE" if conclusion_ok and not missing_conclusions else "TODO",
                    reason=(
                        "清单笔均已收口，可以出底稿。"
                        if conclusion_ok and not missing_conclusions
                        else (
                            f"清单还有 {len(missing_conclusions)} 笔未收口"
                            + (f"（其中 {len(waiting_docs)} 笔待传凭证）" if waiting_docs else "")
                            + "。"
                        )
                    ),
                    action_step=None
                    if conclusion_ok and not missing_conclusions
                    else ("sample_desk" if waiting_docs else "conclusion_gate5"),
                    action_label="回工作台收口清单" if waiting_docs else "完成导出前复核",
                    affected_groups=missing_conclusions,
                )
            )

    # GOSPD01030：报告期末日是 V 公式与期间断言的共用锚点，未配置则禁止正式导出
    goal_ids = [
        str(x)
        for x in (
            (job.get("plan") or {}).get("goal_ids") or job.get("goal_ids") or []
        )
        if x
    ]
    if "gospd01030" in goal_ids:
        pe = (
            job.get("period_end")
            or (job.get("plan") or {}).get("period_end")
            or job.get("cutoff_period_end")
        )
        pe_ok = bool(str(pe or "").strip())
        stages.append(
            _stage(
                "period_end",
                "报告期末日",
                status="DONE" if pe_ok else "TODO",
                reason=(
                    f"已配置报告期末日 {str(pe).strip()}。"
                    if pe_ok
                    else "未配置报告期末日（period_end）；GOSPD01030 的 M5/V 公式与期间断言无法对齐，禁止正式导出。"
                ),
                action_step=None if pe_ok else "sample_desk",
                action_label=None if pe_ok else "去配置报告期末",
            )
        )

    pending = list(blocking_advisory_for_export(job))
    stages.append(
        _stage(
            "advisory",
            "AI 候选观察",
            status="DONE",
            reason=(
                "无字段类待决顾问；其它候选不挡导出，仅作旁注观察。"
                if not pending
                else f"还有 {len(pending)} 条字段类顾问未消化（请先人工核对）。"
            ),
            # 字段类未消化时仍提示去字段页，但不把整段 advisory 设为 NEEDS_REVIEW 挡导出：
            # blocking 由 status!=DONE 决定；此处保持 DONE，字段门禁由 field_mapping 阶段负责。
            action_step=None,
            action_label=None,
        )
    )

    blocking = [stage for stage in stages if stage["blocking"]]
    lights: dict[str, Any] = {"green": 0, "yellow": 0, "red": 0, "wait": 0, "issues": [], "request_docs": []}
    if gospd:
        try:
            from src.workflow.sample_desk import build_desk_overview

            lights = build_desk_overview(job).get("lights") or lights
        except Exception:
            pass
    return {
        "schema_version": "1.1",
        "ready": not blocking,
        "summary": "已满足底稿生成条件。" if not blocking else f"底稿生成前还需处理 {len(blocking)} 项。",
        "blocked_count": len(blocking),
        "stages": stages,
        "lights": lights,
    }

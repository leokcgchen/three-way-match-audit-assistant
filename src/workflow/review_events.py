"""统一人工复核事件投影。

本模块只读取现有 Job 事实并生成可重复的事件视图，不复制工作流状态，
不把显示文案写回任务。人工决定仍由受控领域动作执行。
"""

from __future__ import annotations

from hashlib import sha256
from typing import Any, Iterable


OPEN_STATE = "OPEN"
SEVERITY_ORDER = {"BLOCKING": 0, "REVIEW": 1, "SAMPLE": 2}


def _event_id(
    job_id: str,
    chain_id: str,
    event_type: str,
    source_ref: str,
) -> str:
    raw = "|".join((job_id, chain_id, event_type, source_ref))
    return "evt_" + sha256(raw.encode("utf-8")).hexdigest()[:20]


def _event(
    job: dict[str, Any],
    *,
    chain_id: str,
    event_type: str,
    severity: str,
    title: str,
    reason: str,
    action_kind: str,
    action_step: str,
    source_ref: str,
    evidence: dict[str, Any] | None = None,
    ledger_value: Any = None,
    observed_value: Any = None,
    ai_suggestion: Any = None,
    confidence: float | None = None,
    invalidates: Iterable[str] | None = None,
) -> dict[str, Any]:
    job_id = str(job.get("job_id") or "")
    return {
        "event_id": _event_id(job_id, chain_id, event_type, source_ref),
        "chain_id": chain_id,
        "event_type": event_type,
        "severity": severity,
        "state": OPEN_STATE,
        "title": title,
        "reason": reason,
        "evidence": dict(evidence or {}),
        "ledger_value": ledger_value,
        "observed_value": observed_value,
        "ai_suggestion": ai_suggestion,
        "confidence": confidence,
        "action_kind": action_kind,
        "action_step": action_step,
        "source_ref": source_ref,
        "invalidates": [str(value) for value in (invalidates or []) if value],
    }


def events_for_desk_row(
    job: dict[str, Any],
    row: dict[str, Any],
) -> list[dict[str, Any]]:
    """把一行业务仓库状态转换为至多一个主事件。

    desk_row_status 已按缺件、歧义、字段、测试的业务优先级选出最先应处理
    的问题；这里保持该顺序，避免同一事实生成多个重复事件。
    """
    chain_id = str(row.get("chain_id") or "")
    reason_code = str(row.get("reason") or "")
    label = str(row.get("label") or reason_code or "需要处理")
    source_ref = f"desk:{chain_id}:{reason_code}"

    if reason_code in {"wait_docs", "missing_docs"}:
        missing = [
            str(value)
            for value in (row.get("missing_doc_labels") or [])
            if value
        ]
        reason = label if missing else "该业务尚未上传可识别凭证。"
        return [
            _event(
                job,
                chain_id=chain_id,
                event_type="MISSING_DOCUMENT",
                severity="BLOCKING",
                title="缺少业务凭证",
                reason=reason,
                action_kind="UPLOAD_EVIDENCE",
                action_step="sample_desk",
                source_ref=source_ref,
                evidence={
                    "missing_doc_types": missing,
                    "request_docs": list(row.get("request_docs") or []),
                },
            )
        ]

    if reason_code in {"amount_ambiguity", "docs_uncertain", "fields_gap"}:
        event_type = (
            "RELATIONSHIP_AMBIGUITY"
            if reason_code == "docs_uncertain"
            else "LOW_CONFIDENCE"
        )
        return [
            _event(
                job,
                chain_id=chain_id,
                event_type=event_type,
                severity="REVIEW",
                title="需要确认单据或字段",
                reason=label,
                action_kind="REVIEW_FIELD",
                action_step="field_confirm",
                source_ref=source_ref,
                evidence={
                    "missing_fields": list(row.get("missing_fields") or []),
                    "missing_labels": list(row.get("missing_labels") or []),
                },
            )
        ]

    if reason_code == "test_fail":
        return [
            _event(
                job,
                chain_id=chain_id,
                event_type="AUDIT_TEST_FAILED",
                severity="BLOCKING",
                title="审计测试未通过",
                reason=label,
                action_kind="DECIDE_FINDING",
                action_step="conclusion_gate5",
                source_ref=source_ref,
                evidence={"diff_lines": list(row.get("diff_lines") or [])},
                invalidates=["gate5", "workbook"],
            )
        ]

    return []


def _advisory_events(job: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for candidate in job.get("advisory_candidates") or []:
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("status") or "").upper() != "PROPOSED":
            continue
        task_type = str(candidate.get("task_type") or "").upper()
        chain_id = str(candidate.get("business_id") or "")
        payload = (
            candidate.get("payload")
            if isinstance(candidate.get("payload"), dict)
            else {}
        )
        evidence = (
            candidate.get("evidence")
            if isinstance(candidate.get("evidence"), dict)
            else {}
        )
        if task_type == "MATCHING_DISAMBIGUATION":
            event_type = "RELATIONSHIP_AMBIGUITY"
            title = "业务关系需要确认"
        elif task_type == "FIELD_GAP_FILL":
            event_type = "LOW_CONFIDENCE"
            title = "关键字段需要确认"
        else:
            event_type = "LOW_CONFIDENCE"
            title = "AI 建议需要确认"
        source_ref = "advisory:" + str(
            candidate.get("candidate_id") or candidate.get("fingerprint") or task_type
        )
        confidence_raw = payload.get("confidence")
        try:
            confidence = (
                float(confidence_raw) if confidence_raw not in (None, "") else None
            )
        except (TypeError, ValueError):
            confidence = None
        out.append(
            _event(
                job,
                chain_id=chain_id,
                event_type=event_type,
                severity="BLOCKING" if task_type == "FIELD_GAP_FILL" else "REVIEW",
                title=title,
                reason=str(
                    candidate.get("note")
                    or evidence.get("excerpt")
                    or "AI 候选尚未由审计师确认。"
                ),
                action_kind="DECIDE_ADVISORY",
                action_step="field_confirm",
                source_ref=source_ref,
                evidence={
                    **evidence,
                    "candidate_id": candidate.get("candidate_id"),
                    "field_name": payload.get("field_name"),
                    "file_name": payload.get("file_name") or evidence.get("source_doc"),
                },
                ai_suggestion=payload.get("normalized_candidate")
                or payload.get("suggested_biz_id")
                or payload.get("value"),
                confidence=confidence,
                invalidates=candidate.get("invalidates") or [],
            )
        )
    return out


def _document_events(job: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for doc in job.get("classified") or []:
        if not isinstance(doc, dict):
            continue
        chain_ids = [
            str(value)
            for value in (
                doc.get("declared_business_ids")
                or ([doc.get("chain_id")] if doc.get("chain_id") else [])
            )
            if value
        ]
        chain_id = chain_ids[0] if len(chain_ids) == 1 else ""
        file_name = str(doc.get("file_name") or "")
        if doc.get("ledger_evaluated") and doc.get("ledger_match_ok") is False:
            out.append(
                _event(
                    job,
                    chain_id=chain_id,
                    event_type="LEDGER_MISMATCH",
                    severity="BLOCKING",
                    title="单据与账载信息不一致",
                    reason=str(doc.get("ledger_match_message") or "账载匹配失败。"),
                    action_kind="REVIEW_FIELD",
                    action_step="field_confirm",
                    source_ref=f"ledger:{file_name}",
                    evidence={"file_name": file_name, "field_name": "totalAmount"},
                    ledger_value=doc.get("ledger_amount"),
                    observed_value=(doc.get("fields") or {}).get("totalAmount"),
                )
            )
        if doc.get("error"):
            out.append(
                _event(
                    job,
                    chain_id=chain_id,
                    event_type="PROVENANCE_GAP",
                    severity="BLOCKING",
                    title="识别证据不完整",
                    reason=str(doc.get("error")),
                    action_kind="REVIEW_EVIDENCE",
                    action_step="field_confirm",
                    source_ref=f"ocr:{file_name}",
                    evidence={"file_name": file_name},
                )
            )
    return out


def _rule_conflict_events(job: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    duplicates = job.get("duplicates") if isinstance(job.get("duplicates"), dict) else {}
    for finding in duplicates.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        severity = str(finding.get("severity") or "").upper()
        if severity not in {"FAIL_SIGNAL", "WARNING"}:
            continue
        finding_id = str(finding.get("finding_id") or finding.get("issue_type") or "duplicate")
        chain_id = str(finding.get("chain_id") or finding.get("biz_id") or "")
        out.append(
            _event(
                job,
                chain_id=chain_id,
                event_type="RULE_CONFLICT",
                severity="BLOCKING" if severity == "FAIL_SIGNAL" else "REVIEW",
                title=str(finding.get("title") or "规则发现冲突"),
                reason=str(finding.get("note") or "同批资料存在需要人工处理的规则冲突。"),
                action_kind="DECIDE_FINDING",
                action_step="relation_review",
                source_ref=f"duplicate:{finding_id}",
                evidence={
                    "finding_id": finding_id,
                    "issue_type": finding.get("issue_type"),
                    "file_names": list(finding.get("file_names") or []),
                },
                invalidates=["gate4", "gate5", "workbook"],
            )
        )
    return out


def _ocr_issue_events(job: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, issue in enumerate(job.get("ocr_issues") or []):
        if not isinstance(issue, dict):
            continue
        file_name = str(issue.get("file_name") or issue.get("source_file") or "")
        page_no = issue.get("page_no") or issue.get("page")
        issue_id = str(issue.get("issue_id") or f"{file_name}:{page_no or index}")
        out.append(
            _event(
                job,
                chain_id=str(issue.get("chain_id") or issue.get("business_id") or ""),
                event_type="PROVENANCE_GAP",
                severity="BLOCKING",
                title="识别证据不完整",
                reason=str(issue.get("message") or issue.get("reason") or "原件无法完整追溯。"),
                action_kind="REVIEW_EVIDENCE",
                action_step="sample_desk",
                source_ref=f"ocr_issue:{issue_id}",
                evidence={**issue, "file_name": file_name, "page_no": page_no},
                invalidates=["gate3", "gate4", "gate5", "workbook"],
            )
        )
    return out


def _quality_sample_events(job: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, selection in enumerate(job.get("quality_sample_selections") or []):
        if not isinstance(selection, dict):
            continue
        selection_id = str(selection.get("selection_id") or index)
        out.append(
            _event(
                job,
                chain_id=str(selection.get("chain_id") or selection.get("business_id") or ""),
                event_type="QUALITY_SAMPLE",
                severity="SAMPLE",
                title="自动通过质量抽样",
                reason=str(selection.get("reason") or "该自动通过样本被选中进行质量复核。"),
                action_kind="REVIEW_SAMPLE",
                action_step="event_review",
                source_ref=str(selection.get("source_ref") or f"quality_sample:{selection_id}"),
                evidence=dict(selection),
            )
        )
    return out


def _deduplicate(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for event in events:
        event_id = str(event.get("event_id") or "")
        if not event_id:
            continue
        by_id[event_id] = event
    return sorted(
        by_id.values(),
        key=lambda row: (
            SEVERITY_ORDER.get(str(row.get("severity") or ""), 9),
            str(row.get("chain_id") or ""),
            str(row.get("event_id") or ""),
        ),
    )


def _same_resolved_fact(job: dict[str, Any], event: dict[str, Any]) -> bool:
    decisions = job.get("review_event_decisions") or {}
    if not isinstance(decisions, dict):
        return False
    record = decisions.get(str(event.get("event_id") or ""))
    if not isinstance(record, dict) or str(record.get("state") or "").upper() != "RESOLVED":
        return False
    previous = record.get("event")
    if not isinstance(previous, dict):
        return False
    fact_keys = (
        "chain_id",
        "event_type",
        "source_ref",
        "reason",
        "evidence",
        "ledger_value",
        "observed_value",
        "ai_suggestion",
    )
    return all(previous.get(key) == event.get(key) for key in fact_keys)


def build_review_events(job: dict[str, Any]) -> list[dict[str, Any]]:
    """返回当前 Job 需要人工处理的开放事件。"""
    from src.workflow.sample_desk import build_desk_chains

    events: list[dict[str, Any]] = []
    for row in build_desk_chains(job):
        projected = row.get("_review_events")
        if isinstance(projected, list):
            events.extend(projected)
        else:
            events.extend(events_for_desk_row(job, row))
    events.extend(_advisory_events(job))
    events.extend(_document_events(job))
    events.extend(_rule_conflict_events(job))
    events.extend(_ocr_issue_events(job))
    events.extend(_quality_sample_events(job))
    return [event for event in _deduplicate(events) if not _same_resolved_fact(job, event)]


def review_event_summary(events: Iterable[dict[str, Any]]) -> dict[str, int]:
    summary = {
        "open": 0,
        "blocking": 0,
        "review": 0,
        "sample": 0,
        "missing": 0,
        "passed": 0,
    }
    for event in events:
        state = str(event.get("state") or OPEN_STATE).upper()
        if state != OPEN_STATE:
            summary["passed"] += 1
            continue
        summary["open"] += 1
        severity = str(event.get("severity") or "").upper()
        if severity == "BLOCKING":
            summary["blocking"] += 1
        elif severity == "SAMPLE":
            summary["sample"] += 1
        else:
            summary["review"] += 1
        if str(event.get("event_type") or "").upper() == "MISSING_DOCUMENT":
            summary["missing"] += 1
    return summary

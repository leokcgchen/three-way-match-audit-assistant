"""Route unified review-event decisions to controlled domain actions."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from src.audit.hitl_log import append_hitl_event, current_operator
from src.workflow.job_store import JOB_STORE
from src.workflow.review_events import build_review_events


REASON_REQUIRED = {"OVERRIDE", "AUDIT_FAIL", "DOCUMENT_ISSUE"}
SUPPORTED_BY_ACTION = {
    "DECIDE_ADVISORY": {"ACCEPT_AI", "OVERRIDE", "MANUAL_VALUE"},
    "REVIEW_FIELD": {"ACCEPT_AI", "OVERRIDE", "MANUAL_VALUE"},
    "DECIDE_FINDING": {"AUDIT_FAIL", "DOCUMENT_ISSUE"},
    "REVIEW_EVIDENCE": {"DOCUMENT_ISSUE"},
    "REVIEW_SAMPLE": {"ACCEPT_AI", "AUDIT_FAIL", "DOCUMENT_ISSUE"},
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _event_or_error(job: dict[str, Any], event_id: str) -> dict[str, Any]:
    event = next(
        (row for row in build_review_events(job) if str(row.get("event_id")) == event_id),
        None,
    )
    if event is None:
        history = job.get("review_event_decisions") or {}
        record = history.get(event_id) if isinstance(history, dict) else None
        if isinstance(record, dict) and isinstance(record.get("event"), dict):
            raise ValueError("该事件已经完成裁决")
        raise ValueError("未找到待裁决事件")
    return event


def _candidate_id(event: dict[str, Any]) -> str:
    source_ref = str(event.get("source_ref") or "")
    return source_ref.split(":", 1)[1] if source_ref.startswith("advisory:") else ""


def _apply_advisory(
    job_id: str,
    event: dict[str, Any],
    decision: str,
    reason: str,
    operator: str,
) -> dict[str, Any]:
    from src.audit.gap_fill_replay import apply_advisory_decision

    candidate_id = _candidate_id(event)
    if not candidate_id:
        raise ValueError("事件缺少可裁决的 AI 候选")
    status = "VERIFIED" if decision == "ACCEPT_AI" else "REJECTED"
    return apply_advisory_decision(
        job_id,
        candidate_id,
        status,
        actor=operator,
        reason=reason or decision,
        auto_replay=False,
    )


def _apply_manual_value(
    job_id: str,
    event: dict[str, Any],
    value: Any,
) -> dict[str, Any]:
    from src.models.field_values import accept_field

    evidence = event.get("evidence") if isinstance(event.get("evidence"), dict) else {}
    file_name = str(evidence.get("file_name") or evidence.get("source_doc") or "")
    field_name = str(evidence.get("field_name") or "")
    if not file_name or not field_name:
        raise ValueError("该事件缺少人工值写回所需的文件或字段定位")
    job = JOB_STORE.get(job_id) or {}
    classified = deepcopy(job.get("classified") or [])
    target = next(
        (row for row in classified if str(row.get("file_name") or "") == file_name),
        None,
    )
    if target is None:
        raise ValueError(f"未找到人工值对应的原始文件：{file_name}")
    fields = dict(target.get("fields") or {})
    fields[field_name] = value
    target["fields"] = fields
    target["manual_edited"] = True
    accept_field(target, field_name, value, source="review_event", extractor="manual")
    JOB_STORE.update(job_id, classified=classified)
    expanded = JOB_STORE.invalidate_by_targets(
        job_id, list(event.get("invalidates") or ["fields"])
    )
    return {"expanded_invalidates": expanded, "replayed": []}


def _store_resolution(
    job_id: str,
    event: dict[str, Any],
    payload: dict[str, Any],
    *,
    operator: str,
    reason: str,
    replay: dict[str, Any],
) -> dict[str, Any]:
    job = JOB_STORE.get(job_id) or {}
    decisions = deepcopy(job.get("review_event_decisions") or {})
    event_id = str(event["event_id"])
    snapshot = deepcopy(event)
    snapshot["state"] = "RESOLVED"
    snapshot["resolved_at"] = _now()
    snapshot["operator"] = operator
    snapshot["decision"] = str(payload.get("decision") or "").upper()
    snapshot["decision_reason"] = reason
    record = {
        "event_id": event_id,
        "state": "RESOLVED",
        "decision": snapshot["decision"],
        "value": payload.get("value"),
        "reason": reason,
        "operator": operator,
        "decided_at": snapshot["resolved_at"],
        "before": event.get("observed_value"),
        "after": payload.get("value", event.get("ai_suggestion")),
        "invalidates": list(replay.get("expanded_invalidates") or event.get("invalidates") or []),
        "replayed": list(replay.get("replayed") or []),
        "event": snapshot,
    }
    decisions[event_id] = record
    JOB_STORE.update(job_id, review_event_decisions=decisions)
    return record


def apply_review_decision(
    job_id: str,
    event_id: str,
    decision: dict[str, Any],
) -> dict[str, Any]:
    """Apply one auditable decision without bypassing evidence-upload gates."""
    job = JOB_STORE.get(job_id)
    if not job:
        raise KeyError(job_id)
    event = _event_or_error(job, event_id)
    choice = str(decision.get("decision") or "").upper()
    reason = str(decision.get("reason") or "").strip()
    operator = str(decision.get("operator") or current_operator())

    if choice in REASON_REQUIRED and not reason:
        raise ValueError("该裁决必须填写理由")
    if choice == "MANUAL_VALUE" and "value" not in decision:
        raise ValueError("手工录入必须提供人工值")
    if event.get("event_type") == "MISSING_DOCUMENT":
        raise ValueError("缺件事件只能通过上传补充资料解决，不能直接裁决放行")
    allowed = SUPPORTED_BY_ACTION.get(str(event.get("action_kind") or ""), set())
    if choice not in allowed:
        raise ValueError(f"当前事件不支持裁决：{choice or 'EMPTY'}")

    replay: dict[str, Any] = {}
    if event.get("action_kind") == "DECIDE_ADVISORY":
        replay = _apply_advisory(job_id, event, choice, reason, operator)
        if choice in {"OVERRIDE", "MANUAL_VALUE"} and "value" in decision:
            replay = _apply_manual_value(job_id, event, decision.get("value"))
    elif choice in {"OVERRIDE", "MANUAL_VALUE"} and "value" in decision:
        replay = _apply_manual_value(job_id, event, decision.get("value"))
    elif event.get("invalidates"):
        expanded = JOB_STORE.invalidate_by_targets(job_id, list(event.get("invalidates") or []))
        replay = {"expanded_invalidates": expanded, "replayed": []}

    record = _store_resolution(
        job_id,
        event,
        decision,
        operator=operator,
        reason=reason,
        replay=replay,
    )
    audit = append_hitl_event(
        action="decide_review_event",
        entity_type="review_event",
        entity_id=event_id,
        review_event_id=event_id,
        before=record["before"],
        after=record["after"],
        reason=reason or choice,
        operator=operator,
        extra={
            "job_id": job_id,
            "decision": choice,
            "evidence": event.get("evidence") or {},
            "invalidates": record["invalidates"],
            "replayed": record["replayed"],
        },
    )
    return {
        "decision": record,
        "audit_event": audit,
        "job": JOB_STORE.get(job_id),
    }


def resolved_review_events(job: dict[str, Any]) -> list[dict[str, Any]]:
    history = job.get("review_event_decisions") or {}
    if not isinstance(history, dict):
        return []
    rows = [
        deepcopy(record["event"])
        for record in history.values()
        if isinstance(record, dict) and isinstance(record.get("event"), dict)
    ]
    return sorted(rows, key=lambda row: str(row.get("resolved_at") or ""), reverse=True)

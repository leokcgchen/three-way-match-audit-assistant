"""Persist three-way + cutoff as one legacy blob and two split views.

Consumers:
- UI / older code: ``three_way``
- export_readiness / new UI: ``three_way_match`` and ``cutoff_test``

Always write or clear the three keys together so readiness never reads a stale split.
"""

from __future__ import annotations

from typing import Any


THREE_WAY_RESULT_KEYS = ("three_way", "three_way_match", "cutoff_test")


def three_way_sample_patch(
    result: dict[str, Any],
    manual: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a sample/job patch that keeps legacy and split views in sync."""
    from src.three_way_match.phrases import expand_qty_role_shorthand, strip_match_score_language

    result = dict(result or {})

    def _clean(text: Any) -> str:
        return expand_qty_role_shorthand(strip_match_score_language(str(text or "")))

    # 落盘前清洗旧「得分」文案，避免结论页又读出得分
    for key in (
        "three_way_summary",
        "human_readable_summary",
        "summary",
        "cutoff_summary",
    ):
        if result.get(key):
            result[key] = _clean(result.get(key))
    mr = result.get("match_result")
    if isinstance(mr, dict):
        mr = dict(mr)
        if mr.get("human_readable_summary"):
            mr["human_readable_summary"] = _clean(mr.get("human_readable_summary"))
        # 废弃字段：不落库展示
        mr["match_score"] = 0
        result["match_result"] = mr

    patch: dict[str, Any] = {
        "three_way": result,
        "three_way_match": {
            "status": result.get("three_way_status")
            or (
                (result.get("match_result") or {}).get("overall_status")
                if isinstance(result.get("match_result"), dict)
                else None
            )
            or result.get("status"),
            "summary": _clean(
                result.get("three_way_summary")
                or result.get("summary")
                or result.get("human_readable_summary")
            ),
            "document_binding": result.get("document_binding") or {},
            "field_consistency": result.get("field_consistency") or {},
            "decision_trace": result.get("decision_trace") or [],
            "failure_category": result.get("three_way_failure_category"),
            "decision": result.get("decision"),
            "decision_reasons": result.get("decision_reasons") or [],
            "hold_reason_code": result.get("hold_reason_code"),
            "quantity_roles": result.get("quantity_roles") or {},
            "slot_reasons": result.get("slot_reasons") or {},
            "erp_review": result.get("erp_review") or {},
        },
        "cutoff_test": {
            "status": result.get("cutoff_status") or "NOT_TESTED",
            "summary": _clean(
                result.get("cutoff_summary")
                or result.get("cutoff_skipped_reason")
                or "截止性尚未执行"
            ),
            "result": result.get("cutoff_result"),
            "available": bool(result.get("cutoff_available")),
            "skipped_reason": result.get("cutoff_skipped_reason"),
        },
    }
    if manual is not None:
        patch["manual_three_way"] = manual
    return patch


def clear_three_way_fields(target: dict[str, Any]) -> None:
    """Drop legacy + split three-way/cutoff keys from a job or sample dict."""
    for key in THREE_WAY_RESULT_KEYS:
        if key in target:
            target[key] = None
        else:
            # sample dicts often omit keys; popping keeps merge_sample clean
            target.pop(key, None)

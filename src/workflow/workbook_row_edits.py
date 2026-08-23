"""Gate5：审计师对将写入底稿的业务结论列做覆写（公式列禁止改）。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Optional

# 各底稿：可编辑业务结论列 vs 只读公式列
SCHEMA: dict[str, dict[str, Any]] = {
    "gospd01030": {
        "label": "GOSPD01030 销售截止",
        "editable": [
            {"key": "all_ok", "label": "W 综合结论", "kind": "enum_w"},
            {"key": "exception", "label": "X 异常说明", "kind": "text"},
        ],
        "readonly_formula": [
            {"key": "diff_inv", "label": "K 发票差异", "hint": "公式 =F-J"},
            {"key": "diff_amt", "label": "S 金额差异", "hint": "公式 =F-R"},
            {"key": "diff_qty", "label": "T 数量差异", "hint": "公式 =G-Q"},
            {
                "key": "period_ok_formula",
                "label": "V 期间公式",
                "hint": '公式 =IF(P>$M$5,"YES 是","No 否")',
            },
        ],
        "readonly_context": [
            {"key": "chain_id", "label": "业务链"},
            {"key": "customer", "label": "客户"},
            {"key": "period_ok", "label": "独立期间判断（不写V）"},
            {"key": "formula_v", "label": "V公式口径（对照）"},
            {"key": "formula_conflict", "label": "公式冲突提示"},
        ],
    },
}


def schema_for_goals(goal_ids: list[str]) -> Optional[dict[str, Any]]:
    goals = {str(g).strip().lower() for g in (goal_ids or [])}
    for fmt, meta in SCHEMA.items():
        if fmt in goals:
            return {"format": fmt, **meta}
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_edits_bucket(job: dict[str, Any], fmt: str) -> dict[str, Any]:
    root = dict(job.get("workbook_row_edits") or {})
    bucket = dict(root.get(fmt) or {}) if isinstance(root.get(fmt), dict) else {}
    root[fmt] = bucket
    return root


def get_chain_edit(job: dict[str, Any], fmt: str, chain_id: str) -> dict[str, Any]:
    root = job.get("workbook_row_edits") or {}
    bucket = root.get(fmt) if isinstance(root, dict) else None
    if not isinstance(bucket, dict):
        return {}
    item = bucket.get(chain_id)
    return dict(item) if isinstance(item, dict) else {}


def upsert_chain_edit(
    job: dict[str, Any],
    *,
    fmt: str,
    chain_id: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    """合并单链覆写；仅保留该格式 schema 允许的 editable keys。"""
    meta = SCHEMA.get(fmt)
    if not meta:
        raise ValueError(f"不支持的底稿格式: {fmt}")
    allowed = {f["key"] for f in meta["editable"]}
    clean = {k: patch[k] for k in allowed if k in patch}
    # 空串允许清空 W
    root = ensure_edits_bucket(job, fmt)
    bucket = dict(root[fmt])
    prev = dict(bucket.get(chain_id) or {}) if isinstance(bucket.get(chain_id), dict) else {}
    prev.update(clean)
    prev["updated_at"] = _now()
    bucket[str(chain_id)] = prev
    root[fmt] = bucket
    return root


def apply_edits_to_rows(
    rows: list[dict[str, Any]],
    job: dict[str, Any],
    *,
    fmt: str,
) -> list[dict[str, Any]]:
    """导出前把审计师覆写叠到样本行（不改公式列）。"""
    meta = SCHEMA.get(fmt)
    if not meta:
        return rows
    allowed = {f["key"] for f in meta["editable"]}
    out: list[dict[str, Any]] = []
    for row in rows:
        cur = deepcopy(row)
        cid = str(cur.get("chain_id") or "")
        edit = get_chain_edit(job, fmt, cid)
        if not edit:
            out.append(cur)
            continue
        applied: dict[str, Any] = {}
        for k in allowed:
            if k not in edit:
                continue
            cur[k] = edit[k]
            applied[k] = edit[k]
        if applied:
            cur["auditor_edited"] = True
            cur["auditor_edits"] = applied
        out.append(cur)
    return out


def preview_rows_for_gate5(job: dict[str, Any]) -> dict[str, Any]:
    """Gate5 预览：系统建议值 + 当前覆写 + 只读公式说明。"""
    goals = list((job.get("plan") or {}).get("goal_ids") or job.get("goal_ids") or [])
    schema = schema_for_goals(goals)
    if not schema:
        return {
            "supported": False,
            "format": None,
            "rows": [],
            "message": "当前目标暂无可编辑底稿结论列（已支持：GOSPD01030）",
        }
    fmt = schema["format"]
    if fmt != "gospd01030":
        return {"supported": False, "format": fmt, "rows": [], "message": "未实现"}

    from src.reporting.gospd01030_filler import (
        W_NO_FALLBACK,
        W_YES_FALLBACK,
        build_gospd01030_sample_rows,
    )

    system_rows = build_gospd01030_sample_rows(
        job, w_yes=W_YES_FALLBACK, w_no=W_NO_FALLBACK
    )
    final_rows = apply_edits_to_rows(system_rows, job, fmt=fmt)
    by_cid = {str(r.get("chain_id") or ""): r for r in system_rows}

    ui_rows = []
    for row in final_rows:
        cid = str(row.get("chain_id") or "")
        sys_row = by_cid.get(cid) or {}
        edit = get_chain_edit(job, fmt, cid)
        ui_rows.append(
            {
                "chain_id": cid,
                "sample_no": row.get("sample_no"),
                "system": {
                    "all_ok": sys_row.get("all_ok") or "",
                    "exception": sys_row.get("exception") or "",
                    "period_ok": sys_row.get("period_ok") or "",
                    "formula_v": sys_row.get("formula_v") or "",
                    "formula_conflict": sys_row.get("formula_conflict") or "",
                    "customer": sys_row.get("customer") or "",
                },
                "values": {
                    "all_ok": row.get("all_ok") or "",
                    "exception": row.get("exception") or "",
                },
                "edits": {k: edit[k] for k in ("all_ok", "exception") if k in edit},
                "readonly_formula": {
                    "diff_inv": "公式 =F-J（禁止覆写）",
                    "diff_amt": "公式 =F-R（禁止覆写）",
                    "diff_qty": "公式 =G-Q（禁止覆写）",
                    "period_ok_formula": '公式 =IF(P>$M$5,"YES 是","No 否")（禁止覆写）',
                },
                "w_options": [W_YES_FALLBACK, W_NO_FALLBACK, ""],
            }
        )
    return {
        "supported": True,
        "format": fmt,
        "label": schema["label"],
        "editable": schema["editable"],
        "readonly_formula": schema["readonly_formula"],
        "readonly_context": schema["readonly_context"],
        "rows": ui_rows,
        "message": "可改 W/X；K/S/T/V 公式只读。确认 Gate5 后导出即为终稿。",
    }

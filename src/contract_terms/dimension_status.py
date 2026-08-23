"""合同条款按维度拆分状态（供 GOSPD01010.2 等专项目标使用）。

整单仍可为 WARNING（任一维度不清），但「履约义务」可单独为 CLEAR，
避免支付/对价问题拖累履约义务结论列。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

# CLEAR=未发现该维歧义；AMBIGUOUS=该维有问题码；MISSING=无合同；NOT_TESTED=未跑
DimensionStatus = str

PERF_DIMENSION = "履约义务"


def build_dimension_statuses(
    *,
    has_contract: bool,
    issues: Iterable[Any],
) -> Dict[str, DimensionStatus]:
    """按问题维度汇总状态。"""
    by_dim: Dict[str, List[Any]] = {}
    for it in issues or []:
        if hasattr(it, "dimension"):
            dim = str(getattr(it, "dimension") or "综合")
        elif isinstance(it, dict):
            dim = str(it.get("dimension") or it.get("clause_name") or "综合")
        else:
            continue
        by_dim.setdefault(dim, []).append(it)

    dims = ("交易对价", "支付条款", PERF_DIMENSION, "运输及控制权转移", "综合")
    out: Dict[str, DimensionStatus] = {}
    if not has_contract:
        for d in dims:
            out[d] = "MISSING"
        return out

    for d in dims:
        if d in by_dim and by_dim[d]:
            out[d] = "AMBIGUOUS"
        elif d == "综合":
            # 综合仅在有未归类问题时标 AMBIGUOUS
            out[d] = "AMBIGUOUS" if by_dim.get("综合") else "CLEAR"
        else:
            out[d] = "CLEAR"
    return out


def performance_obligation_status(
    dimension_statuses: Optional[Dict[str, Any]],
) -> Optional[str]:
    if not isinstance(dimension_statuses, dict):
        return None
    raw = (
        dimension_statuses.get(PERF_DIMENSION)
        or dimension_statuses.get("performance_obligation")
        or dimension_statuses.get("PERFORMANCE_OBLIGATION")
    )
    if raw is None:
        return None
    return str(raw).strip().upper() or None

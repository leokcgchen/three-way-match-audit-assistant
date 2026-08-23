"""底稿目标 → 程序 / 认定 / 证据矩阵（准则化改造入口）。

与 recipes.WORKOBER_RECIPES 对齐：只描述「该底稿要证明什么」，
不替代规则引擎执行。供覆盖地图与验收对照。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# goal_id 与 recipes 保持一致
PROGRAM_MATRIX: Dict[str, Dict[str, Any]] = {
    "gospd01010": {
        "label": "GOSPD01010.1 期内收入检查",
        "assertions": [
            {
                "id": "occurrence",
                "label": "发生/存在",
                "ceavop": "E",
                "programs": ["evidence_chain", "relations_gate4", "three_way"],
                "evidence": ["序时账", "合同", "订单", "签收/验收", "发票"],
                "cannot_claim": "不能仅凭账至证证明总体完整性",
            },
            {
                "id": "accuracy",
                "label": "准确性/计价",
                "ceavop": "A/V",
                "programs": ["amount_test", "three_way"],
                "evidence": ["订单/合同单价数量", "发票价税", "签收数量"],
                "cannot_claim": "推导数量不得当作原始签收数量",
            },
            {
                "id": "cutoff",
                "label": "截止",
                "ceavop": "截止",
                "programs": ["three_way_cutoff"],
                "evidence": ["控制权转移日", "过账日", "报告期末日(若配置)"],
                "cannot_claim": "付款账期不是收入确认时点",
            },
            {
                "id": "contract_clarity",
                "label": "合同条款清晰性",
                "ceavop": "A/E",
                "programs": ["contract_terms"],
                "evidence": ["销售合同正文"],
                "cannot_claim": "歧义输出 WARNING，不直接等同已证实错报",
            },
        ],
    },
    "gospd01030": {
        "label": "GOSPD01030 期后销售截止",
        "assertions": [
            {
                "id": "cutoff",
                "label": "截止（期后）",
                "ceavop": "截止",
                "programs": ["evidence_chain", "relations_gate4", "three_way_cutoff"],
                "evidence": ["签收/控制权日", "过账日", "报告期末日"],
                "cannot_claim": "未配置 period_end 或 Gate4 未确认时不得出正式期间结论",
            },
            {
                "id": "occurrence_sample",
                "label": "样本内发生",
                "ceavop": "E",
                "programs": ["evidence_chain", "relations_gate4"],
                "evidence": ["订单", "签收", "发票", "序时账"],
                "cannot_claim": "不形成抽样总体结论；未确认链不进底稿",
            },
            {
                "id": "ar_period",
                "label": "应收账款计入正确期间（步骤3）",
                "ceavop": "截止/E",
                "programs": ["three_way_cutoff"],
                "evidence": ["发票过账日", "收入确认期间", "报告期末日"],
                "cannot_claim": "当前与收入期间同判；独立应收函证/坏账不在本表范围",
            },
        ],
    },
    "gospd01010_2": {
        "label": "GOSPD01010.2 履约义务",
        "assertions": [
            {
                "id": "performance_obligation",
                "label": "履约义务识别",
                "ceavop": "A/E",
                "programs": ["evidence_chain", "contract_terms"],
                "evidence": ["销售合同"],
                "cannot_claim": "不强制金额与三单",
            },
        ],
    },
    "gospd01010_3": {
        "label": "GOSPD01010.3 交易价格",
        "assertions": [
            {
                "id": "transaction_price",
                "label": "交易价格准确性",
                "ceavop": "A/V",
                "programs": ["evidence_chain", "contract_terms", "amount_test"],
                "evidence": ["合同对价条款", "订单", "发票"],
                "cannot_claim": "不强制三单/截止",
            },
        ],
    },
    "gospd01010_4": {
        "label": "GOSPD01010.4 价格分摊",
        "assertions": [
            {
                "id": "ssp_allocation",
                "label": "单独售价分摊",
                "ceavop": "A/V",
                "programs": ["evidence_chain", "contract_terms", "amount_test"],
                "evidence": ["合同", "SSP/分摊依据", "发票"],
                "cannot_claim": "不强制三单/截止",
            },
        ],
    },
}


def get_program_matrix(goal_id: Optional[str] = None) -> Dict[str, Any]:
    """返回单个或全部底稿程序—认定—证据矩阵。"""
    if goal_id:
        key = str(goal_id).strip()
        item = PROGRAM_MATRIX.get(key)
        if not item:
            return {"goal_id": key, "found": False, "assertions": []}
        return {"goal_id": key, "found": True, **item}
    return {
        "version": "program-matrix-v1",
        "goals": [
            {"goal_id": gid, **meta} for gid, meta in PROGRAM_MATRIX.items()
        ],
    }


def matrix_for_job(job: Dict[str, Any]) -> Dict[str, Any]:
    """按任务已选底稿目标汇总矩阵（多选并集）。"""
    goals = list(job.get("selected_goals") or [])
    plan = job.get("plan") or {}
    if not goals and plan.get("goal_id"):
        goals = [plan.get("goal_id")]
    rows: List[Dict[str, Any]] = []
    for gid in goals:
        m = get_program_matrix(str(gid))
        if m.get("found"):
            rows.append(m)
    return {
        "version": "program-matrix-v1",
        "selected_goals": goals,
        "matrices": rows,
        "note": "PASS 仅表示已执行程序未发现异常，不表示单据真实或总体完整",
    }

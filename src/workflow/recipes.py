"""底稿目标配方：多选目标 → 必做步骤/维度并集（流程逻辑，与 UI 无关）。"""

from __future__ import annotations

from typing import Any

STEP_UPLOAD = "upload_ocr"
STEP_FIELDS = "field_confirm"
STEP_EVIDENCE = "evidence_match"
STEP_RELATIONS = "relations_gate4"
STEP_AMOUNT = "amount_test"
STEP_CONTRACT = "contract_terms"
STEP_THREE_WAY = "three_way_cutoff"
STEP_CONCLUSION = "conclusion_gate5"
STEP_WORKBOOK = "workbook_export"

_COMMON_PREFIX = (STEP_UPLOAD, STEP_FIELDS)

STEP_LABELS: dict[str, str] = {
    STEP_UPLOAD: "上传凭证",
    STEP_FIELDS: "人工核对",
    STEP_EVIDENCE: "串单匹配",
    STEP_RELATIONS: "勾稽确认",
    STEP_AMOUNT: "金额测试",
    STEP_CONTRACT: "合同条款",
    STEP_THREE_WAY: "三单+截止",
    STEP_CONCLUSION: "确认结论",
    STEP_WORKBOOK: "导出底稿",
}

# 产品仅开放官方 GOSPD 底稿目标；自建「审阅底稿」配方已下线（导出代码路径仍保留兜底）。
WORKPAPER_RECIPES: dict[str, dict[str, Any]] = {
    # 官方程序索引 GOSPD01010.1：整个期间的收入-检查和评价（期内销售抽凭）
    "gospd01010": {
        "goal_id": "gospd01010",
        "label": "GOSPD01010 期内收入抽凭",
        "description": (
            "期内收入抽凭。工作台先立笔再传凭证；核对字段与串单后测条款、金额与三单/截止，"
            "未过才进结论，清单收口后导出。"
        ),
        "extra_steps": (
            STEP_EVIDENCE,
            STEP_RELATIONS,
            STEP_CONTRACT,
            STEP_AMOUNT,
            STEP_THREE_WAY,
            STEP_CONCLUSION,
            STEP_WORKBOOK,
        ),
        "dimensions": (
            "HITL_FIELD_CONFIRM",
            "EVIDENCE_MATCH",
            "HITL_MATCH_CONFIRM",
            "RELATION_CANDIDATES",
            "CONTRACT_CLARITY",
            "AMOUNT_ACCURACY",
            "THREE_WAY_MATCH",
            "CUTOFF",
            "HITL_CONCLUSION_CONFIRM",
        ),
        "workbook_sheets": ("GOSPD01010.1",),
        "workbook_format": "gospd01010",
    },
    # 官方程序索引 GOSPD01030：销售截止（期后）
    "gospd01030": {
        "goal_id": "gospd01030",
        "label": "GOSPD01030 销售截止（期后）",
        "description": (
            "期后过账销售截止。工作台先上传裁剪序时账立笔，再传凭证；"
            "齐则绿灯；只在缺字段或测试不通过亮红灯。结论页只看对不上的数据和测试逻辑。"
            "不强制合同条款与金额测试。"
        ),
        "extra_steps": (
            STEP_EVIDENCE,
            STEP_RELATIONS,
            STEP_THREE_WAY,
            STEP_CONCLUSION,
            STEP_WORKBOOK,
        ),
        "dimensions": (
            "HITL_FIELD_CONFIRM",
            "EVIDENCE_MATCH",
            "HITL_MATCH_CONFIRM",
            "RELATION_CANDIDATES",
            "THREE_WAY_MATCH",
            "CUTOFF",
            "HITL_CONCLUSION_CONFIRM",
        ),
        "workbook_sheets": ("GOSPD01030",),
        "workbook_format": "gospd01030",
    },
    # 官方程序索引 GOSPD01010.2：履约义务区分（期内销售抽凭之合同审阅）
    "gospd01010_2": {
        "goal_id": "gospd01010_2",
        "label": "GOSPD01010.2 履约义务抽凭",
        "description": (
            "检查销售合同及相关沟通文件，确认管理层是否已适当确定合同中可明确区分的履约义务；"
            "回填交易价格及（若适用）其他相关文件索引。"
            "工作台立笔后传凭证；核对字段与串单后测条款，再确认结论并导出。"
            "不强制金额准确性与三单/截止（与 GOSPD01010.1 不同）。"
        ),
        "extra_steps": (
            STEP_EVIDENCE,
            STEP_RELATIONS,
            STEP_CONTRACT,
            STEP_CONCLUSION,
            STEP_WORKBOOK,
        ),
        "dimensions": (
            "HITL_FIELD_CONFIRM",
            "EVIDENCE_MATCH",
            "HITL_MATCH_CONFIRM",
            "RELATION_CANDIDATES",
            "CONTRACT_CLARITY",
            "HITL_CONCLUSION_CONFIRM",
        ),
        "workbook_sheets": ("GOSPD01010.2",),
        "workbook_format": "gospd01010_2",
    },
    # 官方程序索引 GOSPD01010.3：交易价格适当性
    "gospd01010_3": {
        "goal_id": "gospd01010_3",
        "label": "GOSPD01010.3 交易价格抽凭",
        "description": (
            "获取管理层对合同交易价格的计算，检查合同及其他相关文件，"
            "确定是否已适当确定交易价格；回填是否需要计算、计算方式及其他文件索引。"
            "工作台立笔后传凭证；核对字段与串单后测条款与金额，再确认结论并导出。"
            "不强制三单/截止（与 GOSPD01010.1 不同）。"
        ),
        "extra_steps": (
            STEP_EVIDENCE,
            STEP_RELATIONS,
            STEP_CONTRACT,
            STEP_AMOUNT,
            STEP_CONCLUSION,
            STEP_WORKBOOK,
        ),
        "dimensions": (
            "HITL_FIELD_CONFIRM",
            "EVIDENCE_MATCH",
            "HITL_MATCH_CONFIRM",
            "RELATION_CANDIDATES",
            "CONTRACT_CLARITY",
            "AMOUNT_ACCURACY",
            "HITL_CONCLUSION_CONFIRM",
        ),
        "workbook_sheets": ("GOSPD01010.3",),
        "workbook_format": "gospd01010_3",
    },
    # 官方程序索引 GOSPD01010.4：交易价格分摊至履约义务（SSP）
    "gospd01010_4": {
        "goal_id": "gospd01010_4",
        "label": "GOSPD01010.4 价格分摊抽凭",
        "description": (
            "获取交易价格分摊计算，核对单独售价(SSP)，评价折扣/可变对价分摊标准，"
            "重算分摊并评估收入确认；枚举以模板「底稿须知」为准。"
            "工作台立笔后传凭证；核对字段与串单后测条款与分摊金额，再确认结论并导出。"
            "无管理层分摊底稿时按单一履约义务处理并在注释中披露。"
        ),
        "extra_steps": (
            STEP_EVIDENCE,
            STEP_RELATIONS,
            STEP_CONTRACT,
            STEP_AMOUNT,
            STEP_CONCLUSION,
            STEP_WORKBOOK,
        ),
        "dimensions": (
            "HITL_FIELD_CONFIRM",
            "EVIDENCE_MATCH",
            "HITL_MATCH_CONFIRM",
            "RELATION_CANDIDATES",
            "CONTRACT_CLARITY",
            "AMOUNT_ACCURACY",
            "HITL_CONCLUSION_CONFIRM",
        ),
        "workbook_sheets": ("GOSPD01010.4", "底稿须知"),
        "workbook_format": "gospd01010_4",
    },
}

# 展示序：证据/关系 →（条款）→（金额）→ 三单/截止 → 结论 → 导出
_STEP_ORDER = (
    STEP_UPLOAD,
    STEP_FIELDS,
    STEP_EVIDENCE,
    STEP_RELATIONS,
    STEP_CONTRACT,
    STEP_AMOUNT,
    STEP_THREE_WAY,
    STEP_CONCLUSION,
    STEP_WORKBOOK,
)

OFFICIAL_GOSPD_FORMATS = frozenset(
    {"gospd01010", "gospd01030", "gospd01010_2", "gospd01010_3", "gospd01010_4"}
)


def list_workpaper_goals() -> list[dict[str, Any]]:
    return [
        {
            "goal_id": r["goal_id"],
            "label": r["label"],
            "description": r["description"],
            "workbook_sheets": list(r.get("workbook_sheets") or []),
        }
        for r in WORKPAPER_RECIPES.values()
    ]


def resolve_workflow_plan(goal_ids: list[str] | tuple[str, ...] | None) -> dict[str, Any]:
    """多选底稿目标 → 步骤/维度/导出 sheet 并集。"""
    selected = [str(g).strip() for g in (goal_ids or []) if str(g).strip()]
    unknown = [g for g in selected if g not in WORKPAPER_RECIPES]
    if unknown:
        raise ValueError(f"未知底稿目标: {', '.join(unknown)}")

    if not selected:
        return {
            "goal_ids": [],
            "goals": [],
            "required_steps": [],
            "step_labels": [],
            "required_dimensions": [],
            "workbook_sheets": [],
            "skipped_steps": list(_STEP_ORDER),
            "note": "请先选择至少一项底稿目标。",
        }

    steps: set[str] = set(_COMMON_PREFIX)
    dims: set[str] = set()
    sheets: list[str] = []
    seen_sheets: set[str] = set()
    goals_meta: list[dict[str, Any]] = []

    for gid in selected:
        recipe = WORKPAPER_RECIPES[gid]
        goals_meta.append(
            {
                "goal_id": gid,
                "label": recipe["label"],
                "description": recipe["description"],
            }
        )
        for s in recipe.get("extra_steps") or ():
            steps.add(str(s))
        for d in recipe.get("dimensions") or ():
            dims.add(str(d))
        for sh in recipe.get("workbook_sheets") or ():
            if sh not in seen_sheets:
                seen_sheets.add(sh)
                sheets.append(sh)

    required = [s for s in _STEP_ORDER if s in steps]
    skipped = [s for s in _STEP_ORDER if s not in steps]
    formats = [
        str(WORKPAPER_RECIPES[g].get("workbook_format") or "")
        for g in selected
        if WORKPAPER_RECIPES[g].get("workbook_format")
    ]
    note = "多选目标时步骤与覆盖维度取并集；未列入本次的步骤不跑、不挡、不进底稿。"
    official = [g for g in selected if WORKPAPER_RECIPES[g].get("workbook_format")]
    bits: list[str] = []
    for g in official:
        lab = WORKPAPER_RECIPES[g].get("label") or g
        if g == "gospd01010":
            bits.append(f"「{lab}」：工作台立笔→传凭证→核对/串单→条款/金额/三单截止→结论→导出")
        elif g == "gospd01030":
            bits.append(
                f"「{lab}」：工作台上传裁剪序时账→传凭证→识别分流→三单/截止→未过才进结论→导出"
            )
        elif g == "gospd01010_2":
            bits.append(f"「{lab}」：工作台立笔→传凭证→核对/串单→条款（履约义务）→结论→导出（不强制金额/三单）")
        elif g == "gospd01010_3":
            bits.append(f"「{lab}」：工作台立笔→传凭证→核对/串单→条款/金额（交易价格）→结论→导出（不强制三单）")
        elif g == "gospd01010_4":
            bits.append(f"「{lab}」：工作台立笔→传凭证→核对/串单→条款/金额（价格分摊/SSP）→结论→导出（不强制三单）")
        else:
            bits.append(f"「{lab}」→ 导出对应官方模板")
    if bits:
        export_note = (
            "导出按勾选目标各生成一份底稿，互不偏重、互不覆盖。"
            if len(bits) > 1
            else "导出对应官方模板。"
        )
        note = "；".join(bits) + "。" + export_note + (" " + note if len(selected) > 1 else "")

    return {
        "goal_ids": selected,
        "goals": goals_meta,
        "required_steps": required,
        "step_labels": [
            {"step_id": s, "label": STEP_LABELS.get(s, s)} for s in required
        ],
        "required_dimensions": sorted(dims),
        "workbook_sheets": sheets,
        "workbook_formats": formats,
        "skipped_steps": skipped,
        "note": note,
    }

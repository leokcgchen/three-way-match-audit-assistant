"""LLM 提示词目录：供 UI「提示词工程」页只读展示。

数据来自 `src/llm/prompts.py` 运行时常量与样例拼装，避免与代码脱节。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from src.llm.prompts import (
    PROMPT_VERSION,
    UNIFIED_SYSTEM_PROMPT,
    build_amount_gap_fill_user,
    build_conclusion_interpret_user,
    build_contract_clarity_user,
    build_cutoff_semantic_user,
    build_field_gap_fill_user,
    build_matching_disambiguation_user,
)

SAMPLE_OCR = (
    "【样例正文，仅用于展示提示词结构】\n"
    "销售合同编号：HT25-0001\n"
    "对应订单：SO25-0001\n"
    "验收合格后确认收入；付款账期为开票后30天。\n"
    "含税金额合计人民币壹拾万元整。\n"
)


def _sample_field() -> str:
    return build_field_gap_fill_user(
        doc_type_label="销售合同",
        ocr_text=SAMPLE_OCR,
        unresolved_fields=["paymentTerms", "controlTransferTerms", "acceptanceDate"],
        rule_fields={"documentNo": "HT25-0001", "orderNo": "SO25-0001"},
        semantic_hint="优先抽取控制权/验收相关表述。",
    )


def _sample_amount() -> str:
    return build_amount_gap_fill_user(
        quantity=None,
        unit_price_excl_tax=100.0,
        discount_rate=None,
        vat_rate=0.13,
        documents_blob=SAMPLE_OCR,
        business_id="SO25-0001",
    )


def _sample_contract() -> str:
    return build_contract_clarity_user(
        text=SAMPLE_OCR,
        existing_codes=[],
        allowed_codes=[
            "REBATE_TERM_AMBIGUOUS",
            "PAYMENT_TRIGGER_UNCLEAR",
            "CONTROL_TRANSFER_UNCLEAR",
        ],
        business_id="HT25-0001",
    )


def _sample_cutoff() -> str:
    return build_cutoff_semantic_user(
        documents_blob=SAMPLE_OCR,
        rule_fields={"deliveryDate": "2025-03-01", "paymentTerms": "开票后30天"},
        business_id="SO25-0001",
    )


def _sample_matching() -> str:
    return build_matching_disambiguation_user(
        business_id="SO25-0001",
        rule_result={
            "status": "WARNING",
            "anchor_keys": ["SO25-0001"],
            "missing_roles": [],
            "issue_description": "同角色多候选",
        },
        documents=[
            {"file_name": "合同.pdf", "doc_type": "contract"},
            {"file_name": "订单.pdf", "doc_type": "order"},
        ],
        documents_blob=SAMPLE_OCR,
    )


def _sample_conclusion() -> str:
    return build_conclusion_interpret_user(
        family="amount",
        rule_final_payload={
            "test_status": "WARNING",
            "business_id": "SO25-0001",
            "issue_type": "AMOUNT_MISMATCH",
            "difference_amount": 12.34,
        },
        allowed_issue_types=["AMOUNT_MISMATCH", "PRICING_ELEMENT_MISSING"],
    )


PromptEntry = Dict[str, Any]


def list_prompt_entries() -> List[PromptEntry]:
    """返回已接线任务（含样例 user 提示词生成器）。"""
    return [
        {
            "task_type": "FIELD_GAP_FILL",
            "title": "字段补抽",
            "stage": "上传与OCR",
            "when": "规则/启发式抽不全时，用原文补关键字段",
            "env_flag": "FIELD_EXTRACT_MODE=llm_first（默认）",
            "code_path": "src/legacy_ocr/ocr_adapter.py → build_field_gap_fill_user",
            "affects_final": "否（只写候选字段，须字段确认后进测试）",
            "wired": True,
            "build_sample": _sample_field,
        },
        {
            "task_type": "MATCHING_DISAMBIGUATION",
            "title": "证据匹配消歧",
            "stage": "测试环节 · 证据匹配",
            "when": "编号对不上或同角色多候选时，给出采用/排除建议",
            "env_flag": "MATCHING_LLM_DISAMBIGUATION=1",
            "code_path": "src/evidence_match/disambiguation.py",
            "affects_final": "否（仅候选；人工采纳后仍须重确认字段）",
            "wired": True,
            "build_sample": _sample_matching,
        },
        {
            "task_type": "AMOUNT_GAP_FILL",
            "title": "金额要素补抽",
            "stage": "测试环节 · 金额准确性",
            "when": "数量/未税单价/折扣/税率有缺时补抽",
            "env_flag": "BATCH_LLM_ASSIST=1",
            "code_path": "src/llm/batch_assist.py",
            "affects_final": "否（补要素后仍由公式重算；不直接给正确金额）",
            "wired": True,
            "build_sample": _sample_amount,
        },
        {
            "task_type": "CONTRACT_CLARITY_REVIEW",
            "title": "合同条款补漏",
            "stage": "测试环节 · 合同条款",
            "when": "规则未命中问题时，在原文中找歧义条款候选",
            "env_flag": "BATCH_LLM_ASSIST=1",
            "code_path": "src/llm/batch_assist.py",
            "affects_final": "否（最高 WARNING；须 excerpt 核验）",
            "wired": True,
            "build_sample": _sample_contract,
        },
        {
            "task_type": "CUTOFF_SEMANTIC_EXTRACTION",
            "title": "截止语义补抽",
            "stage": "测试环节 · 三单+截止",
            "when": "控制权转移日/贸易术语等规则抽不足时补语义",
            "env_flag": "BATCH_LLM_ASSIST=1",
            "code_path": "src/llm/batch_assist.py",
            "affects_final": "否（应确认日仍由代码算；付款账期不进公式）",
            "wired": True,
            "build_sample": _sample_cutoff,
        },
        {
            "task_type": "CONCLUSION_INTERPRETATION",
            "title": "结论解读",
            "stage": "测试环节 · 按需按钮",
            "when": "规则已出结论后，用白话解释并给复核清单",
            "env_flag": "CONCLUSION_LLM_ASSIST=1；PASS 默认跳过",
            "code_path": "src/llm/conclusion_interpret.py",
            "affects_final": "否（绝不改 PASS/FAIL/WARNING）",
            "wired": True,
            "build_sample": _sample_conclusion,
        },
        {
            "task_type": "MATCHING_ALLOCATION",
            "title": "匹配分摊（设计中）",
            "stage": "证据匹配",
            "when": "一对多金额/数量分摊消歧",
            "env_flag": "—",
            "code_path": "prompts/06_…四类测试.md（未接线）",
            "affects_final": "—",
            "wired": False,
            "build_sample": None,
        },
        {
            "task_type": "CROSS_DOCUMENT_LOGIC_REVIEW",
            "title": "跨单逻辑审阅（设计中）",
            "stage": "跨测试",
            "when": "跨文件逻辑矛盾 / 未知未知扫描",
            "env_flag": "—",
            "code_path": "prompts/06_…四类测试.md（未接线）",
            "affects_final": "—",
            "wired": False,
            "build_sample": None,
        },
    ]


def catalog_summary() -> Dict[str, Any]:
    entries = list_prompt_entries()
    return {
        "prompt_version": PROMPT_VERSION,
        "system_prompt": UNIFIED_SYSTEM_PROMPT,
        "wired_count": sum(1 for e in entries if e.get("wired")),
        "design_only_count": sum(1 for e in entries if not e.get("wired")),
        "entries": entries,
        "principles": [
            "统一 system + 按 task_type 路由的 user",
            "规则引擎出 PASS/WARNING/FAIL；LLM 仅为顾问式补缺",
            "主张须带可回查原文 excerpt；验证器可拒收",
            "付款账期不决定收入确认日",
        ],
    }


def render_sample_user(entry: PromptEntry) -> Optional[str]:
    builder: Optional[Callable[[], str]] = entry.get("build_sample")
    if not callable(builder):
        return None
    return builder()

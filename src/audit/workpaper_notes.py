"""底稿旁注：系统观察 / 待审计师判断（不改规则终态、不作 AI 审计结论）。

写入异常说明列时仅作附录；Yes/No 仍由断言层确定性规则产出。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence


def _norm_biz_token(raw: str) -> str:
    s = str(raw or "").upper().strip()
    if not s:
        return ""
    # 抽取常见业务号形态，便于 SO25-0281 ↔ HT25-0281 旁注归属时用数字段兜底
    m = re.search(r"((?:SO|HT|KJHT|EXHT|EXKJHT)\d{2}-\d{4})", s)
    return m.group(1) if m else s


def _biz_tokens(raw: str) -> set[str]:
    s = str(raw or "").upper()
    found = set(re.findall(r"(?:SO|HT|KJHT|EXHT|EXKJHT)\d{2}-\d{4}", s))
    # 数字段：0281 —— 仅当两边都有完整号时作弱关联
    digits = set(re.findall(r"\d{2}-\d{4}", s))
    return found | digits


def _biz_match(candidate: Dict[str, Any], chain_id: str) -> bool:
    """顾问候选是否归属当前业务链。

    - chain 为空：不过滤（整单汇总场景）
    - candidate.business_id 为空：不计入「本链」旁注（避免多笔串注）；整单汇总仍可另取
    """
    if not chain_id:
        return True
    bid = str(candidate.get("business_id") or "").strip()
    if not bid:
        return False
    c_tok = _norm_biz_token(chain_id)
    b_tok = _norm_biz_token(bid)
    if c_tok and b_tok and (c_tok == b_tok or c_tok in bid.upper() or b_tok in chain_id.upper()):
        return True
    # 同号段弱匹配：SO25-0281 vs HT25-0281
    c_set = _biz_tokens(chain_id)
    b_set = _biz_tokens(bid)
    if not c_set or not b_set:
        return False
    c_digits = {x.split("-", 1)[-1] if "-" in x else x for x in c_set}
    b_digits = {x.split("-", 1)[-1] if "-" in x else x for x in b_set}
    # 仅用完整业务号交集优先；否则用末四段数字交集且长度>=4
    if c_set & b_set:
        return True
    dig_c = {d for d in c_digits if len(d) >= 4}
    dig_b = {d for d in b_digits if len(d) >= 4}
    return bool(dig_c & dig_b)


def _llm_contract_codes(contract_res: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(contract_res, dict):
        return []
    extracted = contract_res.get("extracted") or {}
    sources = extracted.get("issue_sources") if isinstance(extracted, dict) else {}
    if isinstance(sources, dict) and sources.get("llm"):
        return [str(x) for x in sources.get("llm") or [] if x]
    # 回退：issues[].source
    report = contract_res.get("clarity_report") or {}
    tr = report.get("test_result") if isinstance(report, dict) else {}
    issues = (tr or {}).get("issues") or contract_res.get("checks") or []
    out: List[str] = []
    for it in issues:
        if not isinstance(it, dict):
            continue
        if str(it.get("source") or "").lower() == "llm":
            code = str(it.get("issue_code") or it.get("clause_id") or "").strip()
            if code:
                out.append(code)
    return out


def _llm_amount_sources(amount: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(amount, dict):
        return []
    ar = amount.get("accuracy_report") or {}
    sv = ar.get("source_values") or amount.get("source_values") or {}
    if not isinstance(sv, dict):
        return []
    tags: List[str] = []
    if str(sv.get("quantity_source") or "").lower() in {"llm", "llm_assist"}:
        tags.append("quantity")
    if str(sv.get("price_source") or "").lower() in {"llm", "llm_assist"}:
        tags.append("unit_price")
    return tags


def _pending_advisory(
    job: Optional[Dict[str, Any]],
    *,
    chain_id: str = "",
) -> List[Dict[str, Any]]:
    store = (job or {}).get("advisory_candidates") or []
    out: List[Dict[str, Any]] = []
    for row in store:
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "").upper() != "PROPOSED":
            continue
        if not _biz_match(row, chain_id):
            continue
        out.append(row)
    return out


def build_workpaper_notes(
    *,
    job: Optional[Dict[str, Any]] = None,
    chain_id: str = "",
    contract_res: Optional[Dict[str, Any]] = None,
    amount: Optional[Dict[str, Any]] = None,
    empty_verdict_labels: Optional[Sequence[str]] = None,
    extra_observations: Optional[Sequence[str]] = None,
    extra_pending: Optional[Sequence[str]] = None,
) -> Dict[str, str]:
    """生成旁注三段文本（可为空）。

    返回：
      system_observation / pending_judgment / appendix_appendix（写入异常说明列）
    """
    observations: List[str] = []
    pending: List[str] = []

    for x in extra_observations or []:
        if str(x).strip():
            observations.append(str(x).strip())
    for x in extra_pending or []:
        if str(x).strip():
            pending.append(str(x).strip())

    llm_codes = _llm_contract_codes(contract_res)
    if llm_codes:
        observations.append(
            "合同条款测试出现 LLM 候选问题码（非审计结论，须原文复核）："
            + "、".join(llm_codes[:8])
        )
        pending.append("上述 LLM 候选问题码是否构成条款缺陷、是否需补证或调整程序")

    amt_tags = _llm_amount_sources(amount)
    if amt_tags:
        observations.append(
            "金额要素中以下字段来自 LLM 补缺候选（已标 source=llm，未自动视为认定值）："
            + "、".join(amt_tags)
        )
        pending.append("LLM 补缺的数量/单价是否与原文一致，是否可接受后复跑金额测试")

    for cand in _pending_advisory(job, chain_id=chain_id)[:6]:
        task = str(cand.get("task_type") or "")
        payload = cand.get("payload") if isinstance(cand.get("payload"), dict) else {}
        hint = (
            str(payload.get("issue_code") or payload.get("field_name") or payload.get("disposition") or "")
            or str(cand.get("kind") or "候选")
        )
        observations.append(f"存在待确认顾问候选 [{task}] {hint}".strip())
        pending.append(f"是否接受/拒绝顾问候选 [{task}] {hint}".strip())

    for label in empty_verdict_labels or []:
        if str(label).strip():
            pending.append(f"{label}结论为空（证据不足或未测完），待审计师判断后补填")

    # 去重保序
    def _uniq(items: List[str]) -> List[str]:
        seen = set()
        out: List[str] = []
        for it in items:
            if it in seen:
                continue
            seen.add(it)
            out.append(it)
        return out

    observations = _uniq(observations)
    pending = _uniq(pending)

    sys_block = ""
    if observations:
        sys_block = "系统观察：\n" + "\n".join(f"- {x}" for x in observations)

    pend_block = ""
    if pending:
        pend_block = "待审计师判断：\n" + "\n".join(f"- {x}" for x in pending)

    appendix_parts = [p for p in (sys_block, pend_block) if p]
    appendix = "\n\n".join(appendix_parts)
    return {
        "system_observation": sys_block,
        "pending_judgment": pend_block,
        "exception_appendix": appendix,
    }


def pending_advisory_for_job(
    job: Optional[Dict[str, Any]],
    *,
    chain_id: str = "",
) -> List[Dict[str, Any]]:
    """待决顾问候选（可按链过滤）。旁注展示仍可用；门禁请用 blocking_advisory_for_export。"""
    return _pending_advisory(job, chain_id=chain_id)


# 字段确认应消化的顾问类型（确认字段 = 人工已覆盖补缺主张）
FIELD_DIGEST_TASK_TYPES = frozenset({"FIELD_GAP_FILL"})


def is_field_digest_advisory(cand: Dict[str, Any]) -> bool:
    return str(cand.get("task_type") or "").upper() in FIELD_DIGEST_TASK_TYPES


def blocking_advisory_for_export(
    job: Optional[Dict[str, Any]],
    *,
    chain_id: str = "",
) -> List[Dict[str, Any]]:
    """导出/Gate5 门禁用：按产品口径 A，非字段类顾问不再挡主路径。

    字段类若仍 PROPOSED，说明尚未完成字段确认消化；其余仅旁注观察。
    """
    return [
        row
        for row in _pending_advisory(job, chain_id=chain_id)
        if is_field_digest_advisory(row)
    ]


def digest_field_advisories_on_confirm(
    store: Optional[Iterable[Dict[str, Any]]],
    *,
    chain_id: str = "",
) -> List[Dict[str, Any]]:
    """字段确认时：将本笔相关 FIELD_GAP_FILL 待决标为 DROPPED（已由人工确认覆盖）。"""
    out: List[Dict[str, Any]] = []
    for row in store or []:
        if not isinstance(row, dict):
            continue
        cur = dict(row)
        if (
            str(cur.get("status") or "").upper() == "PROPOSED"
            and is_field_digest_advisory(cur)
            and _biz_match(cur, chain_id)
        ):
            cur["status"] = "DROPPED"
            cur["note"] = (str(cur.get("note") or "") + "；字段确认时已消化").strip("；")
            cur["actor"] = "field_confirm"
        out.append(cur)
    return out



def merge_exception_text(base: str, appendix: str) -> str:
    """兼容旧调用：规则发现 + 旁注分段拼接（旁注不得冒充规则终态）。"""
    base_s = str(base or "").strip()
    app_s = str(appendix or "").strip()
    if not app_s:
        return base_s
    if not base_s:
        return (
            "【规则发现】（无）\n\n"
            "—— 以下为系统观察/待判断，非审计师最终结论 ——\n"
            f"{app_s}"
        )
    if app_s in base_s:
        return base_s
    rule_block = base_s if base_s.startswith("【规则发现】") else f"【规则发现】\n{base_s}"
    return (
        f"{rule_block}\n\n"
        "—— 以下为系统观察/待判断，非审计师最终结论 ——\n"
        f"{app_s}"
    )


def attach_workpaper_notes(
    assertions: Dict[str, Any],
    *,
    job: Optional[Dict[str, Any]] = None,
    chain_id: str = "",
    contract_res: Optional[Dict[str, Any]] = None,
    amount: Optional[Dict[str, Any]] = None,
    empty_verdict_labels: Optional[Sequence[str]] = None,
    extra_observations: Optional[Sequence[str]] = None,
    extra_pending: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """旁注独立字段；异常说明列分段写入，Yes/No 仍只来自断言规则。"""
    notes = build_workpaper_notes(
        job=job,
        chain_id=chain_id,
        contract_res=contract_res,
        amount=amount,
        empty_verdict_labels=empty_verdict_labels,
        extra_observations=extra_observations,
        extra_pending=extra_pending,
    )
    assertions = dict(assertions)
    # 重复 attach 时优先用首次的规则发现，避免旁注叠写
    rule_exc = str(
        assertions.get("rule_exception")
        or assertions.get("exception")
        or ""
    ).strip()
    marker = "—— 以下为系统观察/待判断"
    if marker in rule_exc:
        rule_exc = rule_exc.split(marker, 1)[0].strip()
    if rule_exc.startswith("【规则发现】"):
        rule_exc = rule_exc[len("【规则发现】") :].lstrip("\n").strip()
    assertions["rule_exception"] = rule_exc
    assertions["system_observation"] = notes["system_observation"]
    assertions["pending_judgment"] = notes["pending_judgment"]
    # 审计师最终结论：仅人工 Gate5 备注，系统不得代填
    auditor = str(
        (job or {}).get("auditor_conclusion")
        or (job or {}).get("conclusion_note")
        or ""
    ).strip()
    assertions["auditor_conclusion"] = auditor
    appendix = notes["exception_appendix"]
    if auditor:
        appendix = (
            f"{appendix}\n\n审计师最终结论：{auditor}".strip()
            if appendix
            else f"审计师最终结论：{auditor}"
        )
    assertions["exception"] = merge_exception_text(rule_exc, appendix)
    return assertions

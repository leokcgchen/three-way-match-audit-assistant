"""Gate5：失败结论追溯（用了哪些字段、怎么测的）。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from src.models.field_values import rule_readable_fields
from src.three_way_match.phrases import expand_qty_role_shorthand, strip_match_score_language
from src.workflow.chain_workspace import (
    docs_for_chain,
    get_sample,
    is_gospd_mode,
    list_business_chains,
    resolve_active_chain_id,
)

logger = logging.getLogger(__name__)

FIELD_CN = {
    "documentNo": "单据编号",
    "orderNo": "订单编号",
    "contractNo": "合同编号",
    "invoiceNo": "发票号码",
    "totalAmount": "价税合计",
    "amount": "金额",
    "quantity": "数量",
    "supplierName": "销方/供应商",
    "buyerName": "购方",
    "documentDate": "单据日期",
    "postingDate": "入账日期",
    "deliveryDate": "发货日期",
    "acceptanceDate": "签收日期",
    "paymentTerms": "付款条款",
    "transportTerms": "运输条款",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _doc_field(
    docs: list[dict[str, Any]],
    doc_type: str,
    *keys: str,
) -> Optional[dict[str, Any]]:
    for d in docs or []:
        if str(d.get("doc_type") or "") != doc_type:
            continue
        readable = rule_readable_fields(d)
        for k in keys:
            v = readable.get(k)
            if v is None or str(v).strip() == "":
                continue
            return {
                "doc_type": doc_type,
                "file_name": d.get("file_name"),
                "field_key": k,
                "field_label": FIELD_CN.get(k, k),
                "value": v,
            }
    return None


def _status_bad(st: Any) -> bool:
    s = str(st or "").strip().upper()
    return s in {"FAIL", "失败", "不通过", "WARNING", "WARN", "需关注", "INCOMPLETE"}


def _join_summary(base: str, extra: list[str]) -> str:
    seen = {base} if base else set()
    bits = [x for x in extra if x and x not in seen]
    if not bits:
        return base
    return (base + "；" if base else "") + "；".join(bits)


def _invoice_posting_field(docs: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    hit = _doc_field(docs, "invoice", "postingDate")
    if hit:
        return hit
    for d in docs or []:
        if str(d.get("doc_type") or "") != "invoice":
            continue
        v = d.get("ledger_posting_date")
        if v is None or str(v).strip() == "":
            continue
        return {
            "doc_type": "invoice",
            "file_name": d.get("file_name"),
            "field_key": "postingDate",
            "field_label": "入账日期",
            "value": v,
        }
    return None


def _cmp_rows(match: dict[str, Any]) -> list[dict[str, Any]]:
    raw = match.get("comparisons") or match.get("match_result", {}).get("comparisons") or []
    out = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        consistent = c.get("is_consistent")
        if consistent is False:
            status = "FAIL"
        elif consistent is True:
            status = str(c.get("status") or "PASS")
        else:
            status = str(c.get("status") or "")
        out.append(
            {
                "field_name": c.get("field_name") or c.get("field") or "",
                "status": status,
                "is_consistent": consistent,
                "order_value": c.get("order_value") or c.get("po_value"),
                "receipt_value": c.get("receipt_value") or c.get("wr_value"),
                "invoice_value": c.get("invoice_value") or c.get("inv_value"),
                "message": c.get("diff_description")
                or c.get("message")
                or c.get("note")
                or "",
                "auditor_explain": c.get("auditor_explain") or "",
                "pick_reason": c.get("pick_reason") or "",
            }
        )
    return out


def _match_as_dict(blob: Any) -> dict[str, Any]:
    if hasattr(blob, "model_dump"):
        return blob.model_dump()
    return blob if isinstance(blob, dict) else {}


def prune_acknowledgements_for_chain(
    acks: dict[str, Any] | None,
    chain_id: str,
) -> dict[str, Any]:
    """重测某一笔时，只清该笔 finding 的确认，避免误清其它链。"""
    root = dict(acks or {})
    needle = f":{chain_id}"
    return {k: v for k, v in root.items() if needle not in str(k)}


def keep_acknowledgements_for_chains(
    acks: dict[str, Any] | None,
    chain_ids: list[str] | set[str],
) -> dict[str, Any]:
    """只保留仍存在业务链的确认（追加第二笔时勿清空第一笔）。"""
    root = dict(acks or {})
    if not root:
        return {}
    ids = [str(c).strip() for c in (chain_ids or []) if str(c).strip()]
    if not ids:
        return root
    out: dict[str, Any] = {}
    for k, v in root.items():
        key = str(k)
        if any(f":{cid}" in key for cid in ids):
            out[k] = v
        elif ":job" in key:
            out[k] = v
    return out


def _collect_bundle_findings(
    *,
    job: dict[str, Any],
    chain_id: Optional[str],
    docs: list[dict[str, Any]],
    evidence: Any,
    three_way: Any,
    amount: Any,
    contract: Any,
    acks: dict[str, Any],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    chain_key = chain_id or "job"
    chain_tag = f" · {chain_id}" if chain_id else ""

    if evidence:
        st = str((evidence or {}).get("status") or "")
        fid = f"evidence:{chain_key}"
        findings.append(
            {
                "finding_id": fid,
                "chain_id": chain_id,
                "step": "evidence_match",
                "step_label": "证据匹配",
                "title": f"证据匹配{chain_tag} · {st or '-'}",
                "status": st or "UNKNOWN",
                "blocking": _status_bad(st),
                "method": "按业务号/关键字段把合同、订单、发货、签收、发票、回款串成证据链；Gate4 前须人工确认关系。",
                "summary": (evidence or {}).get("message")
                or (evidence or {}).get("human_readable_summary")
                or "",
                "fields_used": [
                    x
                    for x in [
                        _doc_field(docs, "order", "orderNo", "documentNo"),
                        _doc_field(docs, "contract", "contractNo", "documentNo"),
                        _doc_field(docs, "invoice", "invoiceNo", "documentNo"),
                        _doc_field(docs, "receipt", "documentNo", "acceptanceDate"),
                    ]
                    if x
                ],
                "comparisons": [],
                "go_field_confirm": True,
                "retest_path": "evidence-match",
                "acknowledged": bool((acks.get(fid) or {}).get("genuine")),
                "ack_reason": (acks.get(fid) or {}).get("reason"),
            }
        )

    if three_way and isinstance(three_way, dict):
        match = _match_as_dict(three_way.get("match_result")) or (
            three_way if "comparisons" in three_way else {}
        )
        if not isinstance(match, dict):
            match = {}
        # 三单状态不得用综合 overall_status（其中含截止失败）
        three_st = str(
            three_way.get("three_way_status")
            or match.get("overall_status")
            or three_way.get("status")
            or ""
        )
        failed_cmps = [
            c
            for c in _cmp_rows(three_way if three_way.get("comparisons") else match)
            if _status_bad(c.get("status"))
        ]
        three_extra: list[str] = []
        cutoff_extra: list[str] = []
        try:
            from src.audit.gospd01030_assertions import build_gospd01030_assertions

            goals = list(
                (job.get("plan") or {}).get("goal_ids") or job.get("goal_ids") or []
            )
            if is_gospd_mode(job) and "gospd01030" in goals:
                assertions = build_gospd01030_assertions(
                    docs=docs, job=job, three_way=three_way, chain_id=chain_id or ""
                )
                for gap in assertions.get("gaps") or []:
                    g = str(gap)
                    if g.startswith(("截止性未通过", "三单匹配未通过")):
                        continue
                    if any(x in g for x in ("发货", "签收", "验收", "订单", "必要单据")):
                        three_extra.append(g)
                    elif any(x in g for x in ("过账", "入账", "控制权", "期间", "应收")):
                        cutoff_extra.append(g)
        except Exception:
            logger.exception(
                "build_gospd01030_assertions failed during conclusion_trace chain=%s",
                chain_id,
            )
        fid = f"three_way:{chain_key}:overall"
        findings.append(
            {
                "finding_id": fid,
                "chain_id": chain_id,
                "step": "three_way",
                "module": "three_way",
                "step_label": "三单匹配",
                "title": f"三单匹配{chain_tag} · {three_st or '-'}",
                "status": str(three_st or "UNKNOWN"),
                "blocking": _status_bad(three_st),
                "method": (
                    "先核是否同一笔业务，再比对购方名称、价税合计，以及数量三角色："
                    "订单数量、签收/验收数量、发票开票数量。"
                    "放行看硬规则。订单日、开票日、签收日、入账日不要求同一天，日期只进截止性。"
                ),
                "summary": _join_summary(
                    strip_match_score_language(
                        expand_qty_role_shorthand(
                            str(
                                three_way.get("three_way_summary")
                                or match.get("summary")
                                or match.get("human_readable_summary")
                                or three_way.get("summary")
                                or three_way.get("human_readable_summary")
                                or ""
                            )
                        )
                    ),
                    three_extra,
                ),
                "fields_used": [
                    x
                    for x in [
                        _doc_field(
                            docs, "order", "buyerName", "supplierName", "totalAmount", "quantity", "orderNo"
                        ),
                        _doc_field(docs, "receipt", "quantity", "orderNo", "buyerName"),
                        _doc_field(
                            docs, "invoice", "buyerName", "totalAmount", "quantity", "invoiceNo"
                        ),
                    ]
                    if x
                ],
                "comparisons": failed_cmps,
                "decision": three_way.get("decision") or match.get("decision"),
                "decision_reasons": list(
                    three_way.get("decision_reasons")
                    or match.get("decision_reasons")
                    or []
                ),
                "hold_reason_code": three_way.get("hold_reason_code")
                or match.get("hold_reason_code"),
                "quantity_roles": three_way.get("quantity_roles")
                or match.get("quantity_roles")
                or {},
                "slot_reasons": three_way.get("slot_reasons")
                or match.get("slot_reasons")
                or {},
                "erp_review": three_way.get("erp_review") or match.get("erp_review") or {},
                "go_field_confirm": True,
                "retest_path": "three-way-cutoff",
                "acknowledged": bool((acks.get(fid) or {}).get("genuine")),
                "ack_reason": (acks.get(fid) or {}).get("reason"),
            }
        )

        cutoff = _match_as_dict(three_way.get("cutoff_result"))
        cutoff_st = str(
            three_way.get("cutoff_status")
            or three_way.get("cutoff_test_status")
            or (cutoff or {}).get("测试状态")
            or (cutoff or {}).get("status")
            or ""
        )
        if cutoff_st and cutoff_st.upper() not in {"NONE", "-"}:
            cfid = f"cutoff:{chain_key}"
            posting = _invoice_posting_field(docs)
            control = _doc_field(docs, "receipt", "acceptanceDate", "deliveryDate")
            findings.append(
                {
                    "finding_id": cfid,
                    "chain_id": chain_id,
                    "step": "cutoff",
                    "module": "cutoff",
                    "step_label": "截止性",
                    "title": f"截止性{chain_tag} · {cutoff_st}",
                    "status": cutoff_st,
                    "blocking": _status_bad(cutoff_st),
                    "method": (
                        "以签收/验收日作为控制权转移日，对比序时账入账日与报告期末，"
                        "判断收入是否记入正确期间。不要求开票日、签收日、入账日为同一天；"
                        "禁止用发票开票日冒充入账日。"
                    ),
                    "summary": _join_summary(
                        str(
                            three_way.get("cutoff_summary")
                            or (cutoff or {}).get("问题描述")
                            or (cutoff or {}).get("message")
                            or (cutoff or {}).get("summary")
                            or three_way.get("cutoff_skipped_reason")
                            or ""
                        ),
                        cutoff_extra,
                    ),
                    "fields_used": [x for x in [posting, control] if x],
                    "comparisons": [],
                    "period": {
                        "签收/控制权日": (cutoff or {}).get("应确认日期")
                        or (cutoff or {}).get("expected_revenue_date")
                        or (control or {}).get("value"),
                        "序时账入账日": (posting or {}).get("value")
                        or (cutoff or {}).get("入账日期"),
                        "报告期末": job.get("period_end")
                        or ((job.get("plan") or {}).get("period_end")),
                        "偏差天数": (cutoff or {}).get("偏差天数")
                        if (cutoff or {}).get("偏差天数") is not None
                        else (cutoff or {}).get("deviation_days"),
                    },
                    "go_field_confirm": True,
                    "retest_path": "three-way-cutoff",
                    "acknowledged": bool((acks.get(cfid) or {}).get("genuine")),
                    "ack_reason": (acks.get(cfid) or {}).get("reason"),
                }
            )

    for key, step, label, path, method in (
        (
            amount,
            "amount_test",
            "金额准确性",
            "amount-test",
            "按订单/发票金额、数量、折扣等字段做金额勾稽与重算。",
        ),
        (
            contract,
            "contract_terms",
            "合同条款",
            "contract-terms",
            "从合同抽取付款/控制权转移/运输等条款并与审阅口径比对。",
        ),
    ):
        if not key or not isinstance(key, dict):
            continue
        st = str(key.get("status") or key.get("overall_status") or "")
        fid = f"{step}:{chain_key}"
        findings.append(
            {
                "finding_id": fid,
                "chain_id": chain_id,
                "step": step,
                "step_label": label,
                "title": f"{label}{chain_tag} · {st or '-'}",
                "status": st or "UNKNOWN",
                "blocking": _status_bad(st),
                "method": method,
                "summary": str(key.get("message") or key.get("human_readable_summary") or ""),
                "fields_used": [
                    x
                    for x in [
                        _doc_field(docs, "order", "totalAmount", "quantity", "paymentTerms"),
                        _doc_field(docs, "invoice", "totalAmount", "amount", "taxAmount"),
                        _doc_field(docs, "contract", "paymentTerms", "controlTransferTerms"),
                    ]
                    if x
                ],
                "comparisons": [],
                "go_field_confirm": True,
                "retest_path": path,
                "acknowledged": bool((acks.get(fid) or {}).get("genuine")),
                "ack_reason": (acks.get(fid) or {}).get("reason"),
            }
        )

    return findings


def build_conclusion_trace(
    job: dict[str, Any],
    *,
    chain_id: Optional[str] = None,
) -> dict[str, Any]:
    """汇总可点开追溯的结论项。

    chain_id 有值时只扫该笔（Gate5 当前笔页加速）；空则 GOSPD 扫全部已测业务链。
    """
    acks = dict(job.get("finding_acknowledgements") or {})
    findings: list[dict[str, Any]] = []
    classified = list(job.get("classified") or [])
    active = resolve_active_chain_id(job) if is_gospd_mode(job) else None
    scope = str(chain_id or "").strip() or None

    if is_gospd_mode(job):
        if scope:
            chains = [scope]
        else:
            chains = [
                c.get("chain_id")
                for c in list_business_chains(classified)
                if c.get("chain_id") and c.get("chain_id") != "未识别业务号"
            ]
            if not chains and active:
                chains = [active]
        for cid in chains:
            sample = get_sample(job, cid)
            # 未跑过任何测试的笔跳过（Gate5 另有「分笔未测完」门禁）
            if not any(
                sample.get(k)
                for k in ("evidence", "three_way", "amount_test", "contract_terms")
            ):
                continue
            docs = docs_for_chain(classified, cid) or classified
            findings.extend(
                _collect_bundle_findings(
                    job=job,
                    chain_id=cid,
                    docs=docs,
                    evidence=sample.get("evidence"),
                    three_way=sample.get("three_way"),
                    amount=sample.get("amount_test"),
                    contract=sample.get("contract_terms"),
                    acks=acks,
                )
            )
    else:
        findings.extend(
            _collect_bundle_findings(
                job=job,
                chain_id=None,
                docs=classified,
                evidence=job.get("evidence"),
                three_way=job.get("three_way"),
                amount=job.get("amount_test"),
                contract=job.get("contract_terms"),
                acks=acks,
            )
        )

    blocking = [f for f in findings if f.get("blocking")]
    unacked = [f for f in blocking if not f.get("acknowledged")]
    # GOSPD：当前笔未确认项（Gate5 确认结论只拦这一笔；其它笔可切样本再处理）
    if active:
        unacked_active = [
            f
            for f in unacked
            if not f.get("chain_id") or f.get("chain_id") == active
        ]
        blocking_active = [
            f
            for f in blocking
            if not f.get("chain_id") or f.get("chain_id") == active
        ]
    else:
        unacked_active = list(unacked)
        blocking_active = list(blocking)
    return {
        "chain_id": active,
        "findings": findings,
        "blocking_count": len(blocking),
        "unacked_blocking_count": len(unacked),
        "unacked_blocking_count_active": len(unacked_active),
        "blocking_count_active": len(blocking_active),
        "can_confirm_as_genuine_path": len(unacked_active) == 0,
        "message": (
            "当前笔不通过项已全部确认为单据问题，可对本笔确认 Gate5。"
            if blocking_active and not unacked_active
            else (
                "请点开当前笔不通过项：核对字段与测法；属单据问题则「确认为单据问题」，属系统抽错则去改字段并重测。"
                if unacked_active
                else (
                    "当前笔无阻塞性不通过项。"
                    + (
                        f"（全任务另有 {len(unacked)} 项在其它笔，切换样本后再处理）"
                        if unacked
                        else ""
                    )
                )
            )
        ),
    }


def acknowledge_finding(
    job: dict[str, Any],
    *,
    finding_id: str,
    genuine: bool = True,
    reason: str = "",
) -> dict[str, Any]:
    root = dict(job.get("finding_acknowledgements") or {})
    fid = str(finding_id)
    if genuine:
        root[fid] = {
            "genuine": True,
            "reason": str(reason or "").strip(),
            "at": _now(),
        }
    else:
        # 撤销确认：删除条目，避免残留 genuine=false 干扰
        root.pop(fid, None)
    return root


def acknowledge_findings_batch(
    job: dict[str, Any],
    *,
    chain_id: Optional[str] = None,
    genuine: bool = True,
    reason: str = "",
    only_blocking: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    """批量确认/撤销不通过项。

    chain_id 有值时只处理该笔（GOSPD 本笔放行）；空则处理全部。
    返回 (新 acknowledgements, 触达的 finding_id 列表)。
    """
    trace = build_conclusion_trace(job)
    root = dict(job.get("finding_acknowledgements") or {})
    want = str(chain_id or "").strip()
    touched: list[str] = []
    default_reason = str(reason or "").strip() or (
        "本笔批量确认为单据问题" if genuine else ""
    )
    for f in trace.get("findings") or []:
        if only_blocking and not f.get("blocking"):
            continue
        fid = str(f.get("finding_id") or "").strip()
        if not fid:
            continue
        fcid = str(f.get("chain_id") or "").strip()
        if want:
            # 无 chain_id 的旧项：仅在非分笔或与当前笔并存时纳入
            if fcid and fcid != want:
                continue
            if not fcid and is_gospd_mode(job):
                continue
        if genuine and f.get("acknowledged"):
            continue
        if not genuine and not f.get("acknowledged"):
            continue
        root = acknowledge_finding(
            {**job, "finding_acknowledgements": root},
            finding_id=fid,
            genuine=genuine,
            reason=default_reason,
        )
        touched.append(fid)
    return root, touched

"""Gate5 失败结论追溯 + 确认为单据问题。"""

from __future__ import annotations

import pytest

from src.workflow.conclusion_trace import acknowledge_finding, build_conclusion_trace
from src.workflow.job_store import JOB_STORE


def test_build_trace_marks_blocking_and_ack():
    job = {
        "classified": [
            {
                "doc_type": "order",
                "file_name": "o.pdf",
                "fields": {"orderNo": "SO1", "totalAmount": 100},
            },
            {
                "doc_type": "invoice",
                "file_name": "i.pdf",
                "fields": {"invoiceNo": "INV1", "totalAmount": 90},
            },
        ],
        "amount_test": {"status": "FAIL", "message": "金额不一致"},
        "finding_acknowledgements": {},
        "plan": {"goal_ids": []},
    }
    trace = build_conclusion_trace(job)
    assert trace["blocking_count"] >= 1
    assert trace["unacked_blocking_count"] >= 1
    fid = next(f["finding_id"] for f in trace["findings"] if f["blocking"])
    assert any(x.get("field_key") for x in next(
        f for f in trace["findings"] if f["finding_id"] == fid
    )["fields_used"])

    job["finding_acknowledgements"] = acknowledge_finding(
        job, finding_id=fid, genuine=True, reason="单据少记"
    )
    trace2 = build_conclusion_trace(job)
    assert trace2["unacked_blocking_count"] == 0
    assert trace2["can_confirm_as_genuine_path"] is True


def test_build_trace_can_scope_to_one_chain():
    job = {
        "goal_ids": ["gospd01030"],
        "plan": {"goal_ids": ["gospd01030"]},
        "active_chain_id": "SO-A",
        "classified": [
            {
                "doc_type": "invoice",
                "file_name": "a.pdf",
                "fields": {"orderNo": "SO-A", "totalAmount": 1},
            },
            {
                "doc_type": "invoice",
                "file_name": "b.pdf",
                "fields": {"orderNo": "SO-B", "totalAmount": 1},
            },
        ],
        "gospd_sample_results": {
            "SO-A": {"three_way": {"overall_status": "FAIL", "three_way_status": "FAIL"}},
            "SO-B": {"three_way": {"overall_status": "FAIL", "three_way_status": "FAIL"}},
        },
        "finding_acknowledgements": {},
    }
    full = build_conclusion_trace(job)
    scoped = build_conclusion_trace(job, chain_id="SO-A")
    assert all(
        (not f.get("chain_id")) or f.get("chain_id") == "SO-A"
        for f in scoped["findings"]
    )
    assert len(full["findings"]) >= len(scoped["findings"])


def test_three_way_finding_keeps_one_to_many_fulfillment_evidence():
    fulfillment = {
        "light": "RED",
        "complete_set": True,
        "flags": ["SET_CLAIMED_INCOMPLETE"],
        "rows": [{"ordered_qty": "100", "received_qty": "100", "invoiced_qty": "70"}],
    }
    job = {
        "classified": [],
        "three_way": {
            "three_way_status": "FAIL",
            "decision": "HOLD_REVIEW",
            "fulfillment": fulfillment,
        },
        "finding_acknowledgements": {},
        "plan": {"goal_ids": []},
    }

    trace = build_conclusion_trace(job)
    finding = next(item for item in trace["findings"] if item["module"] == "three_way")

    assert finding["fulfillment"] == fulfillment


def test_confirm_conclusion_requires_finding_ack():
    job = JOB_STORE.create(title="trace-gate")
    jid = job["job_id"]
    JOB_STORE.update(
        jid,
        plan={
            "goal_ids": [],
            "required_steps": ["amount_test"],
            "goals": [],
            "skipped_steps": [],
        },
        amount_test={"status": "FAIL", "message": "少金额"},
        fields_confirmed=True,
        matching_confirmed=True,
        advisory_candidates=[],
    )
    with pytest.raises(ValueError, match="不通过结论未确认"):
        JOB_STORE.confirm_conclusion(jid)

    cur = JOB_STORE.get(jid)
    trace = build_conclusion_trace(cur)
    fid = next(f["finding_id"] for f in trace["findings"] if f["blocking"])
    acks = acknowledge_finding(cur, finding_id=fid, genuine=True, reason="真问题")
    JOB_STORE.update(jid, finding_acknowledgements=acks)
    out = JOB_STORE.confirm_conclusion(jid)
    assert out["conclusion_confirmed"] is True


def test_invalidate_clears_finding_acknowledgements():
    job = JOB_STORE.create(title="trace-clear")
    jid = job["job_id"]
    JOB_STORE.update(
        jid,
        finding_acknowledgements={"amount_test:job": {"genuine": True, "reason": "x"}},
        amount_test={"status": "FAIL"},
    )
    JOB_STORE.invalidate_by_targets(jid, ["amount"])
    cur = JOB_STORE.get(jid)
    assert cur.get("finding_acknowledgements") == {}


def test_gospd_invalidate_fields_keeps_other_chain_acks():
    job = JOB_STORE.create(title="trace-gospd-ack")
    jid = job["job_id"]
    JOB_STORE.update(
        jid,
        goal_ids=["gospd01030"],
        plan={
            "goal_ids": ["gospd01030"],
            "required_steps": ["three_way_cutoff", "relations_gate4", "conclusion_gate5"],
            "goals": [],
            "skipped_steps": [],
        },
        finding_acknowledgements={
            "three_way:SO25-0281:overall": {"genuine": True, "reason": "已确认"},
            "cutoff:SO25-0281": {"genuine": True, "reason": "已确认"},
            "three_way:SO25-0282:overall": {"genuine": True, "reason": "新笔"},
        },
    )
    # 改第二笔字段 → 只作废第二笔确认
    JOB_STORE.invalidate_downstream_from_fields(jid, prune_ack_chains=["SO25-0282"])
    cur = JOB_STORE.get(jid)
    acks = cur.get("finding_acknowledgements") or {}
    assert "three_way:SO25-0281:overall" in acks
    assert "cutoff:SO25-0281" in acks
    assert "three_way:SO25-0282:overall" not in acks


def test_keep_acknowledgements_for_chains():
    from src.workflow.conclusion_trace import keep_acknowledgements_for_chains

    acks = {
        "three_way:SO-A:overall": {"genuine": True},
        "three_way:SO-B:overall": {"genuine": True},
        "amount_test:job": {"genuine": True},
    }
    kept = keep_acknowledgements_for_chains(acks, ["SO-A"])
    assert "three_way:SO-A:overall" in kept
    assert "three_way:SO-B:overall" not in kept
    assert "amount_test:job" in kept


def test_save_chain_sample_prunes_only_that_chain_acks():
    from src.workflow.conclusion_trace import prune_acknowledgements_for_chain

    acks = {
        "amount_test:SO-A": {"genuine": True},
        "amount_test:SO-B": {"genuine": True},
        "evidence:SO-A": {"genuine": True},
    }
    pruned = prune_acknowledgements_for_chain(acks, "SO-A")
    assert "amount_test:SO-B" in pruned
    assert "amount_test:SO-A" not in pruned
    assert "evidence:SO-A" not in pruned


def test_gospd_trace_scans_all_tested_chains():
    # 用金额测避免 01030 断言缺口干扰「当前笔 PASS」判定
    job = {
        "goal_ids": ["gospd01010"],
        "plan": {"goal_ids": ["gospd01010"], "required_steps": ["amount_test"]},
        "classified": [
            {"doc_type": "order", "file_name": "a.pdf", "fields": {"orderNo": "SO-A", "documentNo": "SO-A"}},
            {"doc_type": "invoice", "file_name": "ai.pdf", "fields": {"invoiceNo": "IA", "documentNo": "SO-A"}},
            {"doc_type": "order", "file_name": "b.pdf", "fields": {"orderNo": "SO-B", "documentNo": "SO-B"}},
            {"doc_type": "invoice", "file_name": "bi.pdf", "fields": {"invoiceNo": "IB", "documentNo": "SO-B"}},
        ],
        "active_chain_id": "SO-A",
        "gospd_sample_results": {
            "SO-A": {"amount_test": {"status": "PASS"}},
            "SO-B": {"amount_test": {"status": "FAIL", "message": "B坏"}},
        },
        "finding_acknowledgements": {},
    }
    from src.workflow.chain_workspace import list_business_chains

    chains = [c["chain_id"] for c in list_business_chains(job["classified"])]
    if "SO-A" not in chains or "SO-B" not in chains:
        pytest.skip(f"chain detect unexpected: {chains}")
    trace = build_conclusion_trace(job)
    ids = {f["finding_id"] for f in trace["findings"]}
    assert any("SO-B" in i for i in ids)
    assert trace["unacked_blocking_count"] >= 1
    # 当前笔 SO-A 已通过：本笔未确认阻塞应为 0（不挡本笔 Gate5）
    assert trace.get("unacked_blocking_count_active", 0) == 0
    assert trace["can_confirm_as_genuine_path"] is True


def test_gospd_confirm_conclusion_only_blocks_active_chain():
    """确认当前笔 Gate5 不要求其它笔已 ack 的不通过项。"""
    job = JOB_STORE.create(title="gospd-gate5-active")
    jid = job["job_id"]
    classified = [
        {
            "doc_type": "order",
            "file_name": "SO-A_order.pdf",
            "fields": {"orderNo": "SO-A", "documentNo": "SO-A"},
        },
        {
            "doc_type": "invoice",
            "file_name": "SO-A_inv.pdf",
            "fields": {"invoiceNo": "IA", "documentNo": "SO-A"},
        },
        {
            "doc_type": "order",
            "file_name": "SO-B_order.pdf",
            "fields": {"orderNo": "SO-B", "documentNo": "SO-B"},
        },
        {
            "doc_type": "invoice",
            "file_name": "SO-B_inv.pdf",
            "fields": {"invoiceNo": "IB", "documentNo": "SO-B"},
        },
    ]
    from src.workflow.chain_workspace import list_business_chains

    chains = [c["chain_id"] for c in list_business_chains(classified)]
    if "SO-A" not in chains or "SO-B" not in chains:
        pytest.skip(f"chain detect unexpected: {chains}")

    JOB_STORE.update(
        jid,
        goal_ids=["gospd01010"],
        plan={
            "goal_ids": ["gospd01010"],
            "required_steps": ["amount_test", "relations_gate4", "conclusion_gate5"],
            "goals": [],
            "skipped_steps": [],
        },
        classified=classified,
        active_chain_id="SO-A",
        gospd_sample_results={
            "SO-A": {
                "matching_confirmed": True,
                "fields_confirmed": True,
                "amount_test": {"status": "PASS"},
            },
            "SO-B": {
                "matching_confirmed": True,
                "fields_confirmed": True,
                "amount_test": {"status": "FAIL", "message": "B坏"},
            },
        },
        fields_confirmed=True,
        matching_confirmed=True,
        amount_test={"status": "PASS"},
        finding_acknowledgements={},
        advisory_candidates=[],
    )
    # 其它笔有未 ack FAIL，仍可确认当前笔
    out = JOB_STORE.confirm_conclusion(jid)
    sample_a = (out.get("gospd_sample_results") or {}).get("SO-A") or {}
    assert sample_a.get("conclusion_confirmed") is True
    # 全链未齐 → 顶层导出门禁仍为 False
    assert out.get("conclusion_confirmed") is False


def test_acknowledge_findings_batch_active_chain_only():
    from src.workflow.conclusion_trace import acknowledge_findings_batch

    job = {
        "goal_ids": ["gospd01010"],
        "plan": {"goal_ids": ["gospd01010"], "required_steps": ["amount_test"]},
        "classified": [
            {"doc_type": "order", "file_name": "a.pdf", "fields": {"orderNo": "SO-A", "documentNo": "SO-A"}},
            {"doc_type": "invoice", "file_name": "ai.pdf", "fields": {"invoiceNo": "IA", "documentNo": "SO-A"}},
            {"doc_type": "order", "file_name": "b.pdf", "fields": {"orderNo": "SO-B", "documentNo": "SO-B"}},
            {"doc_type": "invoice", "file_name": "bi.pdf", "fields": {"invoiceNo": "IB", "documentNo": "SO-B"}},
        ],
        "active_chain_id": "SO-A",
        "gospd_sample_results": {
            "SO-A": {"amount_test": {"status": "FAIL", "message": "A坏"}},
            "SO-B": {"amount_test": {"status": "FAIL", "message": "B坏"}},
        },
        "finding_acknowledgements": {},
    }
    from src.workflow.chain_workspace import list_business_chains

    chains = [c["chain_id"] for c in list_business_chains(job["classified"])]
    if "SO-A" not in chains or "SO-B" not in chains:
        pytest.skip(f"chain detect unexpected: {chains}")

    root, touched = acknowledge_findings_batch(
        job, chain_id="SO-A", genuine=True, reason="批量"
    )
    assert touched
    assert all("SO-B" not in fid for fid in touched)
    job2 = {**job, "finding_acknowledgements": root}
    trace = build_conclusion_trace(job2)
    assert trace.get("unacked_blocking_count_active", 1) == 0
    assert trace["unacked_blocking_count"] >= 1


def test_release_active_chain_acks_and_confirms():
    job = JOB_STORE.create(title="release-chain")
    jid = job["job_id"]
    classified = [
        {"doc_type": "order", "file_name": "SO-A_order.pdf", "fields": {"orderNo": "SO-A", "documentNo": "SO-A"}},
        {"doc_type": "invoice", "file_name": "SO-A_inv.pdf", "fields": {"invoiceNo": "IA", "documentNo": "SO-A"}},
    ]
    from src.workflow.chain_workspace import list_business_chains

    chains = [c["chain_id"] for c in list_business_chains(classified)]
    if "SO-A" not in chains:
        pytest.skip(f"chain detect unexpected: {chains}")

    JOB_STORE.update(
        jid,
        goal_ids=["gospd01010"],
        plan={
            "goal_ids": ["gospd01010"],
            "required_steps": ["amount_test", "relations_gate4", "conclusion_gate5"],
            "goals": [],
            "skipped_steps": [],
        },
        classified=classified,
        active_chain_id="SO-A",
        gospd_sample_results={
            "SO-A": {
                "matching_confirmed": True,
                "fields_confirmed": True,
                "amount_test": {"status": "FAIL", "message": "少金额"},
            },
        },
        fields_confirmed=True,
        matching_confirmed=True,
        amount_test={"status": "FAIL", "message": "少金额"},
        finding_acknowledgements={},
        advisory_candidates=[],
    )
    out = JOB_STORE.release_active_chain(jid, reason="测试放行", ack_unacked=True)
    assert out["acknowledged_finding_ids"]
    sample = (out["job"].get("gospd_sample_results") or {}).get("SO-A") or {}
    assert sample.get("conclusion_confirmed") is True


def test_release_heals_gate4_when_only_job_top_confirmed():
    """进度条/顶层已 Gate4，但 sample 漏写时，本笔放行应能走通。"""
    job = JOB_STORE.create(title="heal-gate4")
    jid = job["job_id"]
    classified = [
        {"doc_type": "order", "file_name": "SO-A_order.pdf", "fields": {"orderNo": "SO-A", "documentNo": "SO-A"}},
        {"doc_type": "invoice", "file_name": "SO-A_inv.pdf", "fields": {"invoiceNo": "IA", "documentNo": "SO-A"}},
    ]
    from src.workflow.chain_workspace import list_business_chains, sample_matching_ok

    chains = [c["chain_id"] for c in list_business_chains(classified)]
    if "SO-A" not in chains:
        pytest.skip(f"chain detect unexpected: {chains}")

    JOB_STORE.update(
        jid,
        goal_ids=["gospd01010"],
        plan={
            "goal_ids": ["gospd01010"],
            "required_steps": ["amount_test", "relations_gate4", "conclusion_gate5"],
            "goals": [],
            "skipped_steps": [],
        },
        classified=classified,
        active_chain_id="SO-A",
        matching_confirmed=True,
        matching_confirm_sig="sig",
        fields_confirmed=True,
        amount_test={"status": "PASS"},
        gospd_sample_results={
            "SO-A": {
                "fields_confirmed": True,
                # 故意不写 matching_confirmed
                "amount_test": {"status": "PASS"},
            },
        },
        finding_acknowledgements={},
        advisory_candidates=[],
    )
    cur = JOB_STORE.get(jid)
    assert sample_matching_ok(cur, "SO-A") is True
    out = JOB_STORE.release_active_chain(jid, ack_unacked=True)
    sample = (out["job"].get("gospd_sample_results") or {}).get("SO-A") or {}
    assert sample.get("matching_confirmed") is True
    assert sample.get("conclusion_confirmed") is True


def test_confirm_chain_linkage_fields_then_matching():
    job = JOB_STORE.create(title="linkage")
    jid = job["job_id"]
    JOB_STORE.update(
        jid,
        goal_ids=["gospd01030"],
        plan={
            "goal_ids": ["gospd01030"],
            "required_steps": ["relations_gate4", "three_way_cutoff", "conclusion_gate5"],
            "goals": [],
            "skipped_steps": [],
        },
        classified=[
            {
                "doc_type": "order",
                "file_name": "o.pdf",
                "fields": {"orderNo": "SO1", "contractNo": "HT1", "buyerName": "甲", "quantity": 1, "totalAmount": 1},
            },
            {
                "doc_type": "receipt",
                "file_name": "r.pdf",
                "fields": {"orderNo": "SO1", "acceptanceDate": "2025-12-30", "quantity": 1},
            },
            {
                "doc_type": "invoice",
                "file_name": "i.pdf",
                "fields": {"invoiceNo": "I1", "buyerName": "甲", "totalAmount": 1, "documentDate": "2026-01-02"},
            },
        ],
        active_chain_id="SO1",
        evidence={"status": "PASS", "nodes": []},
        relations=[],
        duplicates={},
        gospd_sample_results={"SO1": {"evidence": {"status": "PASS"}}},
    )
    out = JOB_STORE.confirm_chain_linkage(jid)
    assert out["fields_confirmed"] is True
    assert out["matching_confirmed"] is True
    assert "勾稽" in (out.get("message") or "")


def _three_way_cutoff_job(*, three_way_status: str, cutoff_status: str) -> dict:
    return {
        "goal_ids": ["gospd01030"],
        "plan": {"goal_ids": ["gospd01030"], "required_steps": ["three_way_cutoff"]},
        "period_end": "2025-12-31",
        "classified": [
            {
                "doc_type": "order",
                "file_name": "a.pdf",
                "fields": {
                    "orderNo": "SO-A",
                    "documentNo": "SO-A",
                    "buyerName": "甲公司",
                    "totalAmount": 100,
                    "quantity": 1,
                },
            },
            {
                "doc_type": "receipt",
                "file_name": "r.pdf",
                "fields": {"orderNo": "SO-A", "acceptanceDate": "2025-12-20", "quantity": 1},
            },
            {
                "doc_type": "invoice",
                "file_name": "ai.pdf",
                "fields": {
                    "invoiceNo": "IA",
                    "documentNo": "SO-A",
                    "buyerName": "甲公司",
                    "totalAmount": 100,
                    "quantity": 1,
                    "documentDate": "2025-12-22",
                    "postingDate": "2026-01-05",
                },
            },
        ],
        "active_chain_id": "SO-A",
        "gospd_sample_results": {
            "SO-A": {
                "three_way": {
                    "overall_status": "FAIL",
                    "three_way_status": three_way_status,
                    "cutoff_status": cutoff_status,
                    "three_way_summary": "三单匹配通过，得分 100",
                    "cutoff_summary": "入账跨期",
                    "match_result": {
                        "overall_status": three_way_status,
                        "comparisons": [
                            {
                                "field_name": "supplier_name",
                                "is_consistent": True,
                                "order_value": "甲公司",
                                "receipt_value": "甲公司",
                                "invoice_value": "甲公司",
                            },
                            {
                                "field_name": "total_amount",
                                "is_consistent": True,
                                "order_value": 100,
                                "receipt_value": 0,
                                "invoice_value": 100,
                                "diff_description": "签收金额维未测",
                            },
                            {
                                "field_name": "quantity",
                                "is_consistent": True,
                                "order_value": 1,
                                "receipt_value": 1,
                                "invoice_value": 1,
                            },
                        ],
                    },
                    "cutoff_result": {
                        "测试状态": cutoff_status,
                        "问题描述": "入账跨期",
                        "应确认日期": "2025-12-20",
                        "偏差天数": 16,
                    },
                    "cutoff_available": True,
                }
            }
        },
        "finding_acknowledgements": {},
    }


def test_trace_does_not_treat_merged_overall_as_three_way_fail():
    from src.workflow.chain_workspace import list_business_chains

    job = _three_way_cutoff_job(three_way_status="PASS", cutoff_status="FAIL")
    chains = [c["chain_id"] for c in list_business_chains(job["classified"])]
    if "SO-A" not in chains:
        pytest.skip(f"chain detect unexpected: {chains}")
    trace = build_conclusion_trace(job)
    three = [f for f in trace["findings"] if f.get("module") == "three_way"]
    cutoff = [f for f in trace["findings"] if f.get("module") == "cutoff"]
    assert three and not any(f["blocking"] for f in three)
    assert three[0]["status"] == "PASS"
    assert cutoff and all(f["blocking"] for f in cutoff)
    assert cutoff[0]["status"] == "FAIL"
    assert not any("三单综合" in str(f.get("title") or "") for f in trace["findings"])
    assert not any("断言缺口" in str(f.get("title") or "") for f in trace["findings"])
    used_keys = {x.get("field_key") for f in three for x in (f.get("fields_used") or [])}
    assert "acceptanceDate" not in used_keys
    assert "postingDate" not in used_keys
    assert "documentDate" not in used_keys
    assert three[0].get("comparisons") == []
    assert cutoff[0].get("period", {}).get("序时账入账日") == "2026-01-05"
    # 旧任务摘要里的「得分」不得再出现在结论展示
    assert "得分" not in str(three[0].get("summary") or "")
    assert "三单匹配通过" in str(three[0].get("summary") or "")


def test_trace_missing_docs_belong_to_three_way_not_cutoff():
    job = {
        "goal_ids": ["gospd01030"],
        "plan": {"goal_ids": ["gospd01030"], "required_steps": ["three_way_cutoff"]},
        "classified": [
            {
                "doc_type": "order",
                "file_name": "a.pdf",
                "fields": {"orderNo": "SO-A", "documentNo": "SO-A"},
            },
            {
                "doc_type": "invoice",
                "file_name": "ai.pdf",
                "fields": {"invoiceNo": "IA", "documentNo": "SO-A"},
            },
        ],
        "active_chain_id": "SO-A",
        "gospd_sample_results": {
            "SO-A": {
                "three_way": {
                    "overall_status": "FAIL",
                    "status": "INCOMPLETE",
                    "three_way_status": "FAIL",
                    "cutoff_status": "SKIPPED",
                    "three_way_summary": "缺少必要单据：签收/发货",
                    "human_readable_summary": "缺少必要单据：签收/发货",
                    "cutoff_skipped_reason": "缺少必要单据：签收/发货",
                    "incomplete": True,
                    "missing_roles": ["签收/发货"],
                    "match_result": None,
                    "cutoff_available": False,
                }
            }
        },
        "finding_acknowledgements": {},
    }
    from src.workflow.chain_workspace import list_business_chains

    chains = [c["chain_id"] for c in list_business_chains(job["classified"])]
    if "SO-A" not in chains:
        pytest.skip(f"chain detect unexpected: {chains}")
    trace = build_conclusion_trace(job)
    three = next(f for f in trace["findings"] if f.get("module") == "three_way")
    cutoff = [f for f in trace["findings"] if f.get("module") == "cutoff"]
    assert three["blocking"] is True
    assert all(not f["blocking"] for f in cutoff)

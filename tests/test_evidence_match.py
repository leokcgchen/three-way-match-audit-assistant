"""证据匹配单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evidence_match import build_evidence_chain


def test_evidence_chain_pass_core_docs():
    classified = [
        {
            "file_name": "SO25-0281_HT25-0281_01_销售合同.pdf",
            "doc_type": "contract",
            "fields": {"contractNo": "HT25-0281", "documentNo": "HT25-0281"},
        },
        {
            "file_name": "SO25-0281_HT25-0281_02_销售订单.pdf",
            "doc_type": "order",
            "fields": {"documentNo": "SO25-0281"},
        },
        {
            "file_name": "SO25-0281_HT25-0281_04_产品验收单.pdf",
            "doc_type": "receipt",
            "fields": {"documentNo": "SO25-0281"},
        },
        {
            "file_name": "SO25-0281_HT25-0281_05_增值税发票.pdf",
            "doc_type": "invoice",
            "fields": {"invoiceNo": "INV0281", "documentNo": "SO25-0281"},
        },
    ]
    result = build_evidence_chain(
        classified,
        ledger_matched_biz_id="SO25-0281",
        ledger_posting_date="2025-12-10",
    )
    assert result.status == "PASS"
    assert "SO25-0281" in result.anchor_keys
    linked_roles = {n.role for n in result.nodes if n.linked}
    assert {"contract", "order", "receipt", "invoice", "ledger"} <= linked_roles
    print("test_evidence_chain_pass_core_docs: PASS", result.status)


def test_evidence_chain_fail_when_ledger_missing():
    classified = [
        {
            "file_name": "SO25-0281_订单.pdf",
            "doc_type": "order",
            "fields": {"documentNo": "SO25-0281"},
        },
        {
            "file_name": "SO25-0281_签收.pdf",
            "doc_type": "receipt",
            "fields": {"documentNo": "SO25-0281"},
        },
        {
            "file_name": "SO25-0281_发票.pdf",
            "doc_type": "invoice",
            "fields": {"documentNo": "SO25-0281"},
        },
    ]
    result = build_evidence_chain(classified)
    assert result.status == "FAIL"
    assert "序时账" in result.issue_description or "ledger" in result.missing_roles
    print("test_evidence_chain_fail_when_ledger_missing: PASS")


def test_evidence_fuzzy_biz_id_link():
    classified = [
        {
            "file_name": "a.pdf",
            "doc_type": "order",
            "fields": {"documentNo": "SO25-0281"},
        },
        {
            "file_name": "b.pdf",
            "doc_type": "invoice",
            "fields": {"documentNo": "SO250281"},
        },
        {
            "file_name": "c.pdf",
            "doc_type": "receipt",
            "fields": {"documentNo": "SO25-0281"},
        },
        {
            "file_name": "d.pdf",
            "doc_type": "contract",
            "fields": {"contractNo": "HT25-0281"},
        },
    ]
    result = build_evidence_chain(
        classified, ledger_matched_biz_id="SO250281", ledger_posting_date="2025-12-10"
    )
    assert any(n.role == "invoice" and n.linked for n in result.nodes)
    assert any(n.role == "order" and n.linked for n in result.nodes)
    print("test_evidence_fuzzy_biz_id_link: PASS", result.status)


def test_classify_delivery_separate_from_receipt():
    from src.workflow.classify import classify_document

    assert classify_document("SO25-0281_HT25-0281_03_销售发货单.pdf", "") == "delivery"
    assert classify_document("SO25-0281_HT25-0281_04_产品验收单.pdf", "") == "receipt"
    assert classify_document("银行流水_回款.pdf", "") == "payment"
    print("test_classify_delivery_separate_from_receipt: PASS")


def test_evidence_optional_delivery_payment_missing_still_pass():
    """缺发货/回款（可选）时仍 PASS，不挡结论。"""
    classified = [
        {
            "file_name": "SO25-0282_合同.pdf",
            "doc_type": "contract",
            "fields": {"contractNo": "KJHT25-0282"},
        },
        {
            "file_name": "SO25-0282_订单.pdf",
            "doc_type": "order",
            "fields": {"documentNo": "SO25-0282"},
        },
        {
            "file_name": "SO25-0282_验收.pdf",
            "doc_type": "receipt",
            "fields": {"documentNo": "DAP-QS25-0282"},
        },
        {
            "file_name": "SO25-0282_发票.pdf",
            "doc_type": "invoice",
            "fields": {"invoiceNo": "25322025000000002821", "documentNo": "SO25-0282"},
        },
    ]
    result = build_evidence_chain(
        classified,
        ledger_matched_biz_id="SO25-0282",
        ledger_posting_date="2025-12-10",
    )
    assert result.status == "PASS"
    assert "未上传发货单" in result.human_readable_summary
    assert "WARNING" not in result.issue_description.upper()


def test_heal_optional_attachment_warning():
    from src.evidence_match.linker import heal_optional_attachment_warning

    blob = {
        "status": "WARNING",
        "issue_description": "核心证据已按业务编号串联；未上传发货单（可选）；未上传回款资料（可选）",
        "human_readable_summary": "锚点 SO25-0282；已串联",
    }
    assert heal_optional_attachment_warning(blob) is True
    assert blob["status"] == "PASS"
    assert "可选" not in blob["issue_description"]


def test_manual_business_group_confirms_membership_without_machine_cross_reference():
    """人工 business_group_id 是归属权威；编号异构不应把已归组凭证判失败。"""
    classified = [
        {
            "file_name": "contract.pdf",
            "doc_type": "contract",
            "business_group_id": "BG-001",
            "fields": {"contractNo": "HT-001"},
        },
        {
            "file_name": "order.pdf",
            "doc_type": "order",
            "business_group_id": "BG-001",
            "fields": {"orderNo": "SO-009"},
        },
        {
            "file_name": "receipt.pdf",
            "doc_type": "receipt",
            "business_group_id": "BG-001",
            "fields": {"warehouseNo": "YS-101"},
        },
        {
            "file_name": "invoice.pdf",
            "doc_type": "invoice",
            "business_group_id": "BG-001",
            "fields": {"invoiceNo": "INV-778"},
        },
    ]

    result = build_evidence_chain(
        classified,
        ledger_matched_biz_id="HT-001",
    )

    assert result.status == "PASS"
    assert all(node.linked for node in result.nodes if node.role != "other")
    assert "人工业务分组" in result.issue_description


def test_manual_groups_are_hard_boundaries_even_when_machine_number_matches():
    """不同人工组不能因共享订单号合并或互补核心角色。"""
    classified = [
        {"file_name": "a-contract.pdf", "doc_type": "contract", "business_group_id": "A", "fields": {"orderNo": "SO-1"}},
        {"file_name": "a-order.pdf", "doc_type": "order", "business_group_id": "A", "fields": {"orderNo": "SO-1"}},
        {"file_name": "b-receipt.pdf", "doc_type": "receipt", "business_group_id": "B", "fields": {"orderNo": "SO-1"}},
        {"file_name": "b-invoice.pdf", "doc_type": "invoice", "business_group_id": "B", "fields": {"orderNo": "SO-1"}},
    ]

    result = build_evidence_chain(classified, ledger_matched_biz_id="SO-1")

    assert result.status == "FAIL"
    assert not (next(n for n in result.nodes if n.file_name == "a-contract.pdf").linked and next(n for n in result.nodes if n.file_name == "b-invoice.pdf").linked)


def test_manual_group_without_document_keys_keeps_membership_but_requires_ledger():
    """人工归属可连接无编号单据，但不能把缺失序时账放行。"""
    classified = [
        {"file_name": "contract.pdf", "doc_type": "contract", "business_group_id": "BG-NOKEY", "fields": {}},
        {"file_name": "order.pdf", "doc_type": "order", "business_group_id": "BG-NOKEY", "fields": {}},
        {"file_name": "receipt.pdf", "doc_type": "receipt", "business_group_id": "BG-NOKEY", "fields": {}},
        {"file_name": "invoice.pdf", "doc_type": "invoice", "business_group_id": "BG-NOKEY", "fields": {}},
    ]

    result = build_evidence_chain(classified)

    assert result.status == "FAIL"
    assert all(n.linked for n in result.nodes if n.role not in {"ledger", "other"})
    assert "序时账" in result.issue_description


def test_manual_group_rejects_unverified_ledger_binding():
    classified = [
        {"file_name": "contract.pdf", "doc_type": "contract", "business_group_id": "BG-LEDGER", "fields": {"contractNo": "HT-1"}},
        {"file_name": "order.pdf", "doc_type": "order", "business_group_id": "BG-LEDGER", "fields": {"orderNo": "SO-1"}},
        {"file_name": "receipt.pdf", "doc_type": "receipt", "business_group_id": "BG-LEDGER", "fields": {"warehouseNo": "W-1"}},
        {"file_name": "invoice.pdf", "doc_type": "invoice", "business_group_id": "BG-LEDGER", "fields": {"invoiceNo": "INV-1"}},
    ]

    result = build_evidence_chain(classified, ledger_matched_biz_id="WRONG-LEDGER")

    assert result.status == "FAIL"
    assert not next(n for n in result.nodes if n.role == "ledger").linked


def test_manual_group_reference_formats_are_compact_equivalent():
    classified = [
        {"file_name": "contract.pdf", "doc_type": "contract", "business_group_id": "BG-FORMAT", "fields": {"orderNo": "SO-001"}},
        {"file_name": "order.pdf", "doc_type": "order", "business_group_id": "BG-FORMAT", "fields": {"orderNo": "SO001"}},
        {"file_name": "receipt.pdf", "doc_type": "receipt", "business_group_id": "BG-FORMAT", "fields": {"orderNo": "SO-001"}},
        {"file_name": "invoice.pdf", "doc_type": "invoice", "business_group_id": "BG-FORMAT", "fields": {"orderNo": "SO001", "invoiceNo": "INV-1"}},
    ]

    result = build_evidence_chain(classified, ledger_matched_biz_id="SO001")

    assert result.status == "PASS"


def test_manual_group_generates_concrete_manual_relation_edges():
    classified = [
        {"file_name": "contract.pdf", "doc_type": "contract", "business_group_id": "BG-REL", "fields": {"contractNo": "HT-1"}},
        {"file_name": "order.pdf", "doc_type": "order", "business_group_id": "BG-REL", "fields": {"orderNo": "SO-1"}},
        {"file_name": "receipt.pdf", "doc_type": "receipt", "business_group_id": "BG-REL", "fields": {"warehouseNo": "W-1"}},
        {"file_name": "invoice.pdf", "doc_type": "invoice", "business_group_id": "BG-REL", "fields": {"invoiceNo": "INV-1"}},
    ]

    evidence = build_evidence_chain(classified, ledger_matched_biz_id="HT-1").model_dump()
    proposals = __import__("src.audit.relation_proposer", fromlist=["propose_relations_from_evidence"]).propose_relations_from_evidence(classified, evidence)

    assert any(r["from_id"] == "order.pdf" and r["to_id"] == "invoice.pdf" for r in proposals)
    assert any(r["extra"].get("source") == "manual_business_group" for r in proposals)


def test_legacy_role_only_relation_expands_one_order_to_many_invoices():
    from src.audit.relation_proposer import propose_relations_from_evidence

    evidence = {
        "nodes": [
            {"role": "order", "file_name": "order.pdf", "biz_keys": ["SO-1"]},
            {"role": "invoice", "file_name": "invoice-a.pdf", "biz_keys": ["SO-1"]},
            {"role": "invoice", "file_name": "invoice-b.pdf", "biz_keys": ["SO-1"]},
        ],
        "links": [{"from_role": "order", "to_role": "invoice", "shared_keys": ["SO-1"]}],
    }

    proposals = propose_relations_from_evidence([], evidence)

    assert {r["to_id"] for r in proposals} == {"invoice-a.pdf", "invoice-b.pdf"}


def test_legacy_role_only_relation_does_not_guess_without_matching_node_keys():
    from src.audit.relation_proposer import propose_relations_from_evidence

    evidence = {
        "nodes": [
            {"role": "order", "file_name": "order.pdf", "biz_keys": []},
            {"role": "invoice", "file_name": "invoice.pdf", "biz_keys": []},
        ],
        "links": [{"from_role": "order", "to_role": "invoice", "shared_keys": ["SO-1"]}],
    }

    assert propose_relations_from_evidence([], evidence) == []


def test_legacy_role_only_relation_with_empty_shared_keys_generates_nothing():
    from src.audit.relation_proposer import propose_relations_from_evidence

    evidence = {
        "nodes": [
            {"role": "order", "file_name": "order.pdf", "biz_keys": ["SO-1"]},
            {"role": "invoice", "file_name": "invoice-a.pdf", "biz_keys": ["SO-1"]},
            {"role": "invoice", "file_name": "invoice-b.pdf", "biz_keys": ["SO-2"]},
        ],
        "links": [{"from_role": "order", "to_role": "invoice", "shared_keys": []}],
    }

    assert propose_relations_from_evidence([], evidence) == []


def test_legacy_role_only_relation_pairs_only_nodes_with_the_link_key():
    from src.audit.relation_proposer import propose_relations_from_evidence

    evidence = {
        "nodes": [
            {"role": "order", "file_name": "order-1.pdf", "biz_keys": ["SO-1"]},
            {"role": "order", "file_name": "order-2.pdf", "biz_keys": ["SO-2"]},
            {"role": "invoice", "file_name": "invoice-1.pdf", "biz_keys": ["SO-1"]},
            {"role": "invoice", "file_name": "invoice-2.pdf", "biz_keys": ["SO-2"]},
        ],
        "links": [
            {"from_role": "order", "to_role": "invoice", "shared_keys": ["SO-1"]},
            {"from_role": "order", "to_role": "invoice", "shared_keys": ["SO-2"]},
        ],
    }

    proposals = propose_relations_from_evidence([], evidence)

    assert {(r["from_id"], r["to_id"]) for r in proposals} == {
        ("order-1.pdf", "invoice-1.pdf"),
        ("order-2.pdf", "invoice-2.pdf"),
    }


def test_legacy_multi_key_link_pairs_each_document_by_its_actual_shared_key():
    from src.audit.relation_proposer import propose_relations_from_evidence

    evidence = {
        "nodes": [
            {"role": "order", "file_name": "order-1.pdf", "biz_keys": ["SO-1"]},
            {"role": "order", "file_name": "order-2.pdf", "biz_keys": ["SO-2"]},
            {"role": "invoice", "file_name": "invoice-1.pdf", "biz_keys": ["SO-1"]},
            {"role": "invoice", "file_name": "invoice-2.pdf", "biz_keys": ["SO-2"]},
        ],
        "links": [{"from_role": "order", "to_role": "invoice", "shared_keys": ["SO-1", "SO-2"]}],
    }

    proposals = propose_relations_from_evidence([], evidence)

    assert {(r["from_id"], r["to_id"]) for r in proposals} == {
        ("order-1.pdf", "invoice-1.pdf"),
        ("order-2.pdf", "invoice-2.pdf"),
    }


def test_pipeline_evidence_scopes_to_the_invoice_manual_group():
    from src.workflow.pipeline import run_evidence

    classified = [
        {"file_name": "a-contract.pdf", "doc_type": "contract", "business_group_id": "A", "fields": {"orderNo": "SO-A"}},
        {"file_name": "a-order.pdf", "doc_type": "order", "business_group_id": "A", "fields": {"orderNo": "SO-A"}},
        {"file_name": "b-contract.pdf", "doc_type": "contract", "business_group_id": "B", "fields": {"orderNo": "SO-B"}},
        {"file_name": "b-order.pdf", "doc_type": "order", "business_group_id": "B", "fields": {"orderNo": "SO-B"}},
        {"file_name": "b-receipt.pdf", "doc_type": "receipt", "business_group_id": "B", "fields": {"orderNo": "SO-B"}},
        {"file_name": "b-invoice.pdf", "doc_type": "invoice", "business_group_id": "B", "ledger_matched_biz_id": "SO-B", "fields": {"orderNo": "SO-B", "invoiceNo": "INV-B"}},
    ]

    result = run_evidence(classified, with_llm_disambiguation=False)

    assert result["status"] == "PASS"
    assert {node["file_name"] for node in result["nodes"] if node["role"] != "ledger"} == {
        "b-contract.pdf", "b-order.pdf", "b-receipt.pdf", "b-invoice.pdf"
    }


def test_pipeline_evidence_uses_unique_verified_ledger_group_not_first_invoice():
    from src.workflow.pipeline import run_evidence

    classified = [
        {"file_name": "a-invoice.pdf", "doc_type": "invoice", "business_group_id": "A", "fields": {"invoiceNo": "INV-A"}},
        {"file_name": "b-contract.pdf", "doc_type": "contract", "business_group_id": "B", "fields": {"orderNo": "SO-B"}},
        {"file_name": "b-order.pdf", "doc_type": "order", "business_group_id": "B", "fields": {"orderNo": "SO-B"}},
        {"file_name": "b-receipt.pdf", "doc_type": "receipt", "business_group_id": "B", "fields": {"orderNo": "SO-B"}},
        {"file_name": "b-invoice.pdf", "doc_type": "invoice", "business_group_id": "B", "ledger_matched_biz_id": "SO-B", "fields": {"orderNo": "SO-B", "invoiceNo": "INV-B"}},
    ]

    result = run_evidence(classified, with_llm_disambiguation=False)

    assert result["status"] == "PASS"
    assert {node["file_name"] for node in result["nodes"] if node["role"] != "ledger"} == {
        "b-contract.pdf", "b-order.pdf", "b-receipt.pdf", "b-invoice.pdf"
    }


def test_pipeline_evidence_fails_when_multiple_manual_groups_have_verified_ledgers():
    from src.workflow.pipeline import run_evidence

    classified = [
        {"file_name": "a-contract.pdf", "doc_type": "contract", "business_group_id": "A", "fields": {"orderNo": "SO-A"}},
        {"file_name": "a-order.pdf", "doc_type": "order", "business_group_id": "A", "fields": {"orderNo": "SO-A"}},
        {"file_name": "a-receipt.pdf", "doc_type": "receipt", "business_group_id": "A", "fields": {"orderNo": "SO-A"}},
        {"file_name": "a-invoice.pdf", "doc_type": "invoice", "business_group_id": "A", "ledger_matched_biz_id": "SO-A", "fields": {"orderNo": "SO-A"}},
        {"file_name": "b-contract.pdf", "doc_type": "contract", "business_group_id": "B", "fields": {"orderNo": "SO-B"}},
        {"file_name": "b-order.pdf", "doc_type": "order", "business_group_id": "B", "fields": {"orderNo": "SO-B"}},
        {"file_name": "b-receipt.pdf", "doc_type": "receipt", "business_group_id": "B", "fields": {"orderNo": "SO-B"}},
        {"file_name": "b-invoice.pdf", "doc_type": "invoice", "business_group_id": "B", "ledger_matched_biz_id": "SO-B", "fields": {"orderNo": "SO-B"}},
    ]

    result = run_evidence(classified, with_llm_disambiguation=False)

    assert result["status"] == "FAIL"
    assert "歧义" in result["issue_description"]


def test_gospd_three_way_evidence_uses_business_group_ledger_without_contract():
    """三单底稿以订单、签收和发票为核心；合同可选，序时账可按业务分组连接。"""
    from src.workflow.pipeline import run_evidence

    classified = [
        {
            "file_name": "YW-2025-3962_销售订单.pdf",
            "doc_type": "order",
            "sample_business_id": "YW-2025-3962",
            "fields": {"orderNo": "SO-251209-7214"},
        },
        {
            "file_name": "YW-2025-3962_签收验收单.pdf",
            "doc_type": "receipt",
            "sample_business_id": "YW-2025-3962",
            "fields": {"orderNo": "SO-251209-7214"},
        },
        {
            "file_name": "YW-2025-3962_发票.pdf",
            "doc_type": "invoice",
            "sample_business_id": "YW-2025-3962",
            "ledger_matched_biz_id": "YW-2025-3962",
            "ledger_posting_date": "2026-01-02",
            "fields": {"orderNo": "SO-251209-7214", "invoiceNo": "FP-260102-8305"},
        },
    ]

    result = run_evidence(
        classified,
        require_contract=False,
        with_llm_disambiguation=False,
    )

    assert result["status"] == "PASS"
    assert "contract" not in result["missing_roles"]
    ledger = next(node for node in result["nodes"] if node["role"] == "ledger")
    assert ledger["linked"] is True


def test_ledger_cannot_bridge_manual_group_to_unassigned_documents():
    classified = [
        {"file_name": "invoice.pdf", "doc_type": "invoice", "business_group_id": "MANUAL", "fields": {"orderNo": "SO-1"}},
        {"file_name": "contract.pdf", "doc_type": "contract", "fields": {"orderNo": "SO-1"}},
        {"file_name": "order.pdf", "doc_type": "order", "fields": {"orderNo": "SO-1"}},
        {"file_name": "receipt.pdf", "doc_type": "receipt", "fields": {"orderNo": "SO-1"}},
    ]

    result = build_evidence_chain(classified, ledger_matched_biz_id="SO-1")

    assert result.status == "FAIL"
    assert not next(n for n in result.nodes if n.file_name == "contract.pdf").linked


def test_same_semantic_explicit_order_reference_conflict_fails_even_in_manual_group():
    """同为 orderNo 的显式引用不一致，是应阻断的真实冲突。"""
    classified = [
        {
            "file_name": "contract.pdf",
            "doc_type": "contract",
            "business_group_id": "BG-CONFLICT",
            "fields": {"orderNo": "SO-001"},
        },
        {
            "file_name": "order.pdf",
            "doc_type": "order",
            "business_group_id": "BG-CONFLICT",
            "fields": {"orderNo": "SO-002"},
        },
        {
            "file_name": "receipt.pdf",
            "doc_type": "receipt",
            "business_group_id": "BG-CONFLICT",
            "fields": {"orderNo": "SO-001"},
        },
        {
            "file_name": "invoice.pdf",
            "doc_type": "invoice",
            "business_group_id": "BG-CONFLICT",
            "fields": {"orderNo": "SO-001", "invoiceNo": "INV-1"},
        },
    ]

    result = build_evidence_chain(classified, ledger_matched_biz_id="SO-001")

    assert result.status == "FAIL"
    assert "订单号" in result.issue_description


def test_relation_proposals_keep_each_invoice_when_one_order_has_many_invoices():
    """关系候选必须由节点文件对生成，不能按 invoice 角色覆盖为最后一张。"""
    from src.audit.relation_proposer import propose_relations_from_evidence

    classified = [
        {
            "file_name": "contract.pdf",
            "doc_type": "contract",
            "fields": {"orderNo": "SO-001"},
        },
        {
            "file_name": "order.pdf",
            "doc_type": "order",
            "fields": {"orderNo": "SO-001"},
        },
        {
            "file_name": "receipt.pdf",
            "doc_type": "receipt",
            "fields": {"orderNo": "SO-001"},
        },
        {
            "file_name": "invoice-a.pdf",
            "doc_type": "invoice",
            "fields": {"orderNo": "SO-001", "invoiceNo": "INV-A"},
        },
        {
            "file_name": "invoice-b.pdf",
            "doc_type": "invoice",
            "fields": {"orderNo": "SO-001", "invoiceNo": "INV-B"},
        },
    ]
    evidence = build_evidence_chain(classified, ledger_matched_biz_id="SO-001").model_dump()

    proposals = propose_relations_from_evidence(classified, evidence)
    invoice_targets = {
        row["to_id"]
        for row in proposals
        if row["from_id"] == "order.pdf" and row["to_id"].startswith("invoice-")
    }

    assert invoice_targets == {"invoice-a.pdf", "invoice-b.pdf"}


if __name__ == "__main__":
    test_evidence_chain_pass_core_docs()
    test_evidence_chain_fail_when_ledger_missing()
    test_evidence_fuzzy_biz_id_link()
    test_classify_delivery_separate_from_receipt()
    print("test_evidence_match: ALL PASS")

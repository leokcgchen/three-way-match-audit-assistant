"""#5 条款/金额来源溯源 source=rule|llm。"""

from __future__ import annotations

from src.amount_test.pricing_extract import merge_pricing_from_documents
from src.contract_terms.runner import run_contract_clarity_test
from src.models.field_values import get_field_meta


def test_rule_issues_tagged_source_rule(monkeypatch):
    monkeypatch.setattr(
        "src.llm.batch_assist.batch_llm_assist_enabled",
        lambda: False,
    )
    text = "折扣或返利比例由双方另行协商确定。合同编号 HT25-9999。"
    report = run_contract_clarity_test(
        documents=[
            {
                "file_name": "ht.pdf",
                "doc_type": "contract",
                "fields": {},
                "raw_text": text,
            }
        ],
        business_id="SO25-9999",
    )
    issues = report.test_result.issues or []
    assert issues, "期望规则命中至少一条"
    assert all(i.source == "rule" for i in issues)
    assert "REBATE_TERM_AMBIGUOUS" in (report.extracted.get("issue_sources") or {}).get(
        "rule", []
    )
    assert (report.extracted.get("issue_sources") or {}).get("llm") == []


def test_llm_clarity_issues_tagged_and_ingested(monkeypatch):
    monkeypatch.setattr(
        "src.llm.batch_assist.batch_llm_assist_enabled",
        lambda: True,
    )

    def _fake_llm(text, existing_codes, *, allowed_dimensions=None):
        return (
            [
                {
                    "issue_code": "REBATE_TERM_AMBIGUOUS",
                    "dimension": "交易对价",
                    "description": "返利表述不清",
                    "excerpt": "验收合格后确认收入",
                    "confidence": 0.91,
                    "source": "llm",
                }
            ],
            ["mocked"],
        )

    monkeypatch.setattr(
        "src.llm.batch_assist.llm_supplement_clarity_issues",
        _fake_llm,
    )
    # 干净正文：规则不命中，走 LLM 补漏
    text = "验收合格后确认收入。合同编号 HT25-0001。付款：签收后30日电汇。货物验收合格后控制权转移至买方。"
    report = run_contract_clarity_test(
        documents=[
            {
                "file_name": "ht.pdf",
                "doc_type": "contract",
                "fields": {
                    "paymentTerms": "签收后30日电汇",
                    "controlTransferTerms": "验收合格后控制权转移",
                },
                "raw_text": text,
            }
        ],
        business_id="SO25-0001",
        existing_advisory=[],
    )
    llm_issues = [i for i in (report.test_result.issues or []) if i.source == "llm"]
    assert llm_issues, f"期望 LLM 补漏入库，实际 status={report.test_result.test_status} issues={issues_dump(report)}"
    assert "REBATE_TERM_AMBIGUOUS" in report.extracted["issue_sources"]["llm"]
    adv = report.extracted.get("advisory_candidates") or []
    assert any(
        str(c.get("task_type")) == "CONTRACT_CLARITY_REVIEW"
        and str(c.get("status")) in {"PROPOSED", "DROPPED"}
        for c in adv
    )


def test_llm_still_runs_when_rule_hits_other_dimension(monkeypatch):
    """规则只命中交易对价时，未覆盖维仍应调用 LLM（按维补漏）。"""
    monkeypatch.setattr(
        "src.llm.batch_assist.batch_llm_assist_enabled",
        lambda: True,
    )
    seen: dict = {}

    def _fake_llm(text, existing_codes, *, allowed_dimensions=None):
        seen["dims"] = list(allowed_dimensions or [])
        seen["existing"] = list(existing_codes or [])
        # 仅返回履约义务维，模拟补漏
        if allowed_dimensions and "履约义务" not in allowed_dimensions:
            return [], ["skipped covered"]
        return (
            [
                {
                    "issue_code": "PERFORMANCE_OBLIGATION_BOUNDARY_UNCLEAR",
                    "dimension": "履约义务",
                    "description": "安装调试边界不清",
                    "excerpt": "另行协商确定",
                    "confidence": 0.9,
                    "source": "llm",
                }
            ],
            ["mocked dim gap"],
        )

    monkeypatch.setattr(
        "src.llm.batch_assist.llm_supplement_clarity_issues",
        _fake_llm,
    )
    text = (
        "折扣或返利比例由双方另行协商确定。"
        "合同编号 HT25-8888。付款：签收后30日电汇。"
        "货物验收合格后控制权转移至买方。另行协商确定安装调试范围。"
    )
    report = run_contract_clarity_test(
        documents=[
            {
                "file_name": "ht.pdf",
                "doc_type": "contract",
                "fields": {},
                "raw_text": text,
            }
        ],
        business_id="SO25-8888",
        existing_advisory=[],
    )
    assert "交易对价" not in (seen.get("dims") or []), "已规则覆盖的交易对价不应再开给 LLM"
    assert "履约义务" in (seen.get("dims") or [])
    assert "REBATE_TERM_AMBIGUOUS" in (report.extracted.get("issue_sources") or {}).get(
        "rule", []
    )
    assert "PERFORMANCE_OBLIGATION_BOUNDARY_UNCLEAR" in (
        report.extracted.get("issue_sources") or {}
    ).get("llm", [])


def issues_dump(report) -> str:
    return str([(i.issue_code, i.source) for i in (report.test_result.issues or [])])


def test_amount_llm_sets_candidate_and_source_llm(monkeypatch):
    monkeypatch.setattr(
        "src.llm.batch_assist.batch_llm_assist_enabled",
        lambda: True,
    )

    def _fake_fill(**kwargs):
        return (
            {"quantity": 12.0, "unit_price_excl_tax": 10.0},
            ["mocked fill"],
            [
                {
                    "field_name": "quantity",
                    "normalized_candidate": 12.0,
                    "value": 12.0,
                    "excerpt": "见附件数量十二",
                    "confidence": 0.92,
                },
                {
                    "field_name": "unit_price_excl_tax",
                    "normalized_candidate": 10.0,
                    "value": 10.0,
                    "excerpt": "见附件数量十二",
                    "confidence": 0.92,
                },
            ],
        )

    monkeypatch.setattr("src.llm.batch_assist.llm_fill_pricing_gaps", _fake_fill)
    docs = [
        {
            "file_name": "qs.pdf",
            "doc_type": "receipt",
            "fields": {},
            "raw_text": "见附件数量十二。签收确认。",
        },
        {
            "file_name": "so.pdf",
            "doc_type": "order",
            "fields": {},
            "raw_text": "见附件数量十二。订单确认。",
        },
    ]
    source, _, warnings, advisory = merge_pricing_from_documents(
        docs, existing_advisory=[]
    )
    assert source.quantity == 12.0
    assert source.unit_price_excl_tax == 10.0
    assert source.quantity_source == "llm"
    assert source.price_source == "llm"
    meta_qty = get_field_meta(docs[0]).get("quantity") or {}
    assert meta_qty.get("normalized_candidate") == 12.0
    assert meta_qty.get("source") == "llm"
    assert any(c.get("task_type") == "AMOUNT_GAP_FILL" for c in advisory)

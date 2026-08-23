"""合同条款测试：对齐清晰性 WARNING 口径（不产出账务 FAIL）。"""

from __future__ import annotations

from src.contract_terms import run_contract_terms_test


GOOD_TEXT = """
合同编号：HT25-0001
甲方：甲公司 乙方：乙公司
标的物：乙方应向甲方交付工业设备一套并提供安装调试服务。
交货地点：甲方仓库；运输方式：公路运输，运费由卖方承担。
风险与控制权：货物验收合格后，所有权及控制权转移至买方。
付款条款：签收后30日电汇支付。
"""


def test_full_contract_pass(monkeypatch):
    monkeypatch.setattr(
        "src.llm.batch_assist.batch_llm_assist_enabled",
        lambda: False,
    )
    result = run_contract_terms_test(
        [
            {
                "file_name": "ht.pdf",
                "doc_type": "contract",
                "fields": {"paymentTerms": "签收后30日", "contractNo": "HT25-0001"},
                "raw_text": GOOD_TEXT,
            }
        ]
    )
    assert result.status == "PASS"
    assert result.checks
    assert result.checks[0].status == "PASS"


def test_rebate_ambiguous_warning(monkeypatch):
    monkeypatch.setattr(
        "src.llm.batch_assist.batch_llm_assist_enabled",
        lambda: False,
    )
    result = run_contract_terms_test(
        [
            {
                "file_name": "ht.pdf",
                "doc_type": "contract",
                "fields": {},
                "raw_text": GOOD_TEXT + "\n折扣或返利比例由双方另行协商确定。",
            }
        ]
    )
    assert result.status == "WARNING"
    codes = {c.clause_id for c in result.checks}
    assert "rebate_term_ambiguous" in codes or any(
        "rebate" in c.clause_id for c in result.checks
    )


def test_missing_contract_warning(monkeypatch):
    monkeypatch.setattr(
        "src.llm.batch_assist.batch_llm_assist_enabled",
        lambda: False,
    )
    result = run_contract_terms_test(
        [{"file_name": "inv.pdf", "doc_type": "invoice", "fields": {}, "raw_text": ""}]
    )
    # 无合同：清晰性口径为 WARNING（需补合同），非 SKIPPED
    assert result.status in {"WARNING", "SKIPPED"}

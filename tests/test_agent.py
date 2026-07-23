"""Agent 主控流水线单元测试。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from docx import Document

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agent import ContractComplianceAgent
from src.models import (
    AgentFinalReport,
    ExtractedContractInfo,
    PartyInfo,
)


SAMPLE_TEXT = """
软件开发服务合同
合同编号：HT-2026-AGENT-001
合同名称：软件开发服务合同
甲方：云创科技
乙方：智汇数据
签订日期：2026-03-15
合同金额：500万元
一、乙方应向甲方提供软件开发服务，并按约定交付产品。
二、货物验收合格后，控制权转移至甲方。
三、本合同收入按时点法确认。
"""

DEDUP_TEXT = """
测试合同
合同编号：HT-2026-DEDUP
合同名称：同名当事方测试合同
甲方：云创科技
乙方：云创科技
签订日期：2026-01-01
合同金额：100万元
一、乙方向甲方提供技术服务并交付成果。
二、验收合格后控制权转移。
"""


def _write_docx(text: str, path: Path) -> None:
    doc = Document()
    for line in text.strip().splitlines():
        doc.add_paragraph(line)
    doc.save(str(path))


def test_process_contract_full_pipeline() -> None:
    agent = ContractComplianceAgent()
    with tempfile.TemporaryDirectory() as tmp:
        file_path = Path(tmp) / "sample_contract.docx"
        _write_docx(SAMPLE_TEXT, file_path)

        report = agent.process_contract(str(file_path))

        assert isinstance(report, AgentFinalReport)
        assert report.report_id
        assert report.contract_info.contract_id == "HT-2026-AGENT-001"
        assert len(report.contract_info.parties) == 2
        assert report.compliance_result.overall_status in {"PASS", "WARNING", "FAIL"}
        assert len(report.compliance_result.issues) >= 1
        assert report.audit_program_result.step1_distinct_obligations.conclusion in {
            "Agrees",
            "Disagrees",
            "N/A",
            "Not Selected",
        }
        assert (
            report.audit_program_result.step3_revenue_recognition.conclusion
            == "Not Selected"
        )
        assert len(report.counterparty_info.parties) == 2
        assert report.counterparty_info.confidence_note

        downstream = report.to_downstream_json
        required_keys = {
            "contract_id",
            "parties",
            "parties_names",
            "total_amount",
            "performance_obligations",
            "performance_obligations_list",
            "compliance_status",
            "compliance_issues",
            "audit_testing_steps",
        }
        assert required_keys.issubset(downstream.keys())
        assert downstream["contract_id"] == "HT-2026-AGENT-001"
        assert downstream["total_amount"] == 500.0
        assert downstream["parties"][0]["role"] == "甲方"
        assert downstream["parties"][1]["role"] == "乙方"
        assert downstream["audit_testing_steps"]["step3_revenue_recognition"][
            "conclusion"
        ] == "Not Selected"
        assert "程序表" in report.human_judgment_summary

        saved = agent.save_report(report, output_dir=tmp)
        assert saved.exists()
        print("test_process_contract_full_pipeline: PASS")


def test_party_name_dedupe() -> None:
    agent = ContractComplianceAgent()
    with tempfile.TemporaryDirectory() as tmp:
        file_path = Path(tmp) / "dedup_contract.docx"
        _write_docx(DEDUP_TEXT, file_path)

        report = agent.process_contract(str(file_path))

        # 解析层仍保留两条当事方记录（甲/乙角色）
        assert len(report.contract_info.parties) == 2
        # 对手方查询去重后只查 1 家
        assert len(report.counterparty_info.parties) == 1
        assert "云创科技" in report.counterparty_info.parties[0].company_name

        names = agent._dedupe_party_names(report.contract_info)
        assert names == ["云创科技"]
        print("test_party_name_dedupe: PASS")


def test_parse_failure_raises_value_error() -> None:
    agent = ContractComplianceAgent()
    try:
        agent.process_contract("not_exists_file.docx")
        raise AssertionError("应抛出 ValueError")
    except ValueError as exc:
        assert "合同解析失败" in str(exc)
        print("test_parse_failure_raises_value_error: PASS")


def test_skip_empty_party_name() -> None:
    agent = ContractComplianceAgent()
    info = ExtractedContractInfo(
        contract_id="HT-EMPTY",
        parties=[
            PartyInfo(name="云创科技"),
            PartyInfo(name="  "),
            PartyInfo(name="智汇数据"),
        ],
    )
    counterparty, success, fail = agent._fetch_counterparties(info)
    assert len(counterparty.parties) == 2
    assert fail >= 1
    print("test_skip_empty_party_name: PASS")


if __name__ == "__main__":
    test_process_contract_full_pipeline()
    test_party_name_dedupe()
    test_parse_failure_raises_value_error()
    test_skip_empty_party_name()
    print("全部测试通过：Agent 主控流水线运行正常。")

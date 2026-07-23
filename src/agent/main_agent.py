"""合同合规审阅 Agent：串联解析、合规审阅与对手方数据获取。"""

from __future__ import annotations

import json
import random
import string
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from src.data_fetcher import get_fetcher
from src.models.contract_models import (
    AgentFinalReport,
    AuditProgramResult,
    CompanyProfile,
    ComplianceResult,
    CounterpartyInfo,
    ExtractedContractInfo,
)
from src.parsers import ContractParser
from src.rules import ComplianceEngine
from src.utils.logger import logger


class ContractComplianceAgent:
    """合同合规性审阅主控 Agent。"""

    DEFAULT_HUMAN_GUIDE = "请人工复核关键风险点后确认最终结论。"

    def __init__(self) -> None:
        self.parser = ContractParser()
        self.engine = ComplianceEngine()
        self.fetcher = get_fetcher()

    def process_contract(self, file_path: str) -> AgentFinalReport:
        """执行完整流水线并输出 AgentFinalReport。"""
        logger.info("开始处理合同: {}", file_path)

        try:
            contract_info = self._parse_contract(file_path)
            logger.info(
                "合同解析完成，提取到 {} 个当事方",
                len(contract_info.parties),
            )

            compliance_result, audit_program = self.engine.review(contract_info)
            logger.info(
                "合规审阅完成，状态: {}｜程序表: 1={} 2={} 3={}",
                compliance_result.overall_status,
                audit_program.step1_distinct_obligations.conclusion,
                audit_program.step2_transaction_price.conclusion,
                audit_program.step3_revenue_recognition.conclusion,
            )

            counterparty_info, success_count, fail_count = (
                self._fetch_counterparties(contract_info)
            )
            logger.info(
                "对手方数据获取完成，成功 {} 家，失败 {} 家",
                success_count,
                fail_count,
            )

            report_id = self._generate_report_id()
            human_summary = self._build_human_judgment_summary(
                compliance_result, audit_program, counterparty_info
            )
            downstream = self._build_downstream_json(
                contract_info, compliance_result, audit_program
            )

            report = AgentFinalReport(
                report_id=report_id,
                generated_at=datetime.now(),
                contract_info=contract_info,
                compliance_result=compliance_result,
                audit_program_result=audit_program,
                counterparty_info=counterparty_info,
                human_judgment_summary=human_summary,
                to_downstream_json=downstream,
            )
            logger.info("报告生成完成: {}", report_id)
            return report

        except ValueError:
            raise
        except Exception as exc:
            logger.exception("处理合同过程中发生未预期异常: {}", exc)
            raise

    def save_report(
        self, report: AgentFinalReport, output_dir: str = "reports"
    ) -> Path:
        """将报告保存为格式化 JSON 文件。"""
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"{report.report_id}.json"

        payload = report.model_dump(mode="json")
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        logger.info("报告已保存: {}", output_path)
        return output_path

    def _parse_contract(self, file_path: str) -> ExtractedContractInfo:
        try:
            return self.parser.parse(file_path)
        except Exception as exc:
            logger.error("合同解析失败: {} | {}", file_path, exc)
            raise ValueError(f"合同解析失败: {file_path}") from exc

    def _fetch_counterparties(
        self, contract_info: ExtractedContractInfo
    ) -> tuple[CounterpartyInfo, int, int]:
        profiles: List[CompanyProfile] = []
        success = 0
        fail = 0

        for party in contract_info.parties:
            if not (party.name or "").strip():
                logger.warning("公司名称为空，跳过对手方数据获取")
                fail += 1

        names = self._dedupe_party_names(contract_info)
        for name in names:
            try:
                profile = self.fetcher.fetch(name.strip())
                profiles.append(profile)
                success += 1
            except Exception as exc:
                logger.warning("获取企业数据失败: name={}, error={}", name, exc)
                fail += 1

        note = (
            f"数据源={self.fetcher.get_data_source()}；"
            f"去重后查询 {len(names)} 家，成功 {success} 家，失败 {fail} 家。"
        )
        return (
            CounterpartyInfo(parties=profiles, confidence_note=note),
            success,
            fail,
        )

    @staticmethod
    def _dedupe_party_names(contract_info: ExtractedContractInfo) -> List[str]:
        """提取当事方名称并去重（保序）。"""
        seen = set()
        names: List[str] = []
        for party in contract_info.parties:
            name = (party.name or "").strip()
            if not name:
                continue
            if name in seen:
                continue
            seen.add(name)
            names.append(name)
        return names

    @staticmethod
    def _generate_report_id() -> str:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        alphabet = string.ascii_uppercase + string.digits
        suffix = "".join(random.choices(alphabet, k=4))
        return f"{stamp}-{suffix}"

    def _build_human_judgment_summary(
        self,
        compliance_result: ComplianceResult,
        audit_program: AuditProgramResult,
        counterparty_info: CounterpartyInfo,
    ) -> str:
        risk_count = sum(
            1 for i in compliance_result.issues if i.status in {"WARNING", "FAIL"}
        )
        party_count = len(counterparty_info.parties)
        abnormal_count = sum(
            1
            for p in counterparty_info.parties
            if p.is_abnormal
            or p.is_blacklisted
            or p.registration_status in {"吊销", "注销"}
        )
        s1 = audit_program.step1_distinct_obligations
        s2 = audit_program.step2_transaction_price
        s3 = audit_program.step3_revenue_recognition
        dynamic = (
            f"程序表：步骤1={s1.conclusion_zh}，步骤2={s2.conclusion_zh}，"
            f"步骤3={s3.conclusion_zh}（待三单）；"
            f"系统汇总={compliance_result.overall_status}，风险项={risk_count}；"
            f"对手方={party_count}家，异常={abnormal_count}家。"
        )
        return f"{self.DEFAULT_HUMAN_GUIDE} {dynamic}"

    def _build_downstream_json(
        self,
        contract_info: ExtractedContractInfo,
        compliance_result: ComplianceResult,
        audit_program: AuditProgramResult,
    ) -> Dict[str, Any]:
        role_map = {0: "甲方", 1: "乙方"}
        parties_payload = []
        for idx, party in enumerate(contract_info.parties):
            name = (party.name or "").strip()
            if not name:
                continue
            parties_payload.append(
                {
                    "name": name,
                    "role": role_map.get(idx, "当事方"),
                }
            )

        def _step_payload(step) -> Dict[str, Any]:
            return {
                "step_no": step.step_no,
                "step_name": step.step_name,
                "step_name_en": step.step_name_en,
                "conclusion": step.conclusion,
                "conclusion_zh": step.conclusion_zh,
                "notes": step.notes,
            }

        return {
            "contract_id": contract_info.contract_id,
            "parties": parties_payload,
            "parties_names": [p["name"] for p in parties_payload],
            "total_amount": contract_info.total_contract_amount,
            "performance_obligations": [
                {
                    "description": o.description,
                    "amount": o.amount,
                }
                for o in contract_info.performance_obligations
            ],
            "performance_obligations_list": [
                o.description for o in contract_info.performance_obligations
            ],
            "signing_date": contract_info.signing_date,
            "revenue_recognition_point": contract_info.revenue_recognition_point,
            "control_transfer_time": contract_info.control_transfer_time,
            "compliance_status": compliance_result.overall_status,
            "compliance_issues": [
                {
                    "rule_id": issue.rule_id,
                    "rule_name": issue.rule_name,
                    "status": issue.status,
                    "description": issue.description,
                    "suggestion": issue.suggestion,
                }
                for issue in compliance_result.issues
            ],
            "audit_testing_steps": {
                "step1_distinct_obligations": _step_payload(
                    audit_program.step1_distinct_obligations
                ),
                "step2_transaction_price": _step_payload(
                    audit_program.step2_transaction_price
                ),
                "step3_revenue_recognition": _step_payload(
                    audit_program.step3_revenue_recognition
                ),
                "pending_for_three_way_match": audit_program.pending_for_three_way_match,
            },
        }

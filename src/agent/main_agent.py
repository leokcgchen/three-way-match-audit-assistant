"""合同合规审阅 Agent：串联解析、合规审阅、对手方查询与截止性测试。"""

from __future__ import annotations

import json
import random
import re
import string
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.data_fetcher import get_fetcher
from src.models.contract_models import (
    AgentFinalReport,
    AuditProgramResult,
    CompanyProfile,
    ComplianceResult,
    CounterpartyInfo,
    CutoffTestResult,
    DeliveryReceiptInfo,
    ExtractedContractInfo,
    LedgerEntryInfo,
)
from src.parsers import ContractParser
from src.rules import ComplianceEngine, CutoffChecker
from src.utils.logger import logger


class ContractComplianceAgent:
    """合同合规性审阅主控 Agent。"""

    DEFAULT_HUMAN_GUIDE = "请人工复核关键风险点后确认最终结论。"
    PAYMENT_DAYS_PATTERNS = (
        r"签收后\s*(\d+)\s*日",
        r"交付后\s*(\d+)\s*日",
        r"验收后\s*(\d+)\s*日",
    )

    def __init__(self) -> None:
        self.parser = ContractParser()
        self.engine = ComplianceEngine()
        self.fetcher = get_fetcher()
        self.cutoff_checker = CutoffChecker()

    def process_contract(
        self,
        file_path: str,
        ledger_entry: Optional[LedgerEntryInfo] = None,
        delivery_receipt: Optional[DeliveryReceiptInfo] = None,
    ) -> AgentFinalReport:
        """执行完整流水线并输出 AgentFinalReport。

        不传 ledger_entry / delivery_receipt 时行为与原先一致；
        两者均传入时才执行截止性测试。
        """
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

            cutoff_result = self._run_cutoff_test(
                contract_info, ledger_entry, delivery_receipt
            )

            report_id = self._generate_report_id()
            human_summary = self._build_human_judgment_summary(
                compliance_result,
                audit_program,
                counterparty_info,
                cutoff_result,
            )
            downstream = self._build_downstream_json(
                contract_info,
                compliance_result,
                audit_program,
                cutoff_result,
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
                cutoff_test_result=cutoff_result,
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

    def _run_cutoff_test(
        self,
        contract_info: ExtractedContractInfo,
        ledger_entry: Optional[LedgerEntryInfo],
        delivery_receipt: Optional[DeliveryReceiptInfo],
    ) -> Optional[CutoffTestResult]:
        # 均未提供 → 跳过；任一提供 → 执行（缺字段由 CutoffChecker 返回 WARNING）
        if ledger_entry is None and delivery_receipt is None:
            logger.info("未提供签收单与序时账，跳过截止性测试")
            return None

        payment_days = self._extract_payment_days(contract_info)
        receipt_date = (
            delivery_receipt.receipt_date if delivery_receipt is not None else None
        )
        entry_date = ledger_entry.entry_date if ledger_entry is not None else None
        logger.info(
            "开始截止性测试: payment_days={}, receipt_date={}, entry_date={}",
            payment_days,
            receipt_date,
            entry_date,
        )
        result = self.cutoff_checker.check(
            contract_payment_days=payment_days,
            receipt_date=receipt_date,
            entry_date=entry_date,
        )
        logger.info(
            "截止性测试完成: status={}, deviation_days={}",
            result.test_status,
            result.deviation_days,
        )
        return result

    def _extract_payment_days(
        self, contract_info: ExtractedContractInfo
    ) -> Optional[int]:
        """从履约义务描述或原文预览中提取账期天数。"""
        texts: List[str] = [
            o.description
            for o in (contract_info.performance_obligations or [])
            if o.description
        ]
        if contract_info.control_transfer_time:
            texts.append(contract_info.control_transfer_time)

        days = self._match_payment_days("\n".join(texts))
        if days is not None:
            return days

        if contract_info.raw_text_preview:
            return self._match_payment_days(contract_info.raw_text_preview)
        return None

    def _match_payment_days(self, text: str) -> Optional[int]:
        if not text:
            return None
        for pattern in self.PAYMENT_DAYS_PATTERNS:
            match = re.search(pattern, text)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    continue
        return None

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
        cutoff_result: Optional[CutoffTestResult] = None,
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
        summary = f"{self.DEFAULT_HUMAN_GUIDE} {dynamic}"
        if cutoff_result is not None:
            if cutoff_result.test_status == "PASS":
                summary += " 截止性测试通过，收入入账期间合规。"
            elif cutoff_result.test_status == "WARNING":
                summary += f" 截止性测试需关注：{cutoff_result.issue_description}"
            else:
                summary += (
                    f" 截止性测试未通过：{cutoff_result.issue_description}，"
                    "建议调整入账期间。"
                )
        return summary

    def _build_downstream_json(
        self,
        contract_info: ExtractedContractInfo,
        compliance_result: ComplianceResult,
        audit_program: AuditProgramResult,
        cutoff_result: Optional[CutoffTestResult] = None,
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
            "expected_revenue_date": (
                cutoff_result.expected_revenue_date if cutoff_result else None
            ),
            "actual_entry_date": (
                cutoff_result.actual_entry_date if cutoff_result else None
            ),
            "cutoff_test_status": (
                cutoff_result.test_status if cutoff_result else None
            ),
            "cutoff_deviation_days": (
                cutoff_result.deviation_days if cutoff_result else None
            ),
        }

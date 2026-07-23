"""合规审阅规则引擎：规则明细 + 对齐KPMG程序表三步结论。"""

from __future__ import annotations

from typing import Callable, List, Tuple

from src.models.contract_models import (
    AuditProgramResult,
    ComplianceIssue,
    ComplianceResult,
    ExtractedContractInfo,
    TestingStepResult,
)
from src.utils.logger import logger


CheckFunc = Callable[[ExtractedContractInfo], ComplianceIssue]

CONCLUSION_ZH = {
    "Agrees": "相符",
    "Disagrees": "不符",
    "N/A": "不适用",
    "Not Selected": "未选",
}


class ComplianceEngine:
    """输入 ExtractedContractInfo，输出规则明细 + 程序表三步结论。"""

    def __init__(self) -> None:
        self.rules: List[dict] = [
            {
                "rule_id": "GOSPD01010-1",
                "rule_name": "合同真实存在性",
                "check_func": self._check_existence,
            },
            {
                "rule_id": "GOSPD01010-2",
                "rule_name": "可明确区分的履约义务",
                "check_func": self._check_performance_obligations,
            },
            {
                "rule_id": "GOSPD01010-3",
                "rule_name": "交易价格明确性",
                "check_func": self._check_transaction_price,
            },
            {
                "rule_id": "GOSPD01010-4",
                "rule_name": "截止性风险（控制权转移与签订日期）",
                "check_func": self._check_cutoff_risk,
            },
            {
                "rule_id": "SPD02013",
                "rule_name": "费用抽凭金额勾稽（简化）",
                "check_func": self._check_amount_consistency,
            },
        ]

    def review(
        self, contract_info: ExtractedContractInfo
    ) -> Tuple[ComplianceResult, AuditProgramResult]:
        """执行规则检查并生成对齐程序表的三步结论。"""
        logger.info(
            "开始合规审阅: contract_id={}",
            contract_info.contract_id,
        )

        issues: List[ComplianceIssue] = []
        for rule in self.rules:
            issue: ComplianceIssue = rule["check_func"](contract_info)
            issues.append(issue)
            logger.info(
                "规则 {} [{}] -> {}",
                rule["rule_id"],
                rule["rule_name"],
                issue.status,
            )

        audit_program = self._build_audit_program(contract_info, issues)
        overall_status = self._aggregate_status(issues, audit_program)
        summary = self._build_summary(overall_status, audit_program)

        result = ComplianceResult(
            overall_status=overall_status,
            issues=issues,
            summary=summary,
        )
        logger.info(
            "合规审阅完成: overall={}, step1={}, step2={}, step3={}",
            overall_status,
            audit_program.step1_distinct_obligations.conclusion,
            audit_program.step2_transaction_price.conclusion,
            audit_program.step3_revenue_recognition.conclusion,
        )
        return result, audit_program

    def _build_audit_program(
        self,
        info: ExtractedContractInfo,
        issues: List[ComplianceIssue],
    ) -> AuditProgramResult:
        issue_map = {i.rule_id: i for i in issues}
        existence = issue_map.get("GOSPD01010-1")
        po_issue = issue_map.get("GOSPD01010-2")
        price_issue = issue_map.get("GOSPD01010-3")
        cutoff_issue = issue_map.get("GOSPD01010-4")

        # 合同要素严重缺失时，步骤1/2标 N/A
        existence_failed = existence is not None and existence.status == "FAIL"

        # ---- Step 1: Distinct performance obligations ----
        if existence_failed:
            step1 = self._make_step(
                1,
                "可区分履约义务",
                "Distinct performance obligations",
                "N/A",
                "合同缺少编号或当事方信息，无法可靠执行履约义务识别程序。",
            )
        elif po_issue is None or po_issue.status == "FAIL":
            step1 = self._make_step(
                1,
                "可区分履约义务",
                "Distinct performance obligations",
                "Disagrees",
                po_issue.description if po_issue else "未识别出可明确区分的履约义务。",
            )
        elif po_issue.status == "WARNING":
            step1 = self._make_step(
                1,
                "可区分履约义务",
                "Distinct performance obligations",
                "Disagrees",
                f"{po_issue.description}（合同侧不足以支持管理层已恰当识别多项履约义务）",
            )
        else:
            count = len(info.performance_obligations or [])
            step1 = self._make_step(
                1,
                "可区分履约义务",
                "Distinct performance obligations",
                "Agrees",
                f"合同侧已识别 {count} 条可区分履约义务，初步支持管理层履约义务认定。",
            )

        # ---- Step 2: Transaction price ----
        if existence_failed:
            step2 = self._make_step(
                2,
                "交易价格确定",
                "Transaction price determination",
                "N/A",
                "合同要素不足，无法执行交易价格确定程序。",
            )
        elif price_issue is not None and price_issue.status == "PASS":
            step2 = self._make_step(
                2,
                "交易价格确定",
                "Transaction price determination",
                "Agrees",
                f"合同已明确交易价格 {info.total_contract_amount} 万元（合同侧结论；"
                "管理层计算与入账核对交由三单/账务程序完成）。",
            )
        else:
            step2 = self._make_step(
                2,
                "交易价格确定",
                "Transaction price determination",
                "Disagrees",
                price_issue.description
                if price_issue
                else "合同未明确约定交易价格或金额为0。",
            )

        # ---- Step 3: Always hand off to three-way match ----
        clues = []
        if info.control_transfer_time:
            clues.append(f"控制权转移线索={info.control_transfer_time}")
        if info.revenue_recognition_point:
            clues.append(f"收入确认类型={info.revenue_recognition_point}")
        if info.signing_date:
            clues.append(f"签订日期={info.signing_date}")
        if cutoff_issue and cutoff_issue.status != "PASS":
            clues.append(f"截止性预审={cutoff_issue.description}")
        clue_text = "；".join(clues) if clues else "合同侧未提取到充分的控制权/期间线索"
        step3 = self._make_step(
            3,
            "交付证据与收入确认",
            "Delivery evidence / control transfer / period / revenue recalculation",
            "Not Selected",
            f"本Agent仅完成合同侧预审与交棒，不裁定本步骤最终结论。"
            f"待三单智能匹配Agent核验交付/验收证据并重算收入后给出 Agrees/Disagrees。"
            f"交棒线索：{clue_text}",
        )

        return AuditProgramResult(
            step1_distinct_obligations=step1,
            step2_transaction_price=step2,
            step3_revenue_recognition=step3,
            pending_for_three_way_match=True,
        )

    @staticmethod
    def _make_step(
        step_no: int,
        step_name: str,
        step_name_en: str,
        conclusion: str,
        notes: str,
    ) -> TestingStepResult:
        return TestingStepResult(
            step_no=step_no,
            step_name=step_name,
            step_name_en=step_name_en,
            conclusion=conclusion,  # type: ignore[arg-type]
            conclusion_zh=CONCLUSION_ZH[conclusion],  # type: ignore[arg-type]
            notes=notes,
        )

    @staticmethod
    def _aggregate_status(
        issues: List[ComplianceIssue],
        audit_program: AuditProgramResult,
    ) -> str:
        # 程序表步骤1/2 不符 → FAIL；步骤3未选且1/2相符 → WARNING（待三单）
        s1 = audit_program.step1_distinct_obligations.conclusion
        s2 = audit_program.step2_transaction_price.conclusion
        if s1 == "Disagrees" or s2 == "Disagrees":
            return "FAIL"
        if "FAIL" in {i.status for i in issues}:
            return "FAIL"
        if audit_program.pending_for_three_way_match or s1 == "N/A" or s2 == "N/A":
            return "WARNING"
        if "WARNING" in {i.status for i in issues}:
            return "WARNING"
        return "PASS"

    @staticmethod
    def _build_summary(
        overall_status: str, audit_program: AuditProgramResult
    ) -> str:
        s1 = audit_program.step1_distinct_obligations
        s2 = audit_program.step2_transaction_price
        s3 = audit_program.step3_revenue_recognition
        base = {
            "PASS": "合同基本合规，收入确认条件初步满足",
            "WARNING": "存在需关注事项或步骤3待三单核验，建议人工进一步核查",
            "FAIL": "存在重大合规缺陷，不建议直接进入三单匹配流程",
        }[overall_status]
        return (
            f"{base}｜程序表结论："
            f"步骤1={s1.conclusion}/{s1.conclusion_zh}，"
            f"步骤2={s2.conclusion}/{s2.conclusion_zh}，"
            f"步骤3={s3.conclusion}/{s3.conclusion_zh}"
        )

    def _check_existence(self, info: ExtractedContractInfo) -> ComplianceIssue:
        rule_id = "GOSPD01010-1"
        rule_name = "合同真实存在性"
        has_id = bool(info.contract_id and str(info.contract_id).strip())
        party_names = [
            p.name.strip() for p in info.parties if p.name and p.name.strip()
        ]
        has_parties = len(party_names) >= 2

        if has_id and has_parties:
            return ComplianceIssue(
                rule_id=rule_id,
                rule_name=rule_name,
                status="PASS",
                description="合同编号与甲乙双方信息齐全，合同真实存在性初步可确认",
            )
        return ComplianceIssue(
            rule_id=rule_id,
            rule_name=rule_name,
            status="FAIL",
            description="合同缺少编号或当事方信息，无法确认合同真实存在",
            suggestion="补充合同编号及甲方、乙方完整名称",
        )

    def _check_performance_obligations(
        self, info: ExtractedContractInfo
    ) -> ComplianceIssue:
        rule_id = "GOSPD01010-2"
        rule_name = "可明确区分的履约义务"
        obligations = info.performance_obligations or []

        if not obligations:
            return ComplianceIssue(
                rule_id=rule_id,
                rule_name=rule_name,
                status="FAIL",
                description="未识别出可明确区分的履约义务，建议补充描述交付物/服务内容",
                suggestion="在合同中明确列示可区分的交付物或服务内容",
            )

        if len(obligations) == 1 and len(obligations[0].description.strip()) < 5:
            return ComplianceIssue(
                rule_id=rule_id,
                rule_name=rule_name,
                status="WARNING",
                description="仅识别出1条履约义务，请确认是否存在多项交付物",
                suggestion="核查合同是否包含多项可区分履约义务",
            )

        return ComplianceIssue(
            rule_id=rule_id,
            rule_name=rule_name,
            status="PASS",
            description=f"已识别出 {len(obligations)} 条可区分履约义务",
        )

    def _check_transaction_price(
        self, info: ExtractedContractInfo
    ) -> ComplianceIssue:
        rule_id = "GOSPD01010-3"
        rule_name = "交易价格明确性"
        amount = info.total_contract_amount

        if amount is not None and amount > 0:
            return ComplianceIssue(
                rule_id=rule_id,
                rule_name=rule_name,
                status="PASS",
                description=f"合同交易价格明确，金额为 {amount} 万元",
            )
        return ComplianceIssue(
            rule_id=rule_id,
            rule_name=rule_name,
            status="FAIL",
            description="合同未明确约定交易价格或金额为0，无法确认收入计量基础",
            suggestion="补充明确的合同总价或交易价格条款",
        )

    def _check_cutoff_risk(self, info: ExtractedContractInfo) -> ComplianceIssue:
        rule_id = "GOSPD01010-4"
        rule_name = "截止性风险（控制权转移与签订日期）"
        has_control = bool(
            info.control_transfer_time and str(info.control_transfer_time).strip()
        )
        has_signing = bool(info.signing_date and str(info.signing_date).strip())

        if has_control and has_signing:
            return ComplianceIssue(
                rule_id=rule_id,
                rule_name=rule_name,
                status="PASS",
                description="控制权转移时点与签订日期均已明确，截止性风险可控",
            )
        if not has_control:
            return ComplianceIssue(
                rule_id=rule_id,
                rule_name=rule_name,
                status="WARNING",
                description="未明确控制权转移时点，存在收入确认跨期风险，建议补充截止性测试",
                suggestion="补充验收/交付/签收等控制权转移时点条款，并执行截止性测试",
            )
        return ComplianceIssue(
            rule_id=rule_id,
            rule_name=rule_name,
            status="WARNING",
            description="合同签订日期缺失，影响收入期间归属判断",
            suggestion="补充合同签订日期以支持收入期间归属判断",
        )

    def _check_amount_consistency(
        self, info: ExtractedContractInfo
    ) -> ComplianceIssue:
        rule_id = "SPD02013"
        rule_name = "费用抽凭金额勾稽（简化）"
        total = info.total_contract_amount
        obligation_amounts = [
            o.amount
            for o in (info.performance_obligations or [])
            if o.amount is not None
        ]

        if total is None or not obligation_amounts:
            return ComplianceIssue(
                rule_id=rule_id,
                rule_name=rule_name,
                status="PASS",
                description="仅有合同总额或缺少分项金额，不做强制勾稽",
            )

        subtotal = sum(obligation_amounts)
        if total == 0:
            return ComplianceIssue(
                rule_id=rule_id,
                rule_name=rule_name,
                status="WARNING",
                description="合同总额与各履约义务金额之和存在较大差异，建议核查费用分摊准确性",
                suggestion="核查合同总额与履约义务分项金额的勾稽关系",
            )

        diff_ratio = abs(subtotal - total) / abs(total)
        if diff_ratio > 0.10:
            return ComplianceIssue(
                rule_id=rule_id,
                rule_name=rule_name,
                status="WARNING",
                description="合同总额与各履约义务金额之和存在较大差异，建议核查费用分摊准确性",
                suggestion="核查合同总额与履约义务分项金额的勾稽关系",
            )

        return ComplianceIssue(
            rule_id=rule_id,
            rule_name=rule_name,
            status="PASS",
            description="合同总额与履约义务金额之和勾稽一致（差异不超过10%）",
        )

"""合同文本解析模块：从 PDF/Word 提取结构化字段。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

import pdfplumber
from docx import Document

from src.models.contract_models import (
    ExtractedContractInfo,
    PartyInfo,
    PerformanceObligation,
)
from src.utils.logger import logger


class ContractParser:
    """基于正则与关键词的合同解析器，支持 PDF / DOCX。"""

    SUPPORTED_SUFFIXES = {".pdf", ".docx"}

    # 履约义务关键词
    OBLIGATION_KEYWORDS = ("交付", "提供", "服务", "产品")
    # 收入确认相关关键词
    REVENUE_KEYWORDS = ("验收", "交付", "控制权转移")
    # 控制权转移时间关键词
    CONTROL_TRANSFER_KEYWORDS = ("验收合格后", "交付后", "签收后")

    def parse(self, file_path: str) -> ExtractedContractInfo:
        """根据扩展名读取合同文件并解析为 ExtractedContractInfo。"""
        path = Path(file_path)
        logger.info("开始解析合同文件: {}", path)

        if not path.exists():
            raise FileNotFoundError(f"合同文件不存在: {path}")

        suffix = path.suffix.lower()
        if suffix not in self.SUPPORTED_SUFFIXES:
            raise ValueError(f"不支持的文件格式: {suffix}，仅支持 .pdf / .docx")

        if suffix == ".pdf":
            text = self._read_pdf(path)
        else:
            text = self._read_docx(path)

        result = self.extract(text)
        logger.info(
            "解析完成: contract_id={}, title={}, amount={}, parties={}",
            result.contract_id,
            result.contract_title,
            result.total_contract_amount,
            [p.name for p in result.parties],
        )
        return result

    def extract(self, text: str) -> ExtractedContractInfo:
        """从纯文本提取结构化合同信息（便于单测，不依赖真实文件）。"""
        normalized = text or ""
        preview = normalized[:500] if normalized else None

        contract_id = self._extract_contract_id(normalized)
        contract_title = self._extract_contract_title(normalized)
        signing_date = self._extract_signing_date(normalized)
        parties = self._extract_parties(normalized)
        total_amount = self._extract_total_amount(normalized)
        obligations = self._extract_performance_obligations(normalized)
        revenue_point = self._extract_revenue_recognition_point(normalized)
        control_time = self._extract_control_transfer_time(normalized)

        logger.info(
            "字段提取结果: id={}, title={}, date={}, amount={}, "
            "parties={}, obligations={}, revenue={}, control={}",
            contract_id,
            contract_title,
            signing_date,
            total_amount,
            [p.name for p in parties],
            len(obligations),
            revenue_point,
            control_time,
        )

        return ExtractedContractInfo(
            contract_id=contract_id,
            contract_title=contract_title,
            signing_date=signing_date,
            parties=parties,
            total_contract_amount=total_amount,
            performance_obligations=obligations,
            revenue_recognition_point=revenue_point,
            control_transfer_time=control_time,
            raw_text_preview=preview,
        )

    def _read_pdf(self, path: Path) -> str:
        parts: List[str] = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                if page_text:
                    parts.append(page_text)
        return "\n".join(parts)

    def _read_docx(self, path: Path) -> str:
        doc = Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs if p.text)

    def _extract_contract_id(self, text: str) -> Optional[str]:
        patterns = [
            r"合同编号\s*[:：]\s*(\S+)",
            r"编号\s*[:：]\s*(\S+)",
        ]
        return self._first_group(text, patterns)

    def _extract_contract_title(self, text: str) -> Optional[str]:
        named = self._first_group(text, [r"合同名称\s*[:：]\s*(.+)"])
        if named:
            return named.strip()

        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        return None

    def _extract_signing_date(self, text: str) -> Optional[str]:
        patterns = [
            r"签订日期\s*[:：]\s*(\d{4}[-/.年]?\d{1,2}[-/.月]?\d{1,2}日?)",
            r"签署日期\s*[:：]\s*(\d{4}[-/.年]?\d{1,2}[-/.月]?\d{1,2}日?)",
        ]
        return self._first_group(text, patterns)

    def _extract_parties(self, text: str) -> List[PartyInfo]:
        parties: List[PartyInfo] = []
        patterns = [
            (r"甲方\s*[:：]\s*(.+?)[\n\r]", "甲方"),
            (r"乙方\s*[:：]\s*(.+?)[\n\r]", "乙方"),
        ]
        for pattern, _ in patterns:
            match = re.search(pattern, text)
            if match:
                name = match.group(1).strip().rstrip("。；;,. ")
                if name:
                    parties.append(PartyInfo(name=name))
        return parties

    def _extract_total_amount(self, text: str) -> Optional[float]:
        patterns = [
            r"合同金额\s*[:：]\s*[¥￥]?\s*([\d,.]+)\s*(万)?\s*元?",
            r"总价\s*[:：]\s*[¥￥]?\s*([\d,.]+)\s*(万)?\s*元?",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            raw_number = match.group(1).replace(",", "")
            try:
                value = float(raw_number)
            except ValueError:
                continue
            has_wan = bool(match.group(2))
            # 统一转换为万元：已含「万」则直接用；否则按元÷10000
            return value if has_wan else value / 10000.0
        return None

    def _extract_performance_obligations(self, text: str) -> List[PerformanceObligation]:
        sentences = self._split_sentences(text)
        obligations: List[PerformanceObligation] = []
        seen = set()
        for sentence in sentences:
            if any(kw in sentence for kw in self.OBLIGATION_KEYWORDS):
                cleaned = sentence.strip()
                if cleaned and cleaned not in seen:
                    seen.add(cleaned)
                    obligations.append(PerformanceObligation(description=cleaned))
        return obligations

    def _extract_revenue_recognition_point(
        self, text: str
    ) -> Optional[str]:
        if not any(kw in text for kw in self.REVENUE_KEYWORDS):
            return None
        if "时段" in text:
            return "时段"
        return "时点"

    def _extract_control_transfer_time(self, text: str) -> Optional[str]:
        sentences = self._split_sentences(text)
        for sentence in sentences:
            if any(kw in sentence for kw in self.CONTROL_TRANSFER_KEYWORDS):
                return sentence.strip()
        return None

    @staticmethod
    def _split_sentences(text: str) -> List[str]:
        """按中英文句号/分号/换行切分句子。"""
        parts = re.split(r"[。！？；;\n\r]+", text)
        return [p.strip() for p in parts if p and p.strip()]

    @staticmethod
    def _first_group(text: str, patterns: List[str]) -> Optional[str]:
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                value = match.group(1).strip()
                if value:
                    return value
        return None

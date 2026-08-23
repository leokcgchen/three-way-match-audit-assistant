"""从非结构化文本中提取日期与合同编号。"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

_DATE_PATTERN = re.compile(
    r"(?P<ymd_dash>\d{4}-\d{1,2}-\d{1,2})"
    r"|(?P<ymd_slash>\d{4}/\d{1,2}/\d{1,2})"
    r"|(?P<ymd_cn>\d{4}年\d{1,2}月\d{1,2}日)"
    r"|(?P<mdy_dash>\d{1,2}-\d{1,2}-\d{4})"
    r"|(?P<mdy_slash>\d{1,2}/\d{1,2}/\d{4})"
)

_CONTRACT_ID_PATTERNS = [
    re.compile(r"合同索引号\s*[:：=]\s*([A-Za-z0-9\-]+)"),
    re.compile(r"合同号\s*[:：=]\s*([A-Za-z0-9\-]+)"),
    re.compile(r"合同编号\s*[:：=]\s*([A-Za-z0-9\-]+)"),
    re.compile(r"订单号\s*[:：=]\s*([A-Za-z0-9\-]+)"),
    # 兼容「合同索引号HT2501-0001」无分隔符写法
    re.compile(r"合同索引号\s*([A-Za-z][A-Za-z0-9\-]*)"),
]


def extract_days_from_description(text: Optional[str]) -> Optional[int]:
    """从账期/付款条款描述中提取天数。

    支持：「签收后10日」「验收后30天」「票到30天」等（正则 ``\\d+\\s*[日天]``）。
    全项目统一由此函数提取，供三单匹配与截止性 Agent 共用。
    """
    if text is None:
        return None
    raw = str(text).strip()
    if not raw:
        return None
    match = re.search(r"(\d+)\s*[日天]", raw)
    if match:
        return int(match.group(1))
    return None


def extract_date_from_text(text: Optional[str]) -> Optional[str]:
    """提取文本中第一个日期，格式化为 YYYY-MM-DD；未找到返回 None。"""
    dates = extract_all_dates_from_text(text)
    return dates[0] if dates else None


def extract_all_dates_from_text(text: Optional[str]) -> List[str]:
    """提取文本中全部日期（按出现顺序），统一为 YYYY-MM-DD。"""
    if text is None:
        return []
    raw = str(text).strip()
    if not raw:
        return []

    results: List[str] = []
    for match in _DATE_PATTERN.finditer(raw):
        normalized = _normalize_match(match)
        if normalized:
            results.append(normalized)
    return results


def is_date_column_candidate(
    column_values: Sequence[object], sample_size: int = 100
) -> bool:
    """采样判断某列是否可能包含可提取日期（成功率 > 30%）。"""
    if column_values is None:
        return False
    values = list(column_values)[: max(int(sample_size), 1)]
    if not values:
        return False
    hit = 0
    nonempty = 0
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if not text or text.lower() == "nan":
            continue
        nonempty += 1
        if extract_date_from_text(text):
            hit += 1
    if nonempty == 0:
        return False
    return (hit / nonempty) > 0.30


def extract_contract_id_from_text(text: Optional[str]) -> Optional[str]:
    """从文本中提取第一个合同编号；未找到返回 None。"""
    if text is None:
        return None
    raw = str(text).strip()
    if not raw or raw.lower() == "nan":
        return None
    for pattern in _CONTRACT_ID_PATTERNS:
        match = pattern.search(raw)
        if match:
            cid = match.group(1).strip().upper()
            if cid:
                return cid
    return None


def extract_contract_id_from_row(
    row: pd.Series, candidate_columns: List[str]
) -> Optional[str]:
    """按候选列顺序尝试提取合同编号，成功即返回。"""
    if row is None or not candidate_columns:
        return None
    for col in candidate_columns:
        if col not in row.index:
            continue
        value = row.get(col)
        if value is None:
            continue
        try:
            if pd.isna(value):
                continue
        except Exception:
            pass
        text = str(value).strip()
        if not text or text.lower() == "nan":
            continue
        extracted = extract_contract_id_from_text(text)
        if extracted:
            return extracted
        # 单元格本身已是干净编号
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9\-]{3,}", text) and len(text) <= 32:
            if "合同" not in text and "订单" not in text:
                return text.upper()
    return None


def _normalize_match(match: re.Match[str]) -> Optional[str]:
    group = match.lastgroup
    raw = match.group(0)
    try:
        if group in {"ymd_dash", "ymd_slash"}:
            sep = "-" if group == "ymd_dash" else "/"
            y, m, d = raw.split(sep)
            return _safe_iso(int(y), int(m), int(d))
        if group == "ymd_cn":
            parts = re.findall(r"\d+", raw)
            y, m, d = (int(parts[0]), int(parts[1]), int(parts[2]))
            return _safe_iso(y, m, d)
        if group in {"mdy_dash", "mdy_slash"}:
            sep = "-" if group == "mdy_dash" else "/"
            a, b, y = raw.split(sep)
            return _safe_iso(int(y), int(a), int(b))
    except Exception:
        return None
    return None


def _safe_iso(year: int, month: int, day: int) -> Optional[str]:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_to_date(value: object) -> Optional[date]:
    """辅助：将 YYYY-MM-DD 或可解析值转为 date。"""
    text = extract_date_from_text(str(value) if value is not None else None)
    if not text:
        return None
    return datetime.strptime(text, "%Y-%m-%d").date()


_RAW_DATE_TOKEN = (
    r"(\d{4}[年./-]\d{1,2}[月./-]\d{1,2}日?)"
)

_ACCEPTANCE_DATE_PATTERNS = [
    re.compile(
        rf"{_RAW_DATE_TOKEN}\s*为\s*(?:验收完成|期限届满|验收合格|验收确认)",
        re.I,
    ),
    re.compile(
        rf"(?:验收完成|期限届满|验收合格|验收确认)\s*[/／、]?\s*(?:期限届满)?"
        rf"\s*[:：]?\s*{_RAW_DATE_TOKEN}",
        re.I,
    ),
    re.compile(
        rf"{_RAW_DATE_TOKEN}.{{0,16}}(?:验收完成|期限届满|验收合格)",
        re.I,
    ),
    re.compile(
        rf"(?:验收完成|期限届满|验收合格).{{0,24}}{_RAW_DATE_TOKEN}",
        re.I,
    ),
]

_ARRIVAL_DATE_PATTERNS = [
    re.compile(
        rf"{_RAW_DATE_TOKEN}\s*为\s*(?:实物到货|到货日|到货事实|货物到达)",
        re.I,
    ),
    re.compile(
        rf"(?:实物到货|到货日|到货事实|货物到达)\s*[:：]?\s*{_RAW_DATE_TOKEN}",
        re.I,
    ),
    re.compile(
        rf"{_RAW_DATE_TOKEN}.{{0,16}}(?:实物到货|到货日|到货事实)",
        re.I,
    ),
]

_RECEIPT_LABEL_PATTERNS = [
    re.compile(
        rf"(?:签收日期|入库日期|验收日期|收货日期|交货日期)\s*[:：]?\s*{_RAW_DATE_TOKEN}",
        re.I,
    ),
]

_INSPECTION_PERIOD_PATTERNS = [
    re.compile(r"(\d+)\s*日\s*验收期", re.I),
    re.compile(r"验收期\s*(\d+)\s*[日天]", re.I),
    re.compile(r"(\d+)\s*[日天]\s*验收期", re.I),
    re.compile(r"合同约定.*?(\d+)\s*日\s*验收", re.I),
]


def _normalize_raw_date_token(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    text = str(raw).strip()
    parts = re.match(r"(\d{4})[年./-](\d{1,2})[月./-](\d{1,2})", text)
    if not parts:
        return extract_date_from_text(text)
    return _safe_iso(int(parts.group(1)), int(parts.group(2)), int(parts.group(3)))


def _first_pattern_date(patterns: Sequence[re.Pattern[str]], text: str) -> Optional[str]:
    for pattern in patterns:
        match = pattern.search(text)
        if not match:
            continue
        for group in match.groups():
            if group:
                normalized = _normalize_raw_date_token(group)
                if normalized:
                    return normalized
    return None


def _extract_inspection_days(text: Optional[str], payment_terms: Optional[str] = None) -> Optional[int]:
    for source in (text, payment_terms):
        if not source:
            continue
        raw = str(source)
        for pattern in _INSPECTION_PERIOD_PATTERNS:
            match = pattern.search(raw)
            if match:
                return int(match.group(1))
        days = extract_days_from_description(raw)
        if days is not None:
            return days
    return None


def resolve_receipt_dates(
    text: Optional[str],
    *,
    payment_terms: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """从签收/验收单文本解析截止性测试用签收日。

    优先顺序：验收完成/期限届满日 > 到货日+验收期 > 带标签签收日 > 文中最晚日期（多日期并存时）。
    """
    result: Dict[str, Optional[str]] = {
        "deliveryDate": None,
        "acceptanceDate": None,
        "receiptDateForCutoff": None,
        "_receiptDateSource": None,
    }
    if text is None:
        return result
    raw = str(text).strip()
    if not raw:
        return result

    acceptance = _first_pattern_date(_ACCEPTANCE_DATE_PATTERNS, raw)
    arrival = _first_pattern_date(_ARRIVAL_DATE_PATTERNS, raw)
    labeled_receipt = _first_pattern_date(_RECEIPT_LABEL_PATTERNS, raw)

    if arrival:
        result["deliveryDate"] = arrival
    inspection_days = _extract_inspection_days(raw, payment_terms)
    if acceptance:
        result["acceptanceDate"] = acceptance
        result["receiptDateForCutoff"] = acceptance
        result["_receiptDateSource"] = "acceptance_completion"
    elif arrival and inspection_days is not None and inspection_days >= 0:
        try:
            computed = (
                datetime.strptime(arrival, "%Y-%m-%d").date()
                + timedelta(days=inspection_days)
            ).isoformat()
            result["acceptanceDate"] = computed
            result["receiptDateForCutoff"] = computed
            result["_receiptDateSource"] = "arrival_plus_inspection_period"
        except ValueError:
            pass
    elif labeled_receipt:
        result["receiptDateForCutoff"] = labeled_receipt
        result["_receiptDateSource"] = "receipt_labeled"
        if not result["deliveryDate"]:
            result["deliveryDate"] = labeled_receipt
    elif re.search(r"验收|签收|入库|到货", raw):
        all_dates = extract_all_dates_from_text(raw)
        if len(all_dates) >= 2 and re.search(r"验收|期限届满|到货", raw):
            latest = max(all_dates)
            result["receiptDateForCutoff"] = latest
            result["acceptanceDate"] = latest
            result["_receiptDateSource"] = "latest_of_multiple_dates"
            if not result["deliveryDate"] and all_dates:
                result["deliveryDate"] = min(all_dates)
        elif all_dates:
            result["receiptDateForCutoff"] = all_dates[0]
            result["_receiptDateSource"] = "first_date_fallback"

    return result


def pick_receipt_date_from_fields(fields: Optional[Dict[str, Any]]) -> Optional[str]:
    """从 OCR 字段选取截止性测试签收日（验收完成日优先于到货日）。"""
    if not fields:
        return None
    for key in (
        "receiptDateForCutoff",
        "acceptanceDate",
        "receiptDate",
        "deliveryDate",
        "documentDate",
    ):
        val = fields.get(key)
        if val is None:
            continue
        text = str(val).strip()
        if text and text.lower() not in {"none", "null", "nan", "-"}:
            return text
    return None


def apply_receipt_date_fields(
    fields: Dict[str, Any],
    ocr_text: str,
    *,
    document_type: Optional[str] = None,
) -> Dict[str, Any]:
    """将签收日语义解析结果写入字段 dict（仅入库/签收单）。"""
    doc = (document_type or fields.get("documentType") or "").strip().lower()
    if doc not in {"warehouse_receipt", "receipt"}:
        # 合同/发票等即使正文提到到货，也不写入截止专用签收日
        if doc in {"contract", "invoice", "purchase_order", "order", "other", "payment"}:
            fields.pop("receiptDateForCutoff", None)
            fields.pop("_receiptDateSource", None)
        return fields
    resolved = resolve_receipt_dates(ocr_text, payment_terms=fields.get("paymentTerms"))
    for key in ("deliveryDate", "acceptanceDate", "receiptDateForCutoff", "_receiptDateSource"):
        val = resolved.get(key)
        if val:
            fields[key] = val
    cutoff = resolved.get("receiptDateForCutoff")
    if cutoff:
        fields["documentDate"] = cutoff
        return fields

    # 规则抽不到控制权/验收日 → 可选 LLM 语义补抽（BATCH_LLM_ASSIST）
    try:
        from src.llm.batch_assist import enrich_receipt_fields_with_cutoff_llm

        enriched, _notes = enrich_receipt_fields_with_cutoff_llm(
            fields,
            [
                {
                    "doc_type": doc or "receipt",
                    "file_name": "",
                    "raw_text": ocr_text,
                }
            ],
        )
        fields.update(
            {
                k: enriched[k]
                for k in (
                    "deliveryDate",
                    "acceptanceDate",
                    "receiptDateForCutoff",
                    "documentDate",
                    "_receiptDateSource",
                    "_cutoffSemantic",
                    "_cutoffUnresolved",
                )
                if k in enriched
            }
        )
    except Exception:  # noqa: BLE001
        pass
    return fields

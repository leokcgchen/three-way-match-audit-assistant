"""从非结构化文本中提取日期与合同编号。"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import List, Optional, Sequence

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

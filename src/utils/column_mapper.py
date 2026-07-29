"""Excel/CSV 列名智能映射（相似度 + 本地缓存）。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from config.settings import settings

# 标准字段 -> 候选同义词
STANDARD_FIELDS: Dict[str, List[str]] = {
    "业务编号": [
        "业务编号",
        "订单号",
        "销售订单号",
        "采购订单号",
        "凭证号",
        "单据编号",
        "SO",
        "PO",
        "订单编号",
        "order_no",
        "document_no",
    ],
    "入账日期": [
        "入账日期",
        "过账日期",
        "记账日期",
        "凭证日期",
        "会计期间",
        "posting_date",
        "过账日",
    ],
    "金额": [
        "金额",
        "借方金额",
        "贷方金额",
        "凭证金额",
        "交易金额",
        "total_amount",
        "入账金额",
        "收入金额",
    ],
}

# 序时账内部键
LEDGER_INTERNAL_KEYS: Dict[str, str] = {
    "业务编号": "biz_id",
    "入账日期": "posting_date",
    "金额": "amount",
}

MATCH_THRESHOLD = 0.6
WEIGHT_EXACT = 0.5
WEIGHT_CONTAINS = 0.3
WEIGHT_TOKEN = 0.2

_TOKEN_SPLIT_RE = re.compile(r"[\s_/\\（）()\[\]【】\-]+")


def cache_file_path() -> Path:
    return settings.BASE_DIR / ".column_mapping_cache.json"


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def tokenize(text: str) -> set[str]:
    norm = _normalize(text)
    parts = _TOKEN_SPLIT_RE.split(norm)
    return {p for p in parts if p}


def _token_overlap(a: str, b: str) -> float:
    ta, tb = tokenize(a), tokenize(b)
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    union = ta | tb
    return len(inter) / len(union)


def similarity_score(column_name: str, candidate: str) -> float:
    """单列名 vs 单个候选词的相似度 [0, 1]。"""
    col = _normalize(column_name)
    cand = _normalize(candidate)
    if not col or not cand:
        return 0.0

    exact = 1.0 if col == cand else 0.0
    contains = 1.0 if cand in col or col in cand else 0.0
    token = _token_overlap(column_name, candidate)

    return WEIGHT_EXACT * exact + WEIGHT_CONTAINS * contains + WEIGHT_TOKEN * token


def column_to_field_match(column_name: str, field_name: str) -> Tuple[float, int, int]:
    """
    列名与标准字段的最佳匹配详情，用于排序比较（越大越好）。

    返回 (综合得分, 命中候选词长度, -候选词在列表中的序号)。
    同分时优先更具体的同义词（更长），再优先 STANDARD_FIELDS 中更靠前的候选。
    """
    candidates = STANDARD_FIELDS.get(field_name, [])
    if not candidates:
        return 0.0, 0, -999

    best_score = 0.0
    best_cand_len = 0
    best_cand_idx = 999
    for idx, cand in enumerate(candidates):
        score = similarity_score(column_name, cand)
        cand_len = len(_normalize(cand))
        if score > best_score:
            best_score, best_cand_len, best_cand_idx = score, cand_len, idx
        elif score == best_score and score > 0:
            if cand_len > best_cand_len or (
                cand_len == best_cand_len and idx < best_cand_idx
            ):
                best_cand_len, best_cand_idx = cand_len, idx
    return best_score, best_cand_len, -best_cand_idx


def column_to_field_score(column_name: str, field_name: str) -> float:
    """列名与某标准字段（其全部候选词）的最高相似度。"""
    score, _, _ = column_to_field_match(column_name, field_name)
    return score


def load_mapping_cache() -> Dict[str, str]:
    """读取缓存：{Excel列名: 标准字段名}。"""
    path = cache_file_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except (json.JSONDecodeError, OSError):
        return {}
    return {}


def save_mapping_cache(cache: Dict[str, str]) -> None:
    path = cache_file_path()
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def remember_column_mapping(column_name: str, standard_field: str) -> None:
    """用户手动选择后写入缓存。"""
    if not column_name or not standard_field:
        return
    cache = load_mapping_cache()
    cache[str(column_name)] = str(standard_field)
    save_mapping_cache(cache)


def remember_mappings(mappings: Dict[str, str]) -> None:
    """批量写入：{Excel列名: 标准字段名}。"""
    if not mappings:
        return
    cache = load_mapping_cache()
    cache.update(mappings)
    save_mapping_cache(cache)


def auto_map(columns: Sequence[str]) -> Dict[str, str]:
    """
    智能映射 Excel 列名 -> 标准字段名。

    返回示例：{"业务编号": "销售订单号", "入账日期": "过账日期", "金额": "借方金额"}
    无法匹配的标准字段不出现在结果中。
    """
    cols = [str(c).strip() for c in columns if str(c).strip()]
    if not cols:
        return {}

    cache = load_mapping_cache()
    result: Dict[str, str] = {}
    used_columns: set[str] = set()

    # 1) 缓存优先
    for col in cols:
        field = cache.get(col)
        if field in STANDARD_FIELDS and field not in result:
            result[field] = col
            used_columns.add(col)

    # 2) 相似度匹配剩余标准字段
    for field_name in STANDARD_FIELDS:
        if field_name in result:
            continue
        best_col: Optional[str] = None
        best_key: Tuple[float, int, int] = (0.0, 0, -999)
        for col in cols:
            if col in used_columns:
                continue
            match_key = column_to_field_match(col, field_name)
            if match_key > best_key:
                best_key = match_key
                best_col = col
        if best_col and best_key[0] >= MATCH_THRESHOLD:
            result[field_name] = best_col
            used_columns.add(best_col)

    return result


def is_auto_map_successful(mapped: Dict[str, str]) -> bool:
    """至少映射入账日期视为自动映射成功（序时账最低要求）。"""
    return bool(mapped.get("入账日期"))


def to_ledger_mapping(mapped: Dict[str, str]) -> Dict[str, Optional[str]]:
    """标准字段映射 -> 序时账解析器内部键。"""
    return {
        "posting_date": mapped.get("入账日期"),
        "biz_id": mapped.get("业务编号"),
        "amount": mapped.get("金额"),
    }


def auto_map_columns(columns: Sequence[str]) -> Tuple[Dict[str, str], bool]:
    """返回 (标准字段映射, 是否自动映射成功)。"""
    mapped = auto_map(columns)
    return mapped, is_auto_map_successful(mapped)


def suggest_ledger_mapping(columns: Sequence[str]) -> Tuple[Dict[str, Optional[str]], Dict[str, str], bool]:
    """
    序时账专用：返回 (内部 mapping, 标准字段 mapping, 是否全自动成功)。
    """
    standard_mapped, ok = auto_map_columns(columns)
    return to_ledger_mapping(standard_mapped), standard_mapped, ok

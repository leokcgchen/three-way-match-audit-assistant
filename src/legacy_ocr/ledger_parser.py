"""序时账（Excel/CSV）解析与入账日期匹配。"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import pandas as pd

from src.utils.date_extractor import extract_date_from_text

_BIZ_ID_TEXT_PATTERNS = (
    re.compile(r"订单\s*[:=：＝]\s*([A-Za-z0-9\-_]+)"),
    re.compile(r"订单号\s*[:=：＝]\s*([A-Za-z0-9\-_]+)"),
    re.compile(r"业务编号\s*[:=：＝]\s*([A-Za-z0-9\-_]+)"),
    re.compile(r"销售订单号\s*[:=：＝]?\s*([A-Za-z0-9\-_]+)"),
    re.compile(r"发票号\s*[:=：＝]\s*([A-Za-z0-9\-_]+)"),
    re.compile(r"合同索引号\s*[:=：＝]?\s*([A-Za-z0-9\-_]+)"),
    re.compile(r"合同编号\s*[:=：＝]\s*([A-Za-z0-9\-_]+)"),
)

_FILENAME_BIZ_PATTERNS = (
    re.compile(r"(?i)(SO\d{2,4}[-_]?\d{3,6}[IlO]?)"),
    re.compile(r"(?i)((?:EXKJ|EX|KJ)?HT\d{2,4}[-_]?\d{3,6}[IlO]?)"),
    re.compile(r"(?i)(PO\d{2,4}[-_]?\d{3,6}[IlO]?)"),
    re.compile(r"(?i)(INV\d{2,4}[-_]?\d{3,6})"),
    re.compile(r"(?i)(SA\d{2,4}[-_]?\d{3,6})"),
    re.compile(r"(?i)(DO\d{2,4}[-_]?\d{3,6})"),
    re.compile(r"(?i)(BANK\d{2,8}[-_]?\d{0,8})"),
)


def suggest_column_mapping(columns: Sequence[str]) -> Dict[str, Optional[str]]:
    """根据列名智能推荐映射（委托 column_mapper）。"""
    ledger_map, _, _ok = resolve_ledger_column_mapping(columns)
    return ledger_map


def resolve_ledger_column_mapping(
    columns: Sequence[str],
) -> tuple[Dict[str, Optional[str]], Dict[str, str], bool]:
    """返回 (内部 mapping, 标准字段 mapping, 是否自动映射成功)。"""
    from src.utils.column_mapper import suggest_ledger_mapping

    return suggest_ledger_mapping(columns)


def load_ledger_file(
    source: Union[str, Path, bytes, io.BytesIO],
    *,
    filename: str = "",
) -> pd.DataFrame:
    """读取序时账 Excel/CSV。"""
    suffix = Path(filename or str(source)).suffix.lower()
    if isinstance(source, (str, Path)):
        path = Path(source)
        suffix = path.suffix.lower()
        if suffix in {".xlsx", ".xls"}:
            df = pd.read_excel(path)
        elif suffix == ".csv":
            df = pd.read_csv(path, encoding="utf-8-sig")
        else:
            raise ValueError(f"不支持的序时账格式: {suffix}")
    else:
        buf = source if isinstance(source, io.BytesIO) else io.BytesIO(source)
        if suffix in {".xlsx", ".xls"}:
            df = pd.read_excel(buf)
        else:
            df = pd.read_csv(buf, encoding="utf-8-sig")
    df = df.dropna(how="all")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def normalize_biz_id(value: Any) -> str:
    text = str(value or "").strip().upper()
    text = re.sub(r"\s+", "", text)
    # OCR 常见把 SO 识成 S0（数字零）
    text = re.sub(r"^S0(\d{2}[-_]?\d{3,6})$", r"SO\1", text)
    text = re.sub(r"(?<![A-Z])S0(\d{2}[-_]?\d{3,6})(?![0-9A-Z])", r"SO\1", text)
    # 编号尾部 I/l → 1，O → 0（SO25-002I = SO25-0021）
    text = re.sub(r"^([A-Z]{1,8}\d{2}[-_]?\d*)[IL]$", r"\g<1>1", text)
    text = re.sub(r"^([A-Z]{1,8}\d{2}[-_]?\d*)O$", r"\g<1>0", text)
    return text


def looks_like_biz_id(value: Any) -> bool:
    """判断字符串是否像业务编号（避免把付款条款整段当成编号）。"""
    text = normalize_biz_id(value)
    if not text or len(text) > 40:
        return False
    if re.fullmatch(r"[A-Z]{1,10}\d{2,6}[-_]?\d{0,8}", text):
        return True
    if re.fullmatch(r"[A-Z]{1,10}\d{2,6}[-_]?\d{0,8}[IL]", str(value or "").strip().upper()):
        return True
    if re.fullmatch(r"\d{8,20}", text):  # 发票号码
        return True
    return False


def compact_biz_id(value: Any) -> str:
    """去掉分隔符的编号，用于 SO25-0281 ↔ SO250281 等模糊匹配。"""
    return re.sub(r"[-_\s]", "", normalize_biz_id(value))


def normalize_ledger_date(value: Any) -> Optional[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "-"}:
        return None
    parsed = extract_date_from_text(text)
    if parsed:
        return parsed
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return pd.to_datetime(text, format=fmt).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            continue
    try:
        return pd.to_datetime(text).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def extract_biz_ids_from_free_text(text: str) -> List[str]:
    """从任意文本提取业务编号（结构化 订单=XXX + 裸 SO/HT 编号）。"""
    ids: List[str] = []
    for bid in extract_biz_ids_from_text(text):
        if bid not in ids:
            ids.append(bid)
    for pat in _FILENAME_BIZ_PATTERNS:
        for m in pat.finditer(text or ""):
            bid = normalize_biz_id(m.group(1))
            if bid and bid not in ids:
                ids.append(bid)
    return _drop_subsumed_biz_ids(ids)


def _drop_subsumed_biz_ids(ids: List[str]) -> List[str]:
    """KJHT25-0282 已抽出时丢掉被包含的 HT25-0282。"""
    upper = [str(x) for x in ids]
    keep: List[str] = []
    for item in upper:
        u = item.upper()
        if any(
            other.upper() != u and other.upper().endswith(u) and len(other) > len(item)
            for other in upper
        ):
            continue
        keep.append(item)
    return keep


def extract_biz_ids_from_filename(file_name: str) -> List[str]:
    """从文件名提取 SO/HT/PO 等业务编号（如 SO25-0281_HT25-0281_05.pdf）。"""
    return extract_biz_ids_from_free_text(Path(str(file_name or "")).stem)


def collect_workflow_biz_keys(classified: Sequence[Dict[str, Any]]) -> List[str]:
    """汇总合同/订单/发票等全部业务编号，供序时账匹配。"""
    keys: List[str] = []
    for item in classified:
        if item.get("doc_type") not in {"contract", "order", "invoice", "receipt"}:
            continue
        for k in collect_document_biz_keys(dict(item.get("fields") or {})):
            if k not in keys:
                keys.append(k)
        for k in extract_biz_ids_from_filename(str(item.get("file_name") or "")):
            if k not in keys:
                keys.append(k)
    return keys


def collect_order_biz_keys(classified: Sequence[Dict[str, Any]]) -> List[str]:
    """兼容旧名：委托 collect_workflow_biz_keys。"""
    return collect_workflow_biz_keys(classified)


def extract_biz_ids_from_text(text: str) -> List[str]:
    ids: List[str] = []
    for pat in _BIZ_ID_TEXT_PATTERNS:
        for m in pat.finditer(text or ""):
            bid = normalize_biz_id(m.group(1))
            if bid and bid not in ids:
                ids.append(bid)
    return ids


def _parse_amount(value: Any) -> Optional[float]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).replace(",", "").replace("¥", "").replace("￥", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def build_ledger_index(
    df: pd.DataFrame,
    mapping: Dict[str, Optional[str]],
) -> Dict[str, Dict[str, Any]]:
    """
    构建 业务编号 -> {posting_date, amount} 索引。
    同一编号多行时保留最先出现的过账日期。
    """
    posting_col = mapping.get("posting_date")
    if not posting_col or posting_col not in df.columns:
        raise ValueError("未指定有效的入账日期列")

    biz_col = mapping.get("biz_id")
    amount_col = mapping.get("amount")
    index: Dict[str, Dict[str, Any]] = {}

    def _register_biz_id(bid: str, posting: str, amount: Optional[float]) -> None:
        norm = normalize_biz_id(bid)
        if not norm or norm in index:
            return
        entry = {
            "posting_date": posting,
            "amount": amount,
            "biz_id": norm,
            "biz_id_column": biz_col,
        }
        index[norm] = entry
        compact = compact_biz_id(norm)
        if compact and compact not in index:
            index[compact] = entry

    for _, row in df.iterrows():
        posting = normalize_ledger_date(row.get(posting_col))
        if not posting:
            continue
        ids: set[str] = set()
        if biz_col and biz_col in df.columns:
            val = row.get(biz_col)
            if val is not None and not (isinstance(val, float) and pd.isna(val)):
                ids.add(normalize_biz_id(val))
        for col in df.columns:
            val = row.get(col)
            if val is None or (isinstance(val, float) and pd.isna(val)):
                continue
            text = str(val).strip()
            if not text:
                continue
            for bid in extract_biz_ids_from_free_text(text):
                ids.add(bid)
        amount = None
        if amount_col and amount_col in df.columns:
            amount = _parse_amount(row.get(amount_col))
        for bid in ids:
            _register_biz_id(bid, posting, amount)
    return index


def collect_document_biz_keys(fields: Dict[str, Any]) -> List[str]:
    """从 OCR 字段收集可用于序时账匹配的业务编号。"""
    keys: List[str] = []
    # paymentTerms / remarks 只做「编号提取」，整段文本不得当作 biz_id
    whole_value_fields = ("documentNo", "invoiceNo", "contractNo", "orderNo", "warehouseNo")
    extract_only_fields = ("remarks", "paymentTerms", "projectName")
    for name in whole_value_fields + extract_only_fields:
        val = fields.get(name)
        if val is None:
            continue
        text = str(val).strip()
        if not text:
            continue
        if name in whole_value_fields and looks_like_biz_id(text):
            norm = normalize_biz_id(text)
            if norm and norm not in keys:
                keys.append(norm)
        for bid in extract_biz_ids_from_free_text(text):
            if bid not in keys:
                keys.append(bid)
    return keys


def lookup_posting_date(
    ledger_index: Dict[str, Dict[str, Any]],
    biz_keys: Sequence[str],
) -> Optional[Dict[str, Any]]:
    """按业务编号在序时账索引中查找入账日期（支持去分隔符模糊匹配）。"""
    for key in biz_keys:
        norm = normalize_biz_id(key)
        hit = ledger_index.get(norm) or ledger_index.get(compact_biz_id(norm))
        if hit:
            result = dict(hit)
            result["matched_key"] = hit.get("biz_id") or norm
            result["query_key"] = norm
            return result
    return None


def primary_biz_key_for_match(fields: Dict[str, Any]) -> str:
    """用于失败提示的主业务编号。"""
    for key in ("documentNo", "invoiceNo", "orderNo", "contractNo"):
        val = fields.get(key)
        if val and str(val).strip():
            return str(val).strip()
    keys = collect_document_biz_keys(fields)
    return keys[0] if keys else "（无业务编号）"


def list_ledger_row_options(
    df: pd.DataFrame,
    mapping: Dict[str, Optional[str]],
) -> List[Dict[str, Any]]:
    """列出序时账各行供人工匹配下拉选择。"""
    posting_col = mapping.get("posting_date")
    if not posting_col or posting_col not in df.columns:
        return []
    biz_col = mapping.get("biz_id")
    options: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for row_idx, row in df.iterrows():
        posting = normalize_ledger_date(row.get(posting_col))
        if not posting:
            continue
        biz_id = ""
        if biz_col and biz_col in df.columns:
            val = row.get(biz_col)
            if val is not None and not (isinstance(val, float) and pd.isna(val)):
                biz_id = str(val).strip()
        if not biz_id:
            for col in df.columns:
                val = row.get(col)
                if val is None or (isinstance(val, float) and pd.isna(val)):
                    continue
                ids = extract_biz_ids_from_free_text(str(val))
                if ids:
                    biz_id = ids[0]
                    break
        label = f"{biz_id or f'行{int(row_idx)+1}'} | 入账 {posting}"
        if label in seen:
            continue
        seen.add(label)
        options.append(
            {
                "label": label,
                "posting_date": posting,
                "biz_id": biz_id or None,
                "row_idx": int(row_idx),
            }
        )
    return options


def apply_ledger_to_classified(
    classified: List[Dict[str, Any]],
    ledger_index: Dict[str, Dict[str, Any]],
    *,
    fill_invoice_posting: bool = True,
    order_fields: Optional[Dict[str, Any]] = None,
    order_biz_keys: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """将序时账匹配结果写入分类条目；匹配成功时用序时账入账日期覆盖发票字段。"""
    updated: List[Dict[str, Any]] = []
    order_fields = order_fields or {}
    extra_order_keys: List[str] = list(order_biz_keys or [])
    if not extra_order_keys and order_fields:
        extra_order_keys = collect_document_biz_keys(order_fields)

    for item in classified:
        row = dict(item)
        fields = dict(row.get("fields") or {})
        sample_business_id = normalize_biz_id(row.get("sample_business_id"))
        if sample_business_id:
            biz_keys = [sample_business_id]
        else:
            biz_keys = collect_document_biz_keys(fields)
            for k in extract_biz_ids_from_filename(str(row.get("file_name") or "")):
                if k not in biz_keys:
                    biz_keys.append(k)

        hit = lookup_posting_date(ledger_index, biz_keys)
        primary = sample_business_id or primary_biz_key_for_match(fields)
        if primary == "（无业务编号）" and order_fields:
            primary = primary_biz_key_for_match(order_fields)
        query_biz = normalize_biz_id(biz_keys[0]) if biz_keys else None
        ledger_index_column = None
        if hit:
            ledger_index_column = hit.get("biz_id_column")
        if not ledger_index_column:
            ledger_index_column = next(
                (
                    entry.get("biz_id_column")
                    for entry in ledger_index.values()
                    if isinstance(entry, dict) and entry.get("biz_id_column")
                ),
                None,
            )

        if hit:
            matched_biz = hit.get("matched_key") or hit.get("biz_id")
            query_biz = hit.get("query_key") or query_biz
            row["ledger_posting_date"] = hit["posting_date"]
            row["ledger_match_ok"] = True
            row["ledger_matched_biz_id"] = matched_biz
            row["ledger_query_biz_id"] = query_biz
            row["ledger_index_column"] = ledger_index_column
            row["ledger_match_reason"] = {
                "code": "MATCHED",
                "message": "凭证业务编号已在序时账业务主键列中找到相同值。",
                "document_index": sample_business_id or query_biz,
                "document_index_source": row.get("business_index_source") or "legacy_document_key",
                "ledger_index_column": ledger_index_column,
                "query_value": query_biz,
            }
            if hit.get("amount") is not None:
                row["ledger_amount"] = hit["amount"]
            if query_biz and matched_biz and query_biz != matched_biz:
                row["ledger_match_message"] = (
                    f"已匹配序时账业务 {matched_biz}（查询键 {query_biz}）"
                )
            else:
                row["ledger_match_message"] = f"已匹配序时账业务 {matched_biz}"
            if fill_invoice_posting and row.get("doc_type") == "invoice":
                fields["postingDate"] = hit["posting_date"]
                row["fields"] = fields
        else:
            row["ledger_posting_date"] = None
            row["ledger_match_ok"] = False
            row["ledger_matched_biz_id"] = None
            row["ledger_query_biz_id"] = query_biz
            row["ledger_index_column"] = ledger_index_column
            row.pop("ledger_amount", None)
            if query_biz:
                row["ledger_match_message"] = f"未匹配：业务编号 {query_biz}"
                row["ledger_match_reason"] = {
                    "code": "NOT_FOUND",
                    "message": "序时账业务主键列中未找到与凭证业务编号相同的值。",
                    "document_index": sample_business_id or query_biz,
                    "document_index_source": row.get("business_index_source") or "legacy_document_key",
                    "ledger_index_column": ledger_index_column,
                    "query_value": query_biz,
                }
            else:
                row["ledger_match_message"] = "无法关联：未取得抽样业务编号"
                row["ledger_match_reason"] = {
                    "code": "MISSING_DOCUMENT_INDEX",
                    "message": "凭证未取得抽样清单业务编号，未执行序时账查询。",
                    "document_index": None,
                    "document_index_source": row.get("business_index_source"),
                    "ledger_index_column": ledger_index_column,
                    "query_value": None,
                }
        updated.append(row)
    return updated


def preview_rows(df: pd.DataFrame, limit: int = 5) -> pd.DataFrame:
    return df.head(limit)

"""序时账 Excel/CSV/JSONL 解析与列映射填充。"""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence

import pandas as pd

# 列名自动识别关键词（按优先级）
ID_KEYS = (
    "合同编号",
    "合同索引号",
    "合同号",
    "合同编码",
    "样本编号",
    "样品编号",
    "索引号",
    "contract_no",
    "contract_id",
    "htbh",
)
DATE_KEYS = (
    "入账日期",
    "记账日期",
    "凭证日期",
    "业务日期",
    "发生日期",
    "过账日期",
    "entry_date",
    "记账日",
    "日期",
    "date",
)
AMOUNT_KEYS = (
    "收入金额",
    "入账金额",
    "贷方金额",
    "借方金额",
    "发生额",
    "金额",
    "amount",
    "money",
)
CUSTOMER_KEYS = ("客户名称", "客户", "对方单位", "单位名称", "customer")
VOUCHER_KEYS = ("凭证编号", "凭证号", "凭证字号", "单据号", "voucher", "样本编号")
RECEIPT_DATE_KEYS = (
    "签收日期",
    "验收日期",
    "收货日期",
    "交货日期",
    "取样日期",
    "检测日期",
    "receipt_date",
    "日期",
    "date",
)
QTY_KEYS = ("签收数量", "数量", "qty", "quantity")
PAYMENT_KEYS = ("账期天数", "账期", "付款天数", "payment_days")

# 明显不是日期的列名片段
_NON_DATE_NAME_FRAGMENTS = (
    "编号",
    "号码",
    "索引号",
    "索引",
    "凭证",
    "样本",
    "样品",
    "单号",
    "编码",
    "code",
    "voucher",
    "客户",
    "金额",
    "数量",
    "amount",
    "qty",
    "摘要",
    "描述",
    "备注",
    "说明",
    "内容",
    "文本",
    "pdf",
    "summary",
    "desc",
    "note",
    "comment",
)
# HT2501-0001 / GOSPD25-0001 / SA25-0001
_CODE_LIKE = re.compile(r"^[A-Za-z]{1,30}\d{2,6}[-_/]?\d+")
DATE_PLACEHOLDER = "（请选择日期列）"
_MAX_DATE_VALUE_LEN = 40
_LONG_TEXT_AVG_LEN = 50


def parse_ledger_file(uploaded_file: Any) -> pd.DataFrame:
    """读取 Excel/CSV/JSONL，返回 DataFrame。"""
    name = (getattr(uploaded_file, "name", "") or "").lower()
    if name.endswith(".jsonl"):
        return pd.read_json(uploaded_file, lines=True)
    if name.endswith(".csv"):
        return pd.read_csv(uploaded_file)
    if name.endswith(".xlsx") or name.endswith(".xls"):
        try:
            return pd.read_excel(uploaded_file)
        except Exception as exc:
            raise ValueError(
                f"无法读取 Excel 文件（建议另存为 .xlsx）。原因: {exc}"
            ) from exc
    raise ValueError(
        "文件格式不支持，请上传 Excel（.xlsx/.xls）、CSV 或 JSONL（.jsonl）文件"
    )


def guess_column(
    columns: Sequence[str],
    keywords: Sequence[str],
    *,
    exclude: Optional[Sequence[str]] = None,
) -> Optional[str]:
    """按关键词猜测最可能的列名；找不到则返回 None。"""
    excluded = {str(x) for x in (exclude or [])}
    cols = [str(c) for c in columns if str(c) not in excluded]
    lower_map = {c: c.lower() for c in cols}
    for key in keywords:
        key_l = key.lower()
        for col, col_l in lower_map.items():
            if key_l == col_l or key_l in col_l:
                return col
    return None


def _looks_like_date_column_name(col: str) -> bool:
    c = str(col).lower()
    if any(x in c for x in ("日期", "date", "时间", "time")):
        if any(x in c for x in ("编号", "号码", "凭证号", "样本", "样品")):
            return False
        return True
    return False


def is_non_date_column_name(col: str) -> bool:
    """列名是否明显不像日期（编号/索引号/摘要/金额等）。"""
    if _looks_like_date_column_name(col):
        return False
    name = str(col).strip()
    c = name.lower()
    if any(frag.lower() in c for frag in _NON_DATE_NAME_FRAGMENTS):
        return True
    # 「合同索引号」「样本号」等以「号」结尾，但「日期」除外
    if name.endswith("号") and "日期" not in name:
        return True
    return False


def is_long_text_series(series: pd.Series, *, avg_len: int = _LONG_TEXT_AVG_LEN) -> bool:
    """长文本列（合同摘要等）不可作为日期列。"""
    values = series.dropna().astype(str).str.strip()
    values = values[values.ne("") & values.str.lower().ne("nan")]
    if values.empty:
        return False
    sample = values.head(20)
    return float(sample.str.len().mean()) >= avg_len


def is_likely_date_series(series: pd.Series, *, min_ratio: float = 0.6) -> bool:
    """列内容是否像日期。"""
    if is_long_text_series(series):
        return False
    return _date_parse_ratio(series) >= min_ratio


def _date_parse_ratio(series: pd.Series, sample_size: int = 30) -> float:
    """抽样计算可解析为日期、且不像业务编号/长文本的比例。"""
    values = series.dropna().astype(str).str.strip()
    values = values[values.ne("") & values.str.lower().ne("nan")]
    if values.empty:
        return 0.0
    sample = values.head(sample_size)
    ok = 0
    for raw in sample:
        if len(raw) > _MAX_DATE_VALUE_LEN:
            continue
        if _CODE_LIKE.match(raw):
            continue
        try:
            parsed = pd.to_datetime(raw, errors="coerce")
        except Exception:
            continue
        if pd.notna(parsed):
            ok += 1
    return ok / len(sample)


def list_date_candidate_columns(df: pd.DataFrame) -> list[str]:
    """列出内容像日期的列，供提示用户手动选择。"""
    out: list[str] = []
    for col in df.columns:
        name = str(col)
        if is_non_date_column_name(name):
            continue
        if is_likely_date_series(df[col]):
            out.append(name)
    return out


def guess_date_column(
    df: pd.DataFrame,
    keywords: Sequence[str] = DATE_KEYS,
    *,
    exclude: Optional[Sequence[str]] = None,
) -> Optional[str]:
    """优先按列名猜日期列；失败则按内容可解析率选择，绝不默认选编号/摘要列。"""
    cols = [str(c) for c in df.columns]
    excluded = {str(x) for x in (exclude or [])}

    by_name = guess_column(cols, keywords, exclude=list(excluded))
    if (
        by_name
        and not is_non_date_column_name(by_name)
        and not is_long_text_series(df[by_name])
        and is_likely_date_series(df[by_name])
    ):
        return by_name

    for col in cols:
        if col in excluded:
            continue
        if _looks_like_date_column_name(col) and is_likely_date_series(df[col]):
            return col

    best_col = None
    best_ratio = 0.0
    for col in cols:
        if col in excluded or is_non_date_column_name(col):
            continue
        if is_long_text_series(df[col]):
            continue
        ratio = _date_parse_ratio(df[col])
        if ratio > best_ratio:
            best_ratio = ratio
            best_col = col
    if best_col and best_ratio >= 0.6:
        return best_col
    return None


def default_index(
    columns: Sequence[str],
    guessed: Optional[str],
    *,
    df: Optional[pd.DataFrame] = None,
    prefer_date: bool = False,
) -> int:
    """selectbox 的默认 index；未猜中且 prefer_date 时回落到占位项（index 0）。"""
    if not columns:
        return 0
    if guessed and guessed in columns:
        return list(columns).index(guessed)
    if prefer_date and df is not None:
        best_i, best_r = None, -1.0
        for i, col in enumerate(columns):
            if col == DATE_PLACEHOLDER:
                continue
            if is_non_date_column_name(str(col)):
                continue
            if col not in df.columns:
                continue
            if is_long_text_series(df[col]):
                continue
            ratio = _date_parse_ratio(df[col])
            if ratio > best_r:
                best_r, best_i = ratio, i
        if best_i is not None and best_r >= 0.6:
            return best_i
        # 找不到可靠日期列：若有占位项则选占位，避免误选摘要列
        if DATE_PLACEHOLDER in columns:
            return list(columns).index(DATE_PLACEHOLDER)
        return 0
    for i, col in enumerate(columns):
        if col == DATE_PLACEHOLDER:
            continue
        if not is_non_date_column_name(str(col)):
            return i
    return 0


def clear_bad_date_selection(
    df: pd.DataFrame, session_key: str, state: Any
) -> None:
    """若 session 里缓存的日期列不像日期，清掉以便回落到自动猜测/占位项。"""
    cur = state.get(session_key)
    if cur is None:
        return
    if cur == DATE_PLACEHOLDER:
        return
    if cur not in df.columns:
        state.pop(session_key, None)
        return
    if (
        is_non_date_column_name(str(cur))
        or is_long_text_series(df[cur])
        or not is_likely_date_series(df[cur])
    ):
        state.pop(session_key, None)


def truncate_samples(values: Sequence[Any], *, limit: int = 3, width: int = 36) -> list[str]:
    """缩短样例文本，避免把整段摘要刷到页面上。"""
    out: list[str] = []
    for raw in list(values)[:limit]:
        text = str(raw).replace("\n", " ").strip()
        if len(text) > width:
            text = text[:width] + "…"
        out.append(text)
    return out


def extract_payment_days_from_text(text: str) -> Optional[int]:
    """从摘要中提取「签收后N日」。"""
    if not text:
        return None
    match = re.search(r"签收后\s*(\d+)\s*日", str(text))
    if not match:
        return None
    return int(match.group(1))


def parse_date_series(series: pd.Series, col_name: str) -> pd.Series:
    """将一列解析为 ISO 日期字符串；失败时给出可读错误。"""
    try:
        parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    except TypeError:
        parsed = pd.to_datetime(series, errors="coerce")
    nonempty = series.notna() & series.astype(str).str.strip().ne("")
    nonempty = nonempty & series.astype(str).str.lower().ne("nan")
    bad_mask = parsed.isna() & nonempty
    if bad_mask.any():
        sample = series[bad_mask].astype(str).head(3).tolist()
        raise ValueError(
            f"列「{col_name}」无法解析为日期（样例: {sample}）。"
            f"请确认选的是日期列，而不是凭证号/合同编号/样本编号等。"
        )
    if parsed.isna().all():
        raise ValueError(f"列「{col_name}」全部为空，无法作为日期列。")
    return parsed.dt.date.map(lambda d: d.isoformat() if pd.notna(d) else None)


def parse_amount_series(series: pd.Series, col_name: str) -> pd.Series:
    """将一列解析为 float 金额。"""
    cleaned = (
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("万元", "", regex=False)
        .str.replace("元", "", regex=False)
        .str.strip()
    )
    parsed = pd.to_numeric(cleaned, errors="coerce")
    nonempty = series.notna() & cleaned.ne("") & cleaned.str.lower().ne("nan")
    bad_mask = parsed.isna() & nonempty
    if bad_mask.any():
        sample = series[bad_mask].astype(str).head(3).tolist()
        raise ValueError(
            f"列「{col_name}」无法转换为金额（样例: {sample}）。请确认选的是金额列。"
        )
    return parsed


def map_and_fill_ledger_data(
    df: pd.DataFrame, selected_row: int, col_mapping: dict
) -> dict:
    """根据列映射，从 DataFrame 指定行提取数据，返回 LedgerEntryInfo 字典。"""
    if selected_row < 0 or selected_row >= len(df):
        raise ValueError(f"行号越界：第 {selected_row + 1} 行不存在")

    row = df.iloc[selected_row]
    date_col = col_mapping["date_col"]
    amount_col = col_mapping["amount_col"]

    try:
        parsed_date = pd.to_datetime(row[date_col], errors="raise")
        entry_date = parsed_date.date().isoformat()
    except Exception as exc:
        raise ValueError(
            f"第 {selected_row + 1} 行入账日期无法解析为日期"
            f"（列「{date_col}」= {row[date_col]!r}）。"
            f"请确认选的是日期列，而不是凭证号等。"
        ) from exc

    try:
        raw_amount = row[amount_col]
        if isinstance(raw_amount, str):
            raw_amount = (
                raw_amount.replace(",", "")
                .replace("万元", "")
                .replace("元", "")
                .strip()
            )
        entry_amount = float(raw_amount)
    except Exception as exc:
        raise ValueError(
            f"第 {selected_row + 1} 行金额无法转换为数字"
            f"（列「{amount_col}」= {row[amount_col]!r}）"
        ) from exc

    voucher_col = col_mapping.get("voucher_col")
    customer_col = col_mapping.get("customer_col")

    voucher_id = None
    if voucher_col:
        val = row[voucher_col]
        voucher_id = None if pd.isna(val) else str(val).strip() or None

    customer_name = None
    if customer_col:
        val = row[customer_col]
        customer_name = None if pd.isna(val) else str(val).strip() or None

    return {
        "entry_date": entry_date,
        "entry_amount": entry_amount,
        "voucher_id": voucher_id,
        "customer_name": customer_name,
    }

"""裁剪序时账两用：工作台立样本笔 + 后续测试用入账日/金额。

本系统不做抽样设计。上传的是已按抽样裁过的序时账：
- 工作台只看业务号是否对上、凭证齐不齐
- 同一份里的入账日/金额留给测试，不挡字段确认
- 上传页不再另传序时账
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

_ORDER_COL_BEST = ("销售订单号", "订单编号", "销售订单", "订单号", "sono", "salesorder", "order_no")
_DATE_COL = ("过账日期", "入账日期", "记账日期", "凭证日期", "过账日", "posting")
_AMT_COL_CREDIT = ("贷方金额", "贷方发生额", "贷方")
_AMT_COL_DEBIT = ("借方金额", "借方发生额", "借方")
_AMT_COL_ANY = ("价税合计", "含税金额", "入账金额", "收入金额", "金额")
_CUST_COL = ("客户名称", "往来单位名称", "客户")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_header(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "")).casefold()


def _header_hits(header: str, hints: Iterable[str]) -> bool:
    n = _norm_header(header)
    if not n:
        return False
    return any(_norm_header(h) in n or n in _norm_header(h) for h in hints if h)


def normalize_biz_ids(raw: Iterable[Any]) -> list[str]:
    from src.legacy_ocr.ledger_parser import looks_like_biz_id, normalize_biz_id

    out: list[str] = []
    seen: set[str] = set()
    for x in raw or []:
        nid = normalize_biz_id(x) if x is not None else ""
        if not nid or nid in seen:
            continue
        if not looks_like_biz_id(nid) and not re.match(r"^[A-Z]{1,8}\d", nid):
            continue
        seen.add(nid)
        out.append(nid)
    return out


def _pick_col(headers: list[str], hints: Iterable[str], *, skip: set[str] | None = None) -> str:
    skip_n = {_norm_header(x) for x in (skip or set())}
    for hint in hints:
        for h in headers:
            if _norm_header(h) in skip_n:
                continue
            if _header_hits(h, (hint,)):
                return h
    return ""


def _cell_str(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        try:
            return value.strftime("%Y-%m-%d")
        except Exception:
            return str(value).strip()
    text = str(value).strip()
    if text.lower() in {"none", "nan", "-"}:
        return ""
    return text


def _parse_sheet_rows(headers: list[str], data_rows: list[list[Any]], *, sheet: str) -> list[dict[str, Any]]:
    from src.legacy_ocr.ledger_parser import looks_like_biz_id, normalize_biz_id, normalize_ledger_date

    order_col = _pick_col(headers, _ORDER_COL_BEST)
    if not order_col:
        return []
    date_col = _pick_col(headers, _DATE_COL)
    amt_col = _pick_col(headers, _AMT_COL_CREDIT) or _pick_col(headers, _AMT_COL_DEBIT) or _pick_col(
        headers, _AMT_COL_ANY, skip={order_col}
    )
    cust_col = _pick_col(headers, _CUST_COL)
    idx = {h: i for i, h in enumerate(headers)}

    def take(row: list[Any], col: str) -> Any:
        if not col or col not in idx:
            return None
        i = idx[col]
        return row[i] if i < len(row) else None

    out: list[dict[str, Any]] = []
    for row in data_rows:
        raw_id = take(row, order_col)
        nid = normalize_biz_id(raw_id) if raw_id is not None else ""
        if not nid or not (looks_like_biz_id(nid) or re.match(r"^[A-Z]{1,8}\d", nid)):
            continue
        book_date = ""
        if date_col:
            book_date = normalize_ledger_date(take(row, date_col)) or _cell_str(take(row, date_col))
        amt_raw = take(row, amt_col) if amt_col else None
        book_amount = None
        if amt_raw is not None and str(amt_raw).strip() not in {"", "None", "nan"}:
            try:
                book_amount = float(str(amt_raw).replace(",", "").replace("，", ""))
            except ValueError:
                book_amount = None
        out.append(
            {
                "business_id": nid,
                "book_date": book_date,
                "book_amount": book_amount,
                "customer": _cell_str(take(row, cust_col)) if cust_col else "",
                "sheet": sheet,
            }
        )
    return out


def parse_sample_workbook(path: str | Path) -> dict[str, Any]:
    """从序时账/抽样表抽出业务号。表头不固定：优先订单号列，不把凭证号当业务号。"""
    from openpyxl import load_workbook

    src = Path(path)
    if not src.is_file():
        raise ValueError("抽样文件不存在")
    wb = load_workbook(src, data_only=True, read_only=True)
    all_rows: list[dict[str, Any]] = []
    sheets_used: list[str] = []
    try:
        for ws in wb.worksheets:
            raw: list[list[Any]] = []
            for row in ws.iter_rows(values_only=True):
                raw.append(list(row))
            header_idx = -1
            headers: list[str] = []
            for i, row in enumerate(raw[:12]):
                cells = [str(c).strip() if c is not None else "" for c in row]
                if _pick_col(cells, _ORDER_COL_BEST):
                    header_idx = i
                    headers = cells
                    break
            if header_idx < 0 or not headers:
                continue
            parsed = _parse_sheet_rows(headers, raw[header_idx + 1 :], sheet=str(ws.title))
            if parsed:
                sheets_used.append(str(ws.title))
                all_rows.extend(parsed)
    finally:
        wb.close()
    if not all_rows:
        raise ValueError("未识别到订单号/业务号列。请确认表头含「销售订单号」或「订单编号」一类。")

    from src.legacy_ocr.ledger_parser import compact_biz_id

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in all_rows:
        key = compact_biz_id(row["business_id"])
        if key in seen:
            continue
        seen.add(key)
        merged.append(row)
    ledger_rows = [
        {
            "business_id": r["business_id"],
            "book_date": r.get("book_date") or "",
            "book_amount": r.get("book_amount"),
            "customer": r.get("customer") or "",
            "sheet": r.get("sheet") or "",
        }
        for r in all_rows
    ]
    return {
        "rows": merged,
        "business_ids": [r["business_id"] for r in merged],
        "sheets": sheets_used,
        "ledger_rows": ledger_rows,
        "ledger_columns": ["business_id", "book_date", "book_amount", "customer", "sheet"],
        "ledger_mapping": {
            "biz_id": "business_id",
            "posting_date": "book_date",
            "amount": "book_amount",
        },
        "ledger_auto_ok": any(bool(r.get("book_date")) for r in ledger_rows),
        "ledger_standard_map": {
            "业务编号": "business_id",
            "入账日期": "book_date",
            "金额": "book_amount",
        },
    }


def ledger_patch_from_parsed(parsed: dict[str, Any], *, path: str = "") -> dict[str, Any]:
    """把裁剪序时账解析结果写成现有测试用的 ledger_* 字段。"""
    return {
        "ledger_path": path or None,
        "ledger_rows": list(parsed.get("ledger_rows") or []),
        "ledger_columns": list(parsed.get("ledger_columns") or []),
        "ledger_mapping": dict(parsed.get("ledger_mapping") or {}),
        "ledger_auto_ok": bool(parsed.get("ledger_auto_ok")),
        "ledger_standard_map": dict(parsed.get("ledger_standard_map") or {}),
    }


def build_sample_population(
    *,
    business_ids: Iterable[Any],
    source: str = "external_import",
    note: str = "",
    rows: list[dict[str, Any]] | None = None,
    sheets: list[str] | None = None,
) -> dict[str, Any]:
    ids = normalize_biz_ids(business_ids)
    by_id = {str(r.get("business_id") or ""): r for r in (rows or []) if r.get("business_id")}
    kept_rows = [by_id[i] for i in ids if i in by_id]
    return {
        "business_ids": ids,
        "count": len(ids),
        "source": str(source or "external_import"),
        "note": str(note or ""),
        "imported_at": _utc_now(),
        "cannot_claim": "不替代抽样设计；不证明总体完整性",
        "rows": kept_rows,
        "sheets": list(sheets or []),
    }


def chain_in_population(chain_id: str, population: Optional[dict[str, Any]]) -> Optional[bool]:
    """None=未导入总体；True/False=是否在清单内。"""
    if not isinstance(population, dict) or not population.get("business_ids"):
        return None
    from src.legacy_ocr.ledger_parser import compact_biz_id, normalize_biz_id

    nid = normalize_biz_id(chain_id)
    pool = {normalize_biz_id(x) for x in (population.get("business_ids") or [])}
    if nid in pool:
        return True
    c = compact_biz_id(nid)
    return any(compact_biz_id(x) == c for x in pool if x)


def desk_sample_ids(job: dict[str, Any]) -> list[str]:
    """工作台样本笔：清单优先；识别出但不在清单里的笔附在后面。

    截断号（SO25-002 相对清单里的 SO25-0021）不单独立笔。
    """
    from src.legacy_ocr.ledger_parser import compact_biz_id
    from src.workflow.chain_workspace import list_business_chains

    pop = job.get("sample_population") if isinstance(job.get("sample_population"), dict) else {}
    ids = normalize_biz_ids(pop.get("business_ids") or [])
    seen = {compact_biz_id(x) for x in ids}
    compact_ids = list(seen)
    for row in list_business_chains(list(job.get("classified") or [])):
        cid = str(row.get("chain_id") or "").strip()
        if not cid or cid == "未识别业务号":
            continue
        key = compact_biz_id(cid)
        if key in seen:
            continue
        if any(p.startswith(key) and len(p) > len(key) for p in compact_ids):
            continue
        seen.add(key)
        ids.append(cid)
    return ids

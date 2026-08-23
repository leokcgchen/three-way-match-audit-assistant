"""工作台补齐能力：序时账人工匹配、改类型重抽、底稿预览、AI 解读。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import pandas as pd


def ledger_row_options_from_job(job: dict[str, Any]) -> list[dict[str, Any]]:
    from src.legacy_ocr.ledger_parser import list_ledger_row_options

    rows = job.get("ledger_rows") or []
    mapping = job.get("ledger_mapping") or {}
    if not rows or not mapping:
        return []
    df = pd.DataFrame(rows)
    return list_ledger_row_options(df, mapping)


def apply_manual_ledger_match(
    classified: list[dict[str, Any]],
    *,
    file_name: str,
    option: dict[str, Any],
) -> list[dict[str, Any]]:
    """将人工选中的序时账行套到指定单据（发票/订单）。"""
    updated = [dict(x) for x in classified]
    target = None
    for item in updated:
        if str(item.get("file_name") or "") == file_name:
            target = item
            break
    if target is None:
        raise KeyError(f"document not found: {file_name}")
    if target.get("doc_type") not in {"invoice", "order"}:
        raise ValueError("仅支持对发票或订单做序时账人工匹配")

    posting = option.get("posting_date")
    biz_id = option.get("biz_id")
    fields = dict(target.get("fields") or {})
    target["ledger_posting_date"] = posting
    target["ledger_match_ok"] = True
    target["ledger_match_manual"] = True
    target["ledger_evaluated"] = True
    target["ledger_matched_biz_id"] = biz_id
    target["ledger_query_biz_id"] = biz_id
    target["ledger_match_message"] = f"已匹配序时账业务 {biz_id or '—'}（人工选择）"
    if target.get("doc_type") == "invoice" and posting:
        fields["postingDate"] = posting
        target["fields"] = fields
    return updated


def reclassify_document(
    item: dict[str, Any],
    new_type: str,
) -> dict[str, Any]:
    """改识别类型并按新类型重抽字段（保留序时账入账日权威）。"""
    from src.legacy_ocr.ocr_adapter import LegacyOcrAdapter
    from src.workflow.classify import DOC_TYPE_TO_OCR

    if new_type == item.get("doc_type") and item.get("fields"):
        return item
    updated = dict(item)
    updated["doc_type"] = new_type
    updated["manual_override"] = True
    raw_text = str(updated.get("raw_text") or "")
    if raw_text and new_type != "other":
        adapter = LegacyOcrAdapter(use_mock_when_unavailable=True)
        ocr_type = DOC_TYPE_TO_OCR.get(new_type, "other")
        fields = dict(adapter.extract_fields(raw_text, ocr_type) or {})
        fields["documentType"] = ocr_type
        ledger_posting = item.get("ledger_posting_date")
        if new_type == "invoice" and ledger_posting and item.get("ledger_match_ok"):
            fields["postingDate"] = ledger_posting
        updated["fields"] = fields
    else:
        fields = dict(updated.get("fields") or {})
        fields["documentType"] = DOC_TYPE_TO_OCR.get(new_type, "other")
        updated["fields"] = fields
    updated["manual_edited"] = True
    return updated


def _excel_col_letter(idx: int) -> str:
    """0-based index → A, B, … Z, AA…"""
    n = idx + 1
    letters = ""
    while n:
        n, r = divmod(n - 1, 26)
        letters = chr(65 + r) + letters
    return letters


def workbook_sheet_preview(
    path: str | Path,
    *,
    sheet: str | None = None,
    limit: int = 40,
    max_cols: int = 32,
) -> dict[str, Any]:
    """读取已生成底稿的 sheet 列表与预览行。

    底稿多为「标题区 + 表头」布局，不能用首行当 DataFrame columns，
    否则前端会出现一排 Unnamed 列、宽表撑破工作台。
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    xl = pd.ExcelFile(p)
    sheets = list(xl.sheet_names)
    pick = sheet if sheet in sheets else (sheets[0] if sheets else None)
    rows: list[dict[str, Any]] = []
    columns: list[str] = []
    note = ""
    if pick:
        raw = xl.parse(pick, header=None, dtype=object).fillna("")
        # 去掉尾部全空列
        if not raw.empty:
            nonempty = [
                i
                for i in range(raw.shape[1])
                if any(str(v).strip() for v in raw.iloc[:, i].tolist())
            ]
            if nonempty:
                raw = raw.iloc[:, : nonempty[-1] + 1]
        total_cols = int(raw.shape[1]) if not raw.empty else 0
        if total_cols > max_cols:
            raw = raw.iloc[:, :max_cols]
            note = f"仅预览前 {max_cols}/{total_cols} 列；完整内容请下载 xlsx。"
        columns = [_excel_col_letter(i) for i in range(raw.shape[1])]
        preview = raw.head(limit)
        for _, series in preview.iterrows():
            row: dict[str, Any] = {}
            for i, col in enumerate(columns):
                v = series.iloc[i] if i < len(series) else ""
                if hasattr(v, "isoformat"):
                    row[col] = v.isoformat()
                elif v is None:
                    row[col] = ""
                else:
                    text = str(v).strip()
                    # 单元格过长会把布局拉爆
                    row[col] = text if len(text) <= 120 else text[:117] + "…"
            rows.append(row)
    return {
        "sheets": sheets,
        "sheet": pick,
        "columns": columns,
        "rows": rows,
        "path": str(p),
        "note": note or "按行列原样预览（非表头模式）；完整内容请下载 xlsx。",
    }


def interpret_test_result(family: str, payload: dict[str, Any]) -> dict[str, Any]:
    from src.llm.conclusion_interpret import (
        interpret_amount_conclusion,
        interpret_contract_conclusion,
        interpret_cutoff_conclusion,
    )

    if family == "amount":
        return interpret_amount_conclusion(payload)
    if family == "contract":
        return interpret_contract_conclusion(payload)
    if family in {"cutoff", "three_way"}:
        return interpret_cutoff_conclusion(payload)
    raise ValueError(f"未知解读类型: {family}")


def find_receipt_choices(classified: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """多签收候选，供三单选择。"""
    out: list[dict[str, Any]] = []
    for i, item in enumerate(classified):
        if item.get("doc_type") != "receipt":
            continue
        fields = item.get("fields") or {}
        date = (
            fields.get("acceptanceDate")
            or fields.get("documentDate")
            or fields.get("receiptDate")
            or ""
        )
        out.append(
            {
                "index": i,
                "file_name": item.get("file_name"),
                "date": str(date or ""),
                "label": f"{item.get('file_name')} · {date or '无日期'}",
            }
        )
    return out

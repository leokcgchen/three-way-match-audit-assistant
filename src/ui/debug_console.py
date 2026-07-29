"""截止性测试 Agent — Streamlit 调试控制台。"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import requests
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from config.settings import settings
from src.models.schemas import CutoffResponse
from src.reporting.workbook_generator import WorkbookGenerator
from src.three_way_match.matcher import ThreeWayMatcher, build_request_from_ocr_fields
from src.utils.date_extractor import extract_days_from_description, pick_receipt_date_from_fields

API_BASE = "http://localhost:8000"
CUTOFF_URL = f"{API_BASE}/api/v1/cutoff"
THREE_WAY_URL = f"{API_BASE}/api/v1/three-way-match"
DEFAULT_RECEIPT = date(2026, 6, 1)
DEFAULT_ENTRY = date(2026, 6, 1)

DOC_TYPE_LABELS = {
    "contract": "合同",
    "order": "订单",
    "receipt": "入库单",
    "invoice": "发票",
    "other": "其他",
}

DOC_TYPE_TO_OCR = {
    "contract": "contract",
    "order": "purchase_order",
    "receipt": "warehouse_receipt",
    "invoice": "invoice",
    "other": "other",
}

INVOICE_FILENAME_KEYWORDS: tuple[str, ...] = (
    "增值税发票",
    "电子发票",
    "专用发票",
    "普通发票",
    "税票",
    "发票",
    "invoice",
    "INV",
    "FP",
)

INVOICE_OCR_KEYWORDS: tuple[str, ...] = (
    "发票代码",
    "发票号码",
    "价税合计",
    "增值税",
    "电子发票",
    "专用发票",
    "普通发票",
    "开票日期",
    "税率",
    "税额",
)

_COMPLETENESS_KEYS: dict[str, tuple[str, ...]] = {
    "contract": ("paymentTerms", "contractNo", "documentNo", "documentDate"),
    "order": ("totalAmount", "supplierName", "documentNo", "paymentTerms", "quantity"),
    "receipt": (
        "documentDate",
        "deliveryDate",
        "totalAmount",
        "documentNo",
        "supplierName",
        "quantity",
    ),
    "invoice": (
        "postingDate",
        "totalAmount",
        "invoiceNo",
        "documentNo",
        "documentDate",
        "supplierName",
    ),
}


def classify_document(
    file_name: str,
    ocr_preview: str = "",
    *,
    slot_hint: str = "",
) -> str:
    """按文件名 + OCR + 上传槽位识别单据类型（发票>入库单>订单>合同>其他）。"""
    name = (file_name or "").strip()
    text = (ocr_preview or "").strip()

    def _name_has(*keywords: str) -> bool:
        lower = name.lower()
        for kw in keywords:
            if kw.lower() in lower:
                return True
        return False

    def _text_has(*keywords: str) -> bool:
        for kw in keywords:
            if kw in text:
                return True
        return False

    def _name_token(*tokens: str) -> bool:
        for token in tokens:
            if re.search(
                rf"(?i)(?:^|[^A-Za-z0-9]){re.escape(token)}(?:[^A-Za-z0-9]|$)",
                name,
            ):
                return True
        return False

    def _classify_by_filename() -> str:
        # 1) 发票（最高优先级）
        if _name_has("增值税发票"):
            return "invoice"
        if _name_has(*INVOICE_FILENAME_KEYWORDS) or _name_token("FP", "INV"):
            return "invoice"

        # 2) 入库单 / 签收单
        if (
            _name_has(
                "销售发货单",
                "发货单",
                "产品验收单",
                "验收单",
                "客户签收",
                "签收单",
                "入库单",
                "收货单",
                "delivery",
                "receipt",
                "warehouse",
            )
            or _name_has("签收", "验收", "入库", "收货")
        ):
            return "receipt"

        # 3) 订单
        if (
            _name_has("销售订单", "采购订单", "订单", "order", "采购单", "sales order")
            or _name_token("SO", "PO")
        ):
            return "order"

        # 4) 合同
        if (
            _name_has("销售合同", "采购合同", "合同", "contract", "协议", "agreement")
            or _name_token("HT")
        ):
            return "contract"
        return "other"

    def _classify_by_ocr() -> str:
        if not text:
            return "other"
        # OCR 全文扫描；发票特征优先于订单/合同泛化词
        if _text_has(*INVOICE_OCR_KEYWORDS):
            return "invoice"
        if _text_has("签收人", "验收人", "收货日期", "入库单号", "签收日期"):
            return "receipt"
        if _text_has("订单编号", "采购方", "供应商", "订单日期"):
            return "order"
        if _text_has("合同编号", "甲方", "乙方", "签订日期", "付款条款"):
            return "contract"
        return "other"

    name_type = _classify_by_filename()
    if name_type != "other":
        return name_type

    ocr_type = _classify_by_ocr()
    if ocr_type != "other":
        return ocr_type

    hint = (slot_hint or "").strip().lower()
    if hint in DOC_TYPE_LABELS:
        return hint
    return "other"


def _apply_doc_type_override(
    item: dict[str, Any],
    new_type: str,
    adapter: Any | None = None,
) -> dict[str, Any]:
    """用户手动修正分类后，更新类型并按新类型重抽字段。"""
    if new_type == item.get("doc_type"):
        return item
    updated = dict(item)
    updated["doc_type"] = new_type
    updated["manual_override"] = True
    raw_text = str(updated.get("raw_text") or "")
    if raw_text and adapter is not None and new_type != "other":
        ocr_type = DOC_TYPE_TO_OCR.get(new_type, "other")
        fields = dict(adapter.extract_fields(raw_text, ocr_type) or {})
        fields["documentType"] = ocr_type
        updated["fields"] = fields
        if new_type == "contract" and not _field_filled(fields.get("paymentTerms")):
            m = re.search(
                r"(签收后\s*\d+\s*[日天]|验收后\s*\d+\s*[日天]|票到\s*\d+\s*[日天])",
                raw_text,
            )
            if m:
                fields["paymentTerms"] = m.group(1).replace(" ", "")
                updated["fields"] = fields
    return updated


def _primary_biz_key_label(fields: dict[str, Any]) -> str:
    from src.legacy_ocr.ledger_parser import collect_document_biz_keys

    for key in ("documentNo", "invoiceNo", "contractNo", "orderNo"):
        val = fields.get(key)
        if val and str(val).strip():
            return str(val).strip()
    keys = collect_document_biz_keys(fields) if fields else []
    return keys[0] if keys else "（无业务编号）"


WORKFLOW_UI_VERSION = "2026-07-29-manual-sync-v8"


def _has_ledger_df() -> bool:
    """安全判断 session 中是否已有序时账 DataFrame（避免 DataFrame 布尔歧义）。"""
    df = st.session_state.get("workflow_ledger_df")
    if df is None:
        return False
    try:
        return not df.empty
    except AttributeError:
        return True


def _fallback_fields_from_filename(file_name: str, doc_type: str) -> dict[str, Any]:
    """OCR 失败时从文件名提取最小编号字段，保证流程可继续。"""
    from src.legacy_ocr.ledger_parser import extract_biz_ids_from_filename

    fields: dict[str, Any] = {}
    ids = extract_biz_ids_from_filename(file_name)
    if not ids:
        return fields
    if doc_type == "contract":
        ht = next((x for x in ids if x.startswith("HT")), ids[0])
        fields["contractNo"] = ht
        fields["documentNo"] = ht
    elif doc_type == "order":
        so = next((x for x in ids if x.startswith("SO")), ids[0])
        fields["documentNo"] = so
        fields["orderNo"] = so
    elif doc_type == "invoice":
        inv = next((x for x in ids if x.startswith("INV")), None)
        so = next((x for x in ids if x.startswith("SO")), None)
        primary = inv or so or ids[0]
        fields["invoiceNo"] = primary
        fields["documentNo"] = primary
    else:
        fields["documentNo"] = ids[0]
    if len(ids) > 1:
        fields["remarks"] = "；".join(f"编号={x}" for x in ids[:4])
    return fields


def _merge_fields(primary: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    merged = dict(fallback)
    for key, val in primary.items():
        if val is not None and str(val).strip():
            merged[key] = val
    return merged


def _sync_manual_widgets_from_ledger(
    classified: list[dict[str, Any]],
    *,
    force: bool = False,
) -> None:
    """将序时账匹配结果同步到手工补全输入框。"""
    if not force and not st.session_state.pop("_ledger_sync_pending", False):
        return
    invoice = next((x for x in classified if x.get("doc_type") == "invoice"), None)
    if not invoice:
        return
    if invoice.get("ledger_match_ok") and invoice.get("ledger_posting_date"):
        st.session_state["wf_manual_posting"] = str(invoice["ledger_posting_date"])
    elif invoice.get("fields", {}).get("postingDate"):
        st.session_state["wf_manual_posting"] = str(invoice["fields"]["postingDate"])


def _render_workflow_issue_panel(classified: list[dict[str, Any]]) -> None:
    """汇总 OCR/序时账问题，避免页面散落多条报错。"""
    ocr_failed = [
        x for x in classified if x.get("ocr_source") in {"ocr_failed", "failed"} or x.get("error")
    ]
    ledger_invoice = next((x for x in classified if x.get("doc_type") == "invoice"), None)
    ledger_unmatched = (
        ledger_invoice
        and ledger_invoice.get("ledger_evaluated")
        and not ledger_invoice.get("ledger_match_ok")
    )

    if not ocr_failed and not ledger_unmatched:
        return

    with st.expander("处理提示（OCR / 序时账）", expanded=True):
        if ocr_failed:
            st.warning(
                f"共 **{len(ocr_failed)}** 个文件 OCR 未完整识别，已用文件名/上传槽位兜底分类，"
                "请核对关键字段或在下方手工补全。"
            )
            for item in ocr_failed:
                err = item.get("error") or "OCR 服务暂时不可用（已降级）"
                st.caption(f"• {item.get('file_name', '?')}：{err[:160]}")
        if ledger_unmatched:
            msg = ledger_invoice.get("ledger_match_message") or "未找到对应业务编号"
            st.warning(
                f"序时账未自动匹配发票：{msg}。"
                "请在「序时账人工匹配」中选择对应行，或检查业务编号是否一致。"
            )


def _clear_workflow_manual_widgets() -> None:
    """重新处理时清除手工补全控件缓存，避免旧入账日期残留。"""
    for key in list(st.session_state.keys()):
        if key.startswith("wf_manual_"):
            st.session_state.pop(key, None)
    st.session_state.pop("workflow_receipt_pick", None)


def _reset_manual_widgets_from_docs(
    merged: dict[str, dict[str, Any]],
    classified: list[dict[str, Any]],
) -> None:
    """按当前批次 OCR/匹配结果强制覆盖手工补全控件（解决 Streamlit key 缓存旧值）。"""
    c_fields = ((merged.get("contract") or {}).get("fields") or {})
    o_fields = ((merged.get("order") or {}).get("fields") or {})
    r_fields = ((merged.get("receipt") or {}).get("fields") or {})
    i_fields = ((merged.get("invoice") or {}).get("fields") or {})

    payment = c_fields.get("paymentTerms") or o_fields.get("paymentTerms") or ""
    supplier = o_fields.get("supplierName") or i_fields.get("supplierName") or ""
    receipt_date = _pick_receipt_date(r_fields) or ""
    order_amt = _safe_float(o_fields.get("totalAmount"), 0.0)
    inv_amt = _safe_float(i_fields.get("totalAmount"), order_amt)

    inv_ledger = next((x for x in classified if x.get("doc_type") == "invoice"), None)
    posting = ""
    if inv_ledger and inv_ledger.get("ledger_match_ok") and inv_ledger.get(
        "ledger_posting_date"
    ):
        posting = str(inv_ledger["ledger_posting_date"])
    elif _field_filled(i_fields.get("postingDate")):
        posting = str(i_fields["postingDate"])

    st.session_state["wf_manual_payment"] = str(payment or "")
    st.session_state["wf_manual_supplier"] = str(supplier or "")
    st.session_state["wf_manual_receipt_date"] = str(receipt_date or "")
    st.session_state["wf_manual_order_amount"] = float(order_amt)
    st.session_state["wf_manual_invoice_amount"] = float(inv_amt)
    st.session_state["wf_manual_posting"] = str(posting or "")
    st.session_state.pop("workflow_receipt_pick", None)


def _strip_invoice_ocr_posting(classified: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """上传序时账时，入账日期仅以序时账为准，清除 OCR 误填的开票日/过账日。"""
    for item in classified:
        if item.get("doc_type") != "invoice":
            continue
        fields = dict(item.get("fields") or {})
        fields.pop("postingDate", None)
        item["fields"] = fields
    return classified


def _render_ledger_match_banner(classified: list[dict[str, Any]]) -> None:
    """展示序时账匹配摘要（入账日期 + 命中的业务编号）。"""
    if not _has_ledger_df():
        return
    invoice = next((x for x in classified if x.get("doc_type") == "invoice"), None)
    if not invoice or not invoice.get("ledger_evaluated"):
        st.warning("已上传序时账，但尚未对发票执行匹配。请点击 **开始处理**。")
        return
    if invoice.get("ledger_match_ok"):
        biz = invoice.get("ledger_matched_biz_id") or "—"
        query = invoice.get("ledger_query_biz_id")
        date = invoice.get("ledger_posting_date") or "—"
        manual = "（人工指定）" if invoice.get("ledger_match_manual") else ""
        if query and query != biz:
            st.success(
                f"**序时账已匹配**{manual}：业务编号 **{biz}**（查询键 {query}），入账日期 **{date}**"
            )
        else:
            st.success(
                f"**序时账已匹配**{manual}：业务编号 **{biz}**，入账日期 **{date}**"
            )
    else:
        msg = invoice.get("ledger_match_message") or "未在序时账中找到对应业务编号"
        st.warning(f"**序时账未匹配**：{msg}")


def _format_ledger_posting_cell(item: dict[str, Any]) -> str:
    """序时账匹配列：展示入账日期 + 命中的序时账业务编号。"""
    if not item.get("ledger_evaluated"):
        return "-"
    if item.get("ledger_match_ok") and item.get("ledger_posting_date"):
        manual = "（人工）" if item.get("ledger_match_manual") else ""
        biz = item.get("ledger_matched_biz_id") or "—"
        query = item.get("ledger_query_biz_id")
        date = item["ledger_posting_date"]
        if query and query != biz:
            return f"{date} | 序时账 {biz} ← {query}{manual}"
        return f"{date} | 序时账 {biz}{manual}"
    msg = item.get("ledger_match_message")
    if msg:
        return msg
    if item.get("doc_type") in {"invoice", "order"}:
        return "未匹配"
    return "-"


_LEDGER_MATCH_KEYS = (
    "ledger_evaluated",
    "ledger_match_ok",
    "ledger_match_manual",
    "ledger_posting_date",
    "ledger_matched_biz_id",
    "ledger_query_biz_id",
    "ledger_match_message",
)

_LEDGER_WIDGET_KEYS = (
    "wf_ledger_map_posting",
    "wf_ledger_map_biz",
    "wf_ledger_map_amount",
    "wf_ledger_force_manual",
)


def _clear_ledger_match_fields(
    classified: list[dict[str, Any]],
    *,
    clear_invoice_posting: bool = True,
) -> list[dict[str, Any]]:
    """清除分类结果上的序时账匹配痕迹（换账/清账时用）。"""
    updated: list[dict[str, Any]] = []
    for item in classified:
        row = dict(item)
        for key in _LEDGER_MATCH_KEYS:
            row.pop(key, None)
        if clear_invoice_posting and row.get("doc_type") == "invoice":
            fields = dict(row.get("fields") or {})
            fields.pop("postingDate", None)
            row["fields"] = fields
        updated.append(row)
    return updated


def _clear_workflow_ledger_session() -> None:
    """清空 session 中的序时账数据与映射控件。"""
    for key in (
        "workflow_ledger_df",
        "workflow_ledger_suggested",
        "workflow_ledger_standard_map",
        "workflow_ledger_auto_ok",
        "workflow_ledger_mapping",
        "workflow_ledger_index",
        "_workflow_ledger_file_id",
    ):
        st.session_state.pop(key, None)
    for key in list(st.session_state.keys()):
        if key.startswith("wf_manual_ledger_"):
            st.session_state.pop(key, None)
    for key in _LEDGER_WIDGET_KEYS:
        st.session_state.pop(key, None)


def _refresh_classified_ledger_match() -> None:
    """用当前序时账（或清空状态）刷新已有分类结果中的匹配列。"""
    classified = st.session_state.get("workflow_classified")
    if not classified:
        return
    if _has_ledger_df() and st.session_state.get("workflow_ledger_mapping"):
        classified = _strip_invoice_ocr_posting(list(classified))
        classified = _apply_workflow_ledger(classified)
    else:
        classified = _clear_ledger_match_fields(list(classified))
    merged = _merge_same_type_docs(classified)
    hints = _build_missing_hints(classified, merged)
    st.session_state["workflow_classified"] = classified
    st.session_state["workflow_merged"] = merged
    st.session_state["workflow_hints"] = hints
    st.session_state.pop("workflow_result", None)
    st.session_state["_ledger_sync_pending"] = True
    _sync_manual_widgets_from_ledger(classified, force=True)


def _load_workflow_ledger_to_session(ledger_upload: Any) -> bool:
    """加载序时账到 session。若文件内容变化返回 True。"""
    from src.legacy_ocr.ledger_parser import load_ledger_file, resolve_ledger_column_mapping

    raw = ledger_upload.getvalue()
    file_id = (
        str(ledger_upload.name),
        int(ledger_upload.size),
        hashlib.md5(raw).hexdigest(),
    )
    if st.session_state.get("_workflow_ledger_file_id") == file_id:
        return False
    df = load_ledger_file(raw, filename=ledger_upload.name)
    columns = list(df.columns)
    ledger_map, standard_map, auto_ok = resolve_ledger_column_mapping(columns)
    for key in _LEDGER_WIDGET_KEYS:
        st.session_state.pop(key, None)
    for key in list(st.session_state.keys()):
        if key.startswith("wf_manual_ledger_"):
            st.session_state.pop(key, None)
    st.session_state["workflow_ledger_df"] = df
    st.session_state["workflow_ledger_suggested"] = ledger_map
    st.session_state["workflow_ledger_standard_map"] = standard_map
    st.session_state["workflow_ledger_auto_ok"] = auto_ok
    st.session_state["_workflow_ledger_file_id"] = file_id
    if auto_ok:
        st.session_state["workflow_ledger_mapping"] = ledger_map
    else:
        st.session_state.pop("workflow_ledger_mapping", None)
    return True


def _save_ledger_mapping_to_cache(
    posting_col: str,
    biz_col: Optional[str],
    amount_col: Optional[str],
) -> None:
    from src.utils.column_mapper import remember_column_mapping

    remember_column_mapping(posting_col, "入账日期")
    if biz_col:
        remember_column_mapping(biz_col, "业务编号")
    if amount_col:
        remember_column_mapping(amount_col, "金额")


def _render_ledger_mapping_ui() -> Optional[dict[str, Optional[str]]]:
    from src.legacy_ocr.ledger_parser import preview_rows

    df = st.session_state.get("workflow_ledger_df")
    if df is None:
        return None
    columns = list(df.columns)
    if not columns:
        st.warning("序时账文件无有效列。")
        return None

    suggested = st.session_state.get("workflow_ledger_suggested") or {}
    standard_map = st.session_state.get("workflow_ledger_standard_map") or {}
    auto_ok = bool(st.session_state.get("workflow_ledger_auto_ok"))

    if auto_ok and st.session_state.get("workflow_ledger_mapping"):
        st.success(
            "列映射已自动识别（智能匹配）："
            f"业务编号→{standard_map.get('业务编号', '—')}，"
            f"入账日期→{standard_map.get('入账日期', '—')}，"
            f"金额→{standard_map.get('金额', '—')}"
        )
        st.caption("序时账前 5 行预览")
        st.dataframe(preview_rows(df), width="stretch", hide_index=True)
        if not st.checkbox("手动调整列映射", key="wf_ledger_force_manual"):
            return st.session_state["workflow_ledger_mapping"]

    st.markdown("##### 序时账列映射")
    if not auto_ok:
        st.warning("未能全自动识别列映射，请手动确认各列对应关系（选择后将记住供下次使用）。")
    else:
        st.caption("如需调整自动识别结果，请在下方修改（修改后将写入本地缓存）。")

    c1, c2, c3 = st.columns(3)

    def _idx(col: Optional[str], fallback: int = 0) -> int:
        if col and col in columns:
            return columns.index(col)
        return min(fallback, len(columns) - 1)

    with c1:
        posting_col = st.selectbox(
            "入账日期列",
            columns,
            index=_idx(suggested.get("posting_date")),
            key="wf_ledger_map_posting",
        )
    with c2:
        biz_col = st.selectbox(
            "业务编号列",
            ["（不使用）", *columns],
            index=(
                0
                if not suggested.get("biz_id")
                else columns.index(suggested["biz_id"]) + 1
            ),
            key="wf_ledger_map_biz",
        )
    with c3:
        amount_col = st.selectbox(
            "金额列",
            ["（不使用）", *columns],
            index=(
                0
                if not suggested.get("amount")
                else columns.index(suggested["amount"]) + 1
            ),
            key="wf_ledger_map_amount",
        )

    st.caption("序时账前 5 行预览")
    st.dataframe(preview_rows(df), width="stretch", hide_index=True)

    mapping = {
        "posting_date": posting_col,
        "biz_id": None if biz_col == "（不使用）" else biz_col,
        "amount": None if amount_col == "（不使用）" else amount_col,
    }
    _save_ledger_mapping_to_cache(
        posting_col,
        mapping["biz_id"],
        mapping["amount"],
    )
    st.session_state["workflow_ledger_mapping"] = mapping
    return mapping


def _apply_workflow_ledger(classified: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from src.legacy_ocr.ledger_parser import (
        apply_ledger_to_classified,
        build_ledger_index,
        collect_workflow_biz_keys,
    )

    df = st.session_state.get("workflow_ledger_df")
    mapping = st.session_state.get("workflow_ledger_mapping")
    if df is None or not mapping:
        for item in classified:
            item["ledger_evaluated"] = False
        return classified
    try:
        index = build_ledger_index(df, mapping)
    except Exception as exc:  # noqa: BLE001
        st.error(f"序时账解析失败：{exc}")
        for item in classified:
            item["ledger_evaluated"] = False
        return classified
    st.session_state["workflow_ledger_index"] = index
    workflow_biz_keys = collect_workflow_biz_keys(classified)
    updated = apply_ledger_to_classified(
        classified,
        index,
        order_biz_keys=workflow_biz_keys,
    )
    for item in updated:
        item["ledger_evaluated"] = True
    return updated


def _render_manual_ledger_match(classified: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """序时账人工匹配：为未自动匹配的发票/订单指定序时账行。"""
    from src.legacy_ocr.ledger_parser import list_ledger_row_options

    df = st.session_state.get("workflow_ledger_df")
    mapping = st.session_state.get("workflow_ledger_mapping")
    if df is None or not mapping:
        return classified

    options = list_ledger_row_options(df, mapping)
    if not options:
        return classified

    unmatched = [
        (i, item)
        for i, item in enumerate(classified)
        if item.get("ledger_evaluated")
        and not item.get("ledger_match_ok")
        and item.get("doc_type") in {"invoice", "order"}
    ]
    if not unmatched:
        return classified

    st.markdown("##### 序时账人工匹配")
    st.caption("自动匹配失败时，可手动选择序时账行以填充入账日期（主要影响发票截止性）。")
    labels = ["（不指定）"] + [opt["label"] for opt in options]
    changed = False
    updated = list(classified)

    for idx, item in unmatched:
        choice = st.selectbox(
            f"{item['file_name']} → 选择序时账行",
            labels,
            key=f"wf_manual_ledger_{idx}_{item['file_name']}",
        )
        if choice == "（不指定）":
            continue
        opt = next(o for o in options if o["label"] == choice)
        row = dict(updated[idx])
        fields = dict(row.get("fields") or {})
        row["ledger_posting_date"] = opt["posting_date"]
        row["ledger_match_ok"] = True
        row["ledger_match_manual"] = True
        row["ledger_match_message"] = None
        row["ledger_matched_biz_id"] = opt.get("biz_id")
        row["ledger_query_biz_id"] = opt.get("biz_id")
        row["ledger_match_message"] = (
            f"已匹配序时账业务 {opt.get('biz_id') or '—'}（人工选择）"
        )
        if row.get("doc_type") == "invoice":
            fields["postingDate"] = opt["posting_date"]
            row["fields"] = fields
        updated[idx] = row
        changed = True

    if changed:
        st.session_state["workflow_classified"] = updated
        st.session_state["workflow_merged"] = _merge_same_type_docs(updated)
        st.session_state["workflow_hints"] = _build_missing_hints(
            updated, st.session_state["workflow_merged"]
        )
        _sync_manual_widgets_from_ledger(updated, force=True)
        st.session_state["_ledger_sync_pending"] = True
    return updated if changed else classified


def _find_latest_receipt_index(classified: list[dict[str, Any]]) -> Optional[int]:
    """多入库单时返回签收日期最晚的一条在 classified 中的索引。"""
    best_idx: Optional[int] = None
    best_dt: Optional[date] = None
    for i, item in enumerate(classified):
        if item.get("doc_type") != "receipt":
            continue
        rd = _pick_receipt_date(item.get("fields") or {})
        if not rd:
            continue
        try:
            parsed = date.fromisoformat(rd)
        except ValueError:
            continue
        if best_dt is None or parsed > best_dt:
            best_dt = parsed
            best_idx = i
    return best_idx


def _render_classification_overrides(
    classified: list[dict[str, Any]],
    *,
    show_ledger_column: bool = False,
) -> list[dict[str, Any]]:
    """展示分类结果并允许用户下拉修正类型。"""
    type_options = ["contract", "order", "receipt", "invoice", "other"]
    type_labels = [DOC_TYPE_LABELS[t] for t in type_options]
    label_to_type = dict(zip(type_labels, type_options))

    adapter = None
    updated_items: list[dict[str, Any]] = []
    changed = False

    for idx, item in enumerate(classified):
        doc_type = item["doc_type"]
        default_label = DOC_TYPE_LABELS.get(doc_type, "其他")
        if show_ledger_column:
            col_file, col_type, col_fields, col_ledger, col_status = st.columns(
                [2.5, 1.1, 2.2, 1.3, 1.2]
            )
        else:
            col_file, col_type, col_fields, col_status = st.columns([3, 1.2, 2.5, 1.3])
            col_ledger = None
        with col_file:
            st.caption(item["file_name"])
        with col_type:
            try:
                type_index = type_labels.index(default_label)
            except ValueError:
                type_index = type_labels.index("其他")
            selected_label = st.selectbox(
                "识别类型",
                type_labels,
                index=type_index,
                key=f"workflow_doc_type_{idx}_{item['file_name']}",
                label_visibility="collapsed",
            )
        new_type = label_to_type[selected_label]
        if new_type != doc_type:
            if adapter is None:
                adapter = _create_ocr_adapter()
            item = _apply_doc_type_override(item, new_type, adapter)
            changed = True
        with col_fields:
            st.caption(_format_key_fields(item["doc_type"], item.get("fields") or {}))
        if col_ledger is not None:
            with col_ledger:
                st.caption(_format_ledger_posting_cell(item))
        with col_status:
            ocr_note = _format_ocr_status(item)
            if item.get("error") or item.get("ocr_source") == "ocr_failed":
                st.caption(f"⚠️ {ocr_note}")
            elif show_ledger_column and item.get("ledger_evaluated"):
                if item.get("ledger_match_ok"):
                    st.caption("✅")
                elif item["doc_type"] == "invoice":
                    st.caption("⚠️")
                elif _doc_status_ok(item["doc_type"], item.get("fields") or {}):
                    st.caption("✅")
                else:
                    st.caption("⚠️ 字段不完整")
            elif item.get("ocr_source") == "mock":
                st.caption(f"{ocr_note}")
            elif item["doc_type"] == "other":
                st.caption("⚠️ 待分类")
            elif _doc_status_ok(item["doc_type"], item.get("fields") or {}):
                st.caption("✅")
            else:
                st.caption("⚠️ 字段不完整")
        updated_items.append(item)

    if changed:
        st.session_state["workflow_classified"] = updated_items
        merged = _merge_same_type_docs(updated_items)
        st.session_state["workflow_merged"] = merged
        st.session_state["workflow_hints"] = _build_missing_hints(updated_items, merged)

    return updated_items


def _create_ocr_adapter() -> Any:
    """创建 OCR 适配器；已配置千帆时禁止静默 Mock 降级。"""
    from src.legacy_ocr import LegacyOcrAdapter

    load_dotenv(ROOT / ".env", override=True)
    adapter = LegacyOcrAdapter()
    if adapter.is_api_configured():
        adapter.use_mock_when_unavailable = False
    return adapter


def _ocr_status_banner() -> None:
    """展示千帆 OCR 配置状态。"""
    load_dotenv(ROOT / ".env", override=True)
    from src.legacy_ocr import LegacyOcrAdapter

    adapter = LegacyOcrAdapter()
    if adapter.is_api_configured():
        st.success("千帆 OCR 已配置：完整工作流将调用真实 PaddleOCR（非 Mock）")
    else:
        st.warning(
            "千帆 OCR 未配置或仍为占位符，完整工作流将降级 Mock。"
            "请在项目根目录 `.env` 填入 QIANFAN_API_KEY 后重启调试台。"
        )


def _format_ocr_status(item: dict[str, Any]) -> str:
    source = str(item.get("ocr_source") or "unknown")
    if source == "paddleocr":
        return "OCR: 千帆"
    if source == "ocr_failed":
        return "OCR: 降级"
    if source == "mock":
        return "OCR: Mock ⚠️"
    return f"OCR: {source}"


def _field_filled(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    return bool(text) and text.lower() not in {"none", "null", "nan", "-"}


def _completeness_score(doc_type: str, fields: dict[str, Any]) -> int:
    keys = _COMPLETENESS_KEYS.get(doc_type, ())
    return sum(1 for k in keys if _field_filled(fields.get(k)))


def _pick_receipt_date(fields: dict[str, Any]) -> Optional[str]:
    return pick_receipt_date_from_fields(fields)


def _pick_posting_date(fields: dict[str, Any]) -> Optional[str]:
    for key in ("postingDate", "documentDate"):
        val = fields.get(key)
        if _field_filled(val):
            # 发票 documentDate 多为开票日；仅当 postingDate 缺失时降级使用
            if key == "documentDate" and _field_filled(fields.get("postingDate")):
                continue
            if key == "postingDate" or fields.get("postingDate") is None:
                if key == "postingDate":
                    return str(val).strip()
    if _field_filled(fields.get("postingDate")):
        return str(fields["postingDate"]).strip()
    return None


def _pick_payment_terms(*field_dicts: dict[str, Any]) -> Optional[str]:
    for fields in field_dicts:
        val = fields.get("paymentTerms")
        if _field_filled(val):
            return str(val).strip()
        # 从任意文本字段兜底
        for key in ("remarks", "rawText"):
            text = fields.get(key)
            if not text:
                continue
            m = re.search(r"(签收后\s*\d+\s*[日天]|验收后\s*\d+\s*[日天]|票到\s*\d+\s*[日天])", str(text))
            if m:
                return m.group(1).replace(" ", "")
    return None


def _format_key_fields(doc_type: str, fields: dict[str, Any]) -> str:
    parts: list[str] = []
    if doc_type == "contract":
        terms = fields.get("paymentTerms")
        if _field_filled(terms):
            parts.append(f"账期：{terms}")
        cid = fields.get("contractNo") or fields.get("documentNo")
        if _field_filled(cid):
            parts.append(f"合同号：{cid}")
    elif doc_type == "order":
        amt = fields.get("totalAmount") or fields.get("amount")
        if _field_filled(amt):
            parts.append(f"金额：{amt}")
        if _field_filled(fields.get("supplierName")):
            parts.append(f"供应商：{fields.get('supplierName')}")
    elif doc_type == "receipt":
        rd = _pick_receipt_date(fields)
        arrival = fields.get("deliveryDate")
        if rd:
            source = fields.get("_receiptDateSource")
            if _field_filled(arrival) and str(arrival).strip() != str(rd).strip():
                parts.append(f"到货日：{arrival}")
                parts.append(f"签收日（验收完成）：{rd}")
            elif source == "acceptance_completion":
                parts.append(f"签收日（验收完成）：{rd}")
            else:
                parts.append(f"签收日：{rd}")
        if _field_filled(fields.get("totalAmount")):
            parts.append(f"金额：{fields.get('totalAmount')}")
    elif doc_type == "invoice":
        pd = fields.get("postingDate")
        if _field_filled(pd):
            parts.append(f"入账日：{pd}")
        amt = fields.get("totalAmount") or fields.get("amount")
        if _field_filled(amt):
            parts.append(f"金额：{amt}")
    return "，".join(parts) if parts else "-"


def _doc_status_ok(doc_type: str, fields: dict[str, Any]) -> bool:
    if doc_type == "contract":
        terms = str(fields.get("paymentTerms") or "")
        return bool(extract_days_from_description(terms)) or bool(
            re.search(r"签收后\s*\d+\s*[日天]|验收后\s*\d+\s*[日天]|票到\s*\d+\s*[日天]", terms)
        )
    if doc_type == "order":
        return _field_filled(fields.get("totalAmount")) and _field_filled(
            fields.get("supplierName")
        )
    if doc_type == "receipt":
        return bool(_pick_receipt_date(fields))
    if doc_type == "invoice":
        return _field_filled(fields.get("postingDate")) and _field_filled(
            fields.get("totalAmount")
        )
    return False


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(str(value).replace(",", "").replace("¥", "").replace("￥", "").strip())
    except (TypeError, ValueError):
        return default


def _merge_same_type_docs(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """同类型合并：取关键字段最完整的一条。"""
    best: dict[str, dict[str, Any]] = {}
    for item in items:
        doc_type = item["doc_type"]
        if doc_type == "other":
            continue
        score = _completeness_score(doc_type, item.get("fields") or {})
        prev = best.get(doc_type)
        if prev is None or score > prev["score"]:
            best[doc_type] = {**item, "score": score}
    return best


def _build_missing_hints(
    classified: list[dict[str, Any]],
    merged: dict[str, dict[str, Any]],
) -> list[str]:
    hints: list[str] = []
    type_counts: dict[str, int] = {}
    for item in classified:
        type_counts[item["doc_type"]] = type_counts.get(item["doc_type"], 0) + 1

    if "contract" not in merged:
        hints.append(
            "未识别到合同文件，请确认是否已上传合同。如已上传，请检查文件名是否包含「合同」字样。"
        )
    else:
        c_fields = merged["contract"].get("fields") or {}
        if not _field_filled(c_fields.get("paymentTerms")):
            hints.append(
                "合同中未提取到付款账期条款（如「签收后X日」「开票后30日」）；"
                "截止性测试不依赖账期，但建议补录以便后续收款测试。"
            )

    if "order" not in merged:
        hints.append(
            "未识别到订单文件，请确认是否已上传订单。如已上传，请检查文件名是否包含「订单」或「PO」字样。"
        )
    else:
        o_fields = merged["order"].get("fields") or {}
        if not _field_filled(o_fields.get("totalAmount")):
            hints.append("订单中未提取到金额，请手动补充。")
        if not _field_filled(o_fields.get("supplierName")):
            hints.append("订单中未提取到供应商，请手动补充。")

    receipt_count = type_counts.get("receipt", 0)
    if receipt_count == 0:
        hints.append(
            "未识别到入库单/签收单，请确认是否已上传。如已上传，请检查文件名是否包含「入库」或「签收」字样。"
        )
    elif receipt_count >= 2:
        hints.append(
            f"识别到{receipt_count}个入库单，已默认选用签收日期最晚的一条；可在下方下拉框更改。"
        )
    else:
        r_fields = (merged.get("receipt") or {}).get("fields") or {}
        if not _pick_receipt_date(r_fields):
            hints.append("入库单中未提取到签收日期，请手动补充。")

    if "invoice" not in merged:
        hints.append(
            "未识别到发票文件，请确认是否已上传发票。如已上传，请检查文件名是否包含「发票」字样。"
        )
    else:
        i_fields = merged["invoice"].get("fields") or {}
        if not _field_filled(i_fields.get("postingDate")):
            hints.append("发票中未提取到入账日期，请手动补充。")
        if not _field_filled(i_fields.get("totalAmount")):
            hints.append("发票中未提取到金额，请手动补充。")

    return hints


def _save_upload_to_temp(upload: Any, folder: Path) -> Path:
    safe_name = Path(upload.name).name
    target = folder / safe_name
    # 避免重名覆盖
    if target.exists():
        stem, suffix = target.stem, target.suffix
        i = 1
        while True:
            candidate = folder / f"{stem}_{i}{suffix}"
            if not candidate.exists():
                target = candidate
                break
            i += 1
    target.write_bytes(upload.getvalue())
    return target


def _serialize_workflow_result(raw: dict[str, Any]) -> dict[str, Any]:
    data = dict(raw)
    match = data.get("match_result")
    if hasattr(match, "model_dump"):
        data["match_result"] = match.model_dump()
    req = data.get("match_request")
    if hasattr(req, "model_dump"):
        data["match_request"] = req.model_dump()
    cutoff = data.get("cutoff_result")
    if cutoff is not None and hasattr(cutoff, "model_dump"):
        data["cutoff_result"] = cutoff.model_dump()
    return data


def _assemble_three_way_request(
    merged: dict[str, dict[str, Any]],
    *,
    selected_receipt_idx: Optional[int] = None,
    classified: Optional[list[dict[str, Any]]] = None,
    manual: Optional[dict[str, Any]] = None,
) -> Any:
    """从合并结果 + 可选手工补全组装 ThreeWayMatchRequest。"""
    manual = manual or {}
    contract_fields = dict((merged.get("contract") or {}).get("fields") or {})
    order_fields = dict((merged.get("order") or {}).get("fields") or {})
    receipt_fields = dict((merged.get("receipt") or {}).get("fields") or {})
    invoice_fields = dict((merged.get("invoice") or {}).get("fields") or {})

    # 多入库单时按选择覆盖
    if (
        selected_receipt_idx is not None
        and classified is not None
        and 0 <= selected_receipt_idx < len(classified)
    ):
        chosen = classified[selected_receipt_idx]
        if chosen.get("doc_type") == "receipt":
            receipt_fields = dict(chosen.get("fields") or {})

    payment = (
        manual.get("payment_terms")
        or _pick_payment_terms(contract_fields, order_fields)
    )
    if payment:
        order_fields["paymentTerms"] = payment
        contract_fields["paymentTerms"] = payment

    contract_no = (
        manual.get("contract_no")
        or contract_fields.get("contractNo")
        or contract_fields.get("documentNo")
        or order_fields.get("contractNo")
    )
    if contract_no:
        order_fields["contractNo"] = contract_no
        contract_fields["contractNo"] = contract_no

    if manual.get("supplier"):
        order_fields["supplierName"] = manual["supplier"]
        receipt_fields.setdefault("supplierName", manual["supplier"])
        invoice_fields.setdefault("supplierName", manual["supplier"])

    if manual.get("order_amount") is not None:
        order_fields["totalAmount"] = manual["order_amount"]
    if manual.get("receipt_date"):
        receipt_fields["deliveryDate"] = manual["receipt_date"]
        receipt_fields["documentDate"] = manual["receipt_date"]
    if manual.get("receipt_amount") is not None:
        receipt_fields["totalAmount"] = manual["receipt_amount"]
    elif manual.get("order_amount") is not None and not _field_filled(
        receipt_fields.get("totalAmount")
    ):
        receipt_fields["totalAmount"] = manual["order_amount"]
    if manual.get("posting_date"):
        invoice_fields["postingDate"] = manual["posting_date"]
    if manual.get("invoice_amount") is not None:
        invoice_fields["totalAmount"] = manual["invoice_amount"]
    elif manual.get("order_amount") is not None and not _field_filled(
        invoice_fields.get("totalAmount")
    ):
        invoice_fields["totalAmount"] = manual["order_amount"]

    # 合同付款账期写入订单字段，截止性不使用，保留供后续收款等测试
    if not order_fields.get("paymentTerms") and contract_fields.get("paymentTerms"):
        order_fields["paymentTerms"] = contract_fields["paymentTerms"]

    return build_request_from_ocr_fields(order_fields, receipt_fields, invoice_fields)


def _api_health() -> bool:
    try:
        r = requests.get(f"{API_BASE}/health", timeout=2)
        return r.status_code == 200
    except requests.RequestException:
        return False


def _post_cutoff(payload: dict[str, Any]) -> tuple[Optional[dict], Optional[str]]:
    try:
        r = requests.post(CUTOFF_URL, json=payload, timeout=30)
        if r.status_code >= 400:
            try:
                detail = r.json()
            except Exception:
                detail = r.text
            return None, f"HTTP {r.status_code}: {detail}"
        return r.json(), None
    except requests.RequestException as exc:
        return None, f"请求失败（请确认已启动 API）：{exc}"


def _status_banner(status: str) -> None:
    if status == "PASS":
        st.success(f"测试状态：**{status}**")
    elif status == "WARNING":
        st.warning(f"测试状态：**{status}**")
    else:
        st.error(f"测试状态：**{status}**")


def _show_single_result(data: dict[str, Any]) -> None:
    _status_banner(str(data.get("测试状态", "")))
    c1, c2, c3 = st.columns(3)
    c1.metric("风险等级", data.get("风险等级") or "-")
    c2.metric("应确认日期", data.get("应确认日期") or "-")
    c3.metric("偏差天数", data.get("偏差天数") if data.get("偏差天数") is not None else "-")
    st.markdown(f"**问题描述：** {data.get('问题描述') or '-'}")
    st.markdown(f"**计算依据：** {data.get('计算依据') or '-'}")
    path = data.get("底稿文件路径")
    if path:
        abs_path = ROOT / path
        st.markdown(f"**底稿文件路径：** `{path}`")
        if abs_path.is_file():
            st.download_button(
                "下载底稿 CSV",
                data=abs_path.read_bytes(),
                file_name=abs_path.name,
                mime="text/csv",
                key=f"dl_single_{data.get('报告ID', path)}",
            )
        else:
            st.caption("文件尚未落盘或路径不可读，可到「查看已生成底稿」刷新。")
    with st.expander("完整 JSON"):
        st.json(data)


def _parse_optional_int(text: str) -> Optional[int]:
    raw = (text or "").strip()
    if not raw:
        return None
    return int(raw)


def _build_payload_from_form(
    biz_id: str,
    contract_id: str,
    customer: str,
    payment_desc: str,
    payment_days_text: str,
    receipt: date,
    entry: date,
    amount: float,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "业务编号": biz_id.strip(),
        "签收日期": receipt.isoformat(),
        "入账日期": entry.isoformat(),
        "入账金额": float(amount),
    }
    if contract_id.strip():
        payload["合同编号"] = contract_id.strip()
    if customer.strip():
        payload["客户名称"] = customer.strip()
    if payment_desc.strip():
        payload["合同账期描述"] = payment_desc.strip()
    days = _parse_optional_int(payment_days_text)
    if days is not None:
        payload["合同账期天数"] = days
    return payload


def _response_to_flat_row(data: dict[str, Any]) -> dict[str, Any]:
    fill = data.get("底稿回填") or {}
    return {
        "报告ID": data.get("报告ID"),
        "业务编号": data.get("业务编号"),
        "测试状态": data.get("测试状态"),
        "风险等级": data.get("风险等级"),
        "应确认日期": data.get("应确认日期"),
        "偏差天数": data.get("偏差天数"),
        "问题描述": data.get("问题描述"),
        "计算依据": data.get("计算依据"),
        "底稿文件路径": data.get("底稿文件路径"),
        "凭证号": fill.get("凭证号"),
        "客户名称": fill.get("客户名称"),
        "合同编号": fill.get("合同编号"),
        "审计结论": fill.get("审计结论"),
    }


def _export_workbook_bytes(responses: list[dict[str, Any]]) -> bytes:
    models = [CutoffResponse.model_validate(item) for item in responses]
    # 写到临时路径再读回，保证与正式生成器一致
    tmp = settings.get_reports_dir() / "_debug_batch_export.csv"
    WorkbookGenerator.generate_from_responses(models, str(tmp))
    data = tmp.read_bytes()
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass
    return data


def render_single_test() -> None:
    st.subheader("🧪 单条截止性测试（API调试）")
    with st.form("single_cutoff_form"):
        biz_id = st.text_input("业务编号", value="SO-DEBUG-001")
        c1, c2 = st.columns(2)
        with c1:
            contract_id = st.text_input("合同编号（可选）", value="")
            payment_desc = st.text_input(
                "合同账期描述（可选，截止性不使用，写入底稿）",
                value="签收后10日",
            )
            receipt = st.date_input("控制权转移日（签收/验收）", value=DEFAULT_RECEIPT)
            amount = st.number_input("入账金额", min_value=0.0, value=500.0, step=100.0)
        with c2:
            customer = st.text_input("客户名称（可选）", value="")
            payment_days_text = st.text_input(
                "合同账期天数（可选，截止性不使用）", value="10"
            )
            entry = st.date_input("入账日期（序时账过账日）", value=DEFAULT_ENTRY)
        submitted = st.form_submit_button("执行测试", type="primary")

    if not submitted:
        return
    if not biz_id.strip():
        st.error("业务编号不能为空")
        return
    try:
        payload = _build_payload_from_form(
            biz_id,
            contract_id,
            customer,
            payment_desc,
            payment_days_text,
            receipt,
            entry,
            amount,
        )
    except ValueError:
        st.error("合同账期天数须为整数")
        return

    with st.spinner("调用 /api/v1/cutoff …"):
        data, err = _post_cutoff(payload)
    if err:
        st.error(err)
        return
    assert data is not None
    _show_single_result(data)


def render_batch_test() -> None:
    st.subheader("📦 批量测试（上传JSONL）")
    st.caption("每行一个 CutoffRequest JSON 对象。")
    uploaded = st.file_uploader("上传 .jsonl 文件", type=["jsonl", "json"])
    if st.button("批量执行", type="primary", disabled=uploaded is None):
        if uploaded is None:
            return
        raw = uploaded.getvalue().decode("utf-8-sig")
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        payloads: list[dict[str, Any]] = []
        parse_errors: list[str] = []
        for i, line in enumerate(lines, start=1):
            try:
                payloads.append(json.loads(line))
            except json.JSONDecodeError as exc:
                parse_errors.append(f"第 {i} 行 JSON 无效: {exc}")
        if parse_errors:
            st.error("\n".join(parse_errors[:5]))
            return

        results: list[dict[str, Any]] = []
        errors: list[str] = []
        progress = st.progress(0.0, text="批量调用中…")
        for idx, payload in enumerate(payloads, start=1):
            data, err = _post_cutoff(payload)
            if err:
                errors.append(f"业务编号={payload.get('业务编号', '?')}: {err}")
            elif data:
                results.append(data)
            progress.progress(idx / max(len(payloads), 1), text=f"{idx}/{len(payloads)}")
        progress.empty()

        st.session_state["batch_results"] = results
        st.session_state["batch_errors"] = errors

    results = st.session_state.get("batch_results") or []
    errors = st.session_state.get("batch_errors") or []
    if not results and not errors:
        return

    total = len(results)
    n_pass = sum(1 for r in results if r.get("测试状态") == "PASS")
    n_warn = sum(1 for r in results if r.get("测试状态") == "WARNING")
    n_fail = sum(1 for r in results if r.get("测试状态") == "FAIL")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("总条数", total)
    m2.metric("PASS", n_pass)
    m3.metric("WARNING", n_warn)
    m4.metric("FAIL", n_fail)

    if errors:
        st.warning(f"{len(errors)} 条调用失败")
        with st.expander("失败详情"):
            for e in errors:
                st.text(e)

    if results:
        df = pd.DataFrame([_response_to_flat_row(r) for r in results])
        st.dataframe(df, width="stretch")
        csv_bytes = _export_workbook_bytes(results)
        st.download_button(
            "导出合并底稿 CSV",
            data=csv_bytes,
            file_name="底稿_批量导出_GOSPD01010.csv",
            mime="text/csv",
            key="dl_batch_workbook",
        )


def render_workbook_viewer() -> None:
    st.subheader("📄 查看已生成底稿")
    reports_dir = Path(settings.get_reports_dir())
    reports_dir.mkdir(parents=True, exist_ok=True)
    csv_files = sorted(reports_dir.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not csv_files:
        st.info("reports/ 下暂无 CSV 文件。先跑单条/批量测试即可生成。")
        return

    labels = [p.name for p in csv_files]
    choice = st.selectbox("选择 CSV 文件", labels)
    path = reports_dir / choice
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        st.error(f"读取失败: {exc}")
        return
    st.caption(f"路径：`reports/{choice}` · 行数 {len(df)}")
    st.dataframe(df, width="stretch")
    st.download_button(
        "下载CSV",
        data=path.read_bytes(),
        file_name=choice,
        mime="text/csv",
        key=f"dl_view_{choice}",
    )


def _post_three_way(payload: dict[str, Any]) -> tuple[Optional[dict], Optional[str]]:
    try:
        r = requests.post(THREE_WAY_URL, json=payload, timeout=60)
        if r.status_code >= 400:
            try:
                detail = r.json()
            except Exception:
                detail = r.text
            return None, f"HTTP {r.status_code}: {detail}"
        return r.json(), None
    except requests.RequestException as exc:
        return None, f"请求失败（请确认已启动 API）：{exc}"


def _show_three_way_result(data: dict[str, Any]) -> None:
    match = data.get("match_result") or {}
    if hasattr(match, "model_dump"):
        match = match.model_dump()
    cutoff = data.get("cutoff_result")
    if cutoff is not None and hasattr(cutoff, "model_dump"):
        cutoff = cutoff.model_dump()
    overall = data.get("overall_status") or "-"
    summary = (
        data.get("human_readable_summary")
        or match.get("human_readable_summary")
        or ""
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("##### 三单匹配结果")
        status = match.get("overall_status") or "-"
        if status == "PASS":
            st.success(f"状态：**{status}**")
        elif status == "WARNING":
            st.warning(f"状态：**{status}**")
        else:
            st.error(f"状态：**{status}**")
        st.metric("匹配得分", match.get("match_score", "-"))
        st.caption(match.get("summary") or "")
        comparisons = match.get("comparisons") or []
        if comparisons:
            st.dataframe(pd.DataFrame(comparisons), width="stretch", hide_index=True)

    with c2:
        st.markdown("##### 截止性测试结果")
        if not data.get("cutoff_available", True) or cutoff is None:
            st.warning("截止性未执行")
            st.caption(
                data.get("cutoff_skipped_reason")
                or data.get("cutoff_error")
                or "无截止性结果"
            )
        else:
            c_status = cutoff.get("测试状态") or "-"
            if c_status == "PASS":
                st.success(f"状态：**{c_status}**")
            elif c_status == "WARNING":
                st.warning(f"状态：**{c_status}**")
            else:
                st.error(f"状态：**{c_status}**")
            st.metric(
                "偏差天数",
                cutoff.get("偏差天数") if cutoff.get("偏差天数") is not None else "-",
            )
            st.markdown(f"**问题描述：** {cutoff.get('问题描述') or '-'}")
            st.caption(cutoff.get("计算依据") or "")

    with c3:
        st.markdown("##### 合并结论")
        if overall == "PASS":
            st.success(f"整体状态：**{overall}**")
        elif overall == "WARNING":
            st.warning(f"整体状态：**{overall}**")
        else:
            st.error(f"整体状态：**{overall}**")
        st.markdown("**人工可读摘要**")
        st.info(summary or "（无摘要）")
        path = data.get("底稿文件路径")
        if path:
            st.caption(f"底稿：`{path}`")

    with st.expander("完整 JSON"):
        st.json(_serialize_workflow_result(data))


def render_full_workflow() -> None:
    """完整工作流：批量上传 → 智能分类 → OCR提取 → 三单匹配+截止性。"""
    st.subheader("🚀 完整工作流（批量上传 · 智能分类）")
    st.caption(
        f"步骤1：按类型上传合同 / 订单 / 入库单 / 发票；可选上传序时账以自动匹配入账日期。"
        f"　版本：{WORKFLOW_UI_VERSION}"
    )

    st.markdown("#### 步骤1 · 上传区")
    u1, u2, u3, u4, u5 = st.columns(5)
    doc_types = ("pdf", "png", "jpg", "jpeg")
    with u1:
        upload_contract = st.file_uploader(
            "1. 合同",
            type=list(doc_types),
            accept_multiple_files=True,
            key="wf_upload_contract",
        )
    with u2:
        upload_order = st.file_uploader(
            "2. 订单",
            type=list(doc_types),
            accept_multiple_files=True,
            key="wf_upload_order",
        )
    with u3:
        upload_receipt = st.file_uploader(
            "3. 入库单/签收单",
            type=list(doc_types),
            accept_multiple_files=True,
            key="wf_upload_receipt",
        )
    with u4:
        upload_invoice = st.file_uploader(
            "4. 发票",
            type=list(doc_types),
            accept_multiple_files=True,
            key="wf_upload_invoice",
        )
    with u5:
        upload_ledger = st.file_uploader(
            "5. 序时账（可选）",
            type=["csv", "xlsx", "xls"],
            accept_multiple_files=False,
            key="wf_upload_ledger",
            help="用于从序时账提取入账日期并匹配到发票；不上传则截止性可能 SKIPPED",
        )

    uploaded_files: list[Any] = []
    upload_slots: list[tuple[Any, str]] = []
    for batch, slot in (
        (upload_contract, "contract"),
        (upload_order, "order"),
        (upload_receipt, "receipt"),
        (upload_invoice, "invoice"),
    ):
        if batch:
            for upload in batch:
                uploaded_files.append(upload)
                upload_slots.append((upload, slot))

    if uploaded_files:
        st.info(f"已选择 **{len(uploaded_files)}** 个单据文件")

    ledger_mapping: Optional[dict[str, Optional[str]]] = None
    if upload_ledger:
        ledger_changed = _load_workflow_ledger_to_session(upload_ledger)
        ledger_mapping = _render_ledger_mapping_ui()
        mapping_sig = (
            ledger_mapping.get("posting_date") if ledger_mapping else None,
            ledger_mapping.get("biz_id") if ledger_mapping else None,
            ledger_mapping.get("amount") if ledger_mapping else None,
        )
        prev_sig = st.session_state.get("_workflow_ledger_mapping_sig")
        mapping_changed = mapping_sig != prev_sig and ledger_mapping is not None
        if ledger_mapping is not None:
            st.session_state["_workflow_ledger_mapping_sig"] = mapping_sig
        if ledger_changed:
            st.info(f"已切换序时账：**{upload_ledger.name}**，正在刷新匹配结果…")
            _refresh_classified_ledger_match()
        elif mapping_changed and st.session_state.get("workflow_classified"):
            _refresh_classified_ledger_match()
    elif st.session_state.get("_workflow_ledger_file_id"):
        # 用户清空了上传控件：同步清空 session，并刷新分类区匹配列
        _clear_workflow_ledger_session()
        st.session_state.pop("_workflow_ledger_mapping_sig", None)
        if st.session_state.get("workflow_classified"):
            _refresh_classified_ledger_match()
            st.info("已移除序时账，分类结果中的入账匹配已清除。")
        else:
            st.info("已移除序时账。")
    elif _has_ledger_df():
        ledger_mapping = _render_ledger_mapping_ui()

    start = st.button(
        "开始处理",
        type="primary",
        disabled=not uploaded_files,
        key="full_workflow_start",
    )

    if start and uploaded_files:
        _clear_workflow_manual_widgets()
        if _has_ledger_df() and not st.session_state.get(
            "workflow_ledger_mapping"
        ):
            from src.legacy_ocr.ledger_parser import resolve_ledger_column_mapping

            cols = list(st.session_state["workflow_ledger_df"].columns)
            ledger_map, _, _auto_ok = resolve_ledger_column_mapping(cols)
            st.session_state["workflow_ledger_mapping"] = ledger_map

        adapter = _create_ocr_adapter()
        classified: list[dict[str, Any]] = []
        progress = st.progress(0.0, text="正在识别文件…")
        tmp_root = Path(tempfile.mkdtemp(prefix="cutoff_workflow_"))

        slot_by_name = {upload.name: slot for upload, slot in upload_slots}
        for idx, upload in enumerate(uploaded_files, start=1):
            slot_hint = slot_by_name.get(upload.name, "")
            progress.progress(
                (idx - 1) / max(len(uploaded_files), 1),
                text=f"处理中 {idx}/{len(uploaded_files)}：{upload.name}",
            )
            try:
                path = _save_upload_to_temp(upload, tmp_root)
                name_type = classify_document(upload.name, "", slot_hint=slot_hint)
                ocr_type = DOC_TYPE_TO_OCR.get(name_type, "other")
                ocr = adapter.recognize_and_extract(
                    str(path), ocr_type, allow_degraded=True
                )
                raw_text = str(ocr.get("rawText") or "")
                ocr_source = str(ocr.get("source") or "unknown")
                ocr_error: Optional[str] = None

                if ocr_source == "mock" and adapter.is_api_configured():
                    ocr_error = "OCR 降级为 Mock（请检查千帆服务状态）"
                elif ocr_source == "ocr_failed":
                    ocr_error = "千帆 OCR 暂时不可用，已用文件名兜底"

                final_type = classify_document(upload.name, raw_text, slot_hint=slot_hint)
                fields = dict(ocr.get("extractedFields") or {})
                if final_type != name_type and final_type != "other" and raw_text:
                    fields = adapter.extract_fields(
                        raw_text, DOC_TYPE_TO_OCR.get(final_type, "other")
                    )
                    fields["documentType"] = DOC_TYPE_TO_OCR.get(final_type, "other")

                fallback = _fallback_fields_from_filename(upload.name, final_type)
                fields = _merge_fields(fields, fallback)

                if final_type == "contract" and not _field_filled(fields.get("paymentTerms")) and raw_text:
                    m = re.search(
                        r"(签收后\s*\d+\s*[日天]|验收后\s*\d+\s*[日天]|票到\s*\d+\s*[日天])",
                        raw_text,
                    )
                    if m:
                        fields["paymentTerms"] = m.group(1).replace(" ", "")

                classified.append(
                    {
                        "file_name": upload.name,
                        "path": str(path),
                        "doc_type": final_type,
                        "upload_slot": slot_hint,
                        "fields": fields,
                        "raw_text": raw_text,
                        "ocr_source": ocr_source,
                        "confidence": ocr.get("confidence"),
                        "error": ocr_error,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                final_type = classify_document(upload.name, "", slot_hint=slot_hint)
                fields = _fallback_fields_from_filename(upload.name, final_type)
                classified.append(
                    {
                        "file_name": upload.name,
                        "path": "",
                        "doc_type": final_type,
                        "upload_slot": slot_hint,
                        "fields": fields,
                        "raw_text": "",
                        "ocr_source": "failed",
                        "error": f"处理异常：{exc}",
                    }
                )
            progress.progress(
                idx / max(len(uploaded_files), 1),
                text=f"完成 {idx}/{len(uploaded_files)}",
            )
        progress.empty()

        if _has_ledger_df():
            classified = _strip_invoice_ocr_posting(classified)
            classified = _apply_workflow_ledger(classified)

        merged = _merge_same_type_docs(classified)
        hints = _build_missing_hints(classified, merged)
        st.session_state["workflow_classified"] = classified
        st.session_state["workflow_merged"] = merged
        st.session_state["workflow_hints"] = hints
        st.session_state.pop("workflow_result", None)
        _reset_manual_widgets_from_docs(merged, classified)
        st.session_state["_ledger_sync_pending"] = True
        _sync_manual_widgets_from_ledger(classified, force=True)

    classified = st.session_state.get("workflow_classified") or []
    merged = st.session_state.get("workflow_merged") or {}
    hints = st.session_state.get("workflow_hints") or []

    if not classified:
        return

    show_ledger = bool(
        _has_ledger_df()
        or any(item.get("ledger_evaluated") for item in classified)
    )
    st.markdown("#### 分类结果")
    st.caption("识别有误可在「识别类型」下拉框中手动修正，系统将按新类型重新抽取字段。")
    if show_ledger:
        hdr1, hdr2, hdr3, hdr4, hdr5 = st.columns([2.5, 1.1, 2.2, 1.3, 1.2])
        with hdr1:
            st.markdown("**文件**")
        with hdr2:
            st.markdown("**识别类型**")
        with hdr3:
            st.markdown("**关键字段**")
        with hdr4:
            st.markdown("**序时账匹配**")
        with hdr5:
            st.markdown("**状态**")
    else:
        hdr1, hdr2, hdr3, hdr4 = st.columns([3, 1.2, 2.5, 1.3])
        with hdr1:
            st.markdown("**文件**")
        with hdr2:
            st.markdown("**识别类型**")
        with hdr3:
            st.markdown("**关键字段**")
        with hdr4:
            st.markdown("**状态**")
    classified = _render_classification_overrides(
        classified, show_ledger_column=show_ledger
    )
    classified = _render_manual_ledger_match(classified)
    merged = st.session_state.get("workflow_merged") or _merge_same_type_docs(classified)
    hints = st.session_state.get("workflow_hints") or _build_missing_hints(classified, merged)
    _sync_manual_widgets_from_ledger(classified)
    _render_workflow_issue_panel(classified)
    _render_ledger_match_banner(classified)

    if hints:
        st.markdown("#### 缺失数据提示")
        for hint in hints:
            st.warning(hint)

    receipt_items = [x for x in classified if x["doc_type"] == "receipt"]
    selected_receipt_idx: Optional[int] = None
    if len(receipt_items) >= 2:
        labels: list[str] = []
        index_map: list[int] = []
        for i, item in enumerate(classified):
            if item["doc_type"] != "receipt":
                continue
            rd = _pick_receipt_date(item.get("fields") or {}) or "无签收日"
            labels.append(f"{item['file_name']}（签收日：{rd}）")
            index_map.append(i)
        auto_idx = _find_latest_receipt_index(classified)
        default_pos = 0
        if auto_idx is not None and auto_idx in index_map:
            default_pos = index_map.index(auto_idx)
            auto_item = classified[auto_idx]
            auto_date = _pick_receipt_date(auto_item.get("fields") or {}) or "未知"
            st.info(
                f"自动选择【{auto_item['file_name']}】作为签收日期（签收日：{auto_date}）"
            )
            st.caption("如需更改，请在下拉框中重新选择")
        choice = st.selectbox(
            "签收日期来源（入库单/签收单）",
            labels,
            index=default_pos,
            key="workflow_receipt_pick",
        )
        selected_receipt_idx = index_map[labels.index(choice)]
    elif len(receipt_items) == 1:
        for i, item in enumerate(classified):
            if item["doc_type"] == "receipt":
                selected_receipt_idx = i
                break

    st.markdown("#### 手工补全（可选）")
    with st.expander("关键字段缺失时可在此补充后继续", expanded=bool(hints)):
        # 若本轮尚未写入 session（例如仅改类型未点开始处理），用当前 OCR 结果补默认值
        if "wf_manual_payment" not in st.session_state:
            _reset_manual_widgets_from_docs(merged, classified)

        m1, m2 = st.columns(2)
        with m1:
            manual_payment = st.text_input(
                "合同账期描述（截止性不使用，供后续收款测试）",
                key="wf_manual_payment",
            )
            manual_supplier = st.text_input(
                "供应商",
                key="wf_manual_supplier",
            )
            manual_receipt_date = st.text_input(
                "签收日期 (YYYY-MM-DD)",
                key="wf_manual_receipt_date",
            )
        with m2:
            manual_order_amount = st.number_input(
                "订单金额（万元）",
                min_value=0.0,
                step=1.0,
                key="wf_manual_order_amount",
            )
            manual_posting = st.text_input(
                "入账日期 (YYYY-MM-DD)",
                key="wf_manual_posting",
                help="由序时账自动匹配；未匹配时请手工填写",
            )
            manual_invoice_amount = st.number_input(
                "发票金额（万元）",
                min_value=0.0,
                step=1.0,
                key="wf_manual_invoice_amount",
            )

    required_ready = all(k in merged for k in ("order", "receipt", "invoice"))
    # 允许仅靠手工补全推进：只要三类主单据有识别或用户填了关键日期/金额
    can_run = required_ready or (
        bool(manual_receipt_date.strip())
        and bool(manual_posting.strip())
        and manual_order_amount > 0
    )

    if not can_run:
        st.error("数据尚未齐备：至少需要识别到订单、入库单、发票（或手工补全关键日期与金额）。")
        return

    if st.button("数据齐备，执行三单匹配 + 截止性", type="primary", key="wf_run_match"):
        manual = {
            "payment_terms": manual_payment.strip() or None,
            "supplier": manual_supplier.strip() or None,
            "receipt_date": manual_receipt_date.strip() or None,
            "posting_date": manual_posting.strip() or None,
            "order_amount": manual_order_amount if manual_order_amount > 0 else None,
            "receipt_amount": manual_order_amount if manual_order_amount > 0 else None,
            "invoice_amount": manual_invoice_amount if manual_invoice_amount > 0 else None,
        }
        # 若无订单识别结果，用手工字段构造最小 order fields
        if "order" not in merged:
            merged = {
                **merged,
                "order": {
                    "file_name": "(手工)",
                    "doc_type": "order",
                    "fields": {
                        "documentNo": "PO-MANUAL",
                        "supplierName": manual["supplier"] or "未知供应商",
                        "totalAmount": manual["order_amount"] or 0,
                        "quantity": 1,
                        "paymentTerms": manual["payment_terms"],
                    },
                    "score": 0,
                },
            }
        if "receipt" not in merged:
            merged = {
                **merged,
                "receipt": {
                    "file_name": "(手工)",
                    "doc_type": "receipt",
                    "fields": {
                        "documentNo": "WR-MANUAL",
                        "documentDate": manual["receipt_date"],
                        "deliveryDate": manual["receipt_date"],
                        "totalAmount": manual["order_amount"] or 0,
                        "quantity": 1,
                        "supplierName": manual["supplier"] or "未知供应商",
                    },
                    "score": 0,
                },
            }
        if "invoice" not in merged:
            merged = {
                **merged,
                "invoice": {
                    "file_name": "(手工)",
                    "doc_type": "invoice",
                    "fields": {
                        "documentNo": "INV-MANUAL",
                        "invoiceNo": "INV-MANUAL",
                        "postingDate": manual["posting_date"],
                        "totalAmount": manual["invoice_amount"] or manual["order_amount"] or 0,
                        "quantity": 1,
                        "supplierName": manual["supplier"] or "未知供应商",
                    },
                    "score": 0,
                },
            }

        try:
            request = _assemble_three_way_request(
                merged,
                selected_receipt_idx=selected_receipt_idx,
                classified=classified,
                manual=manual,
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"组装三单请求失败：{exc}")
            return

        with st.spinner("执行 ThreeWayMatcher.match_and_cutoff …"):
            try:
                matcher = ThreeWayMatcher()
                raw = matcher.match_and_cutoff(request, inprocess=True)
                result = _serialize_workflow_result(raw)
                st.session_state["workflow_result"] = result
            except Exception as exc:  # noqa: BLE001
                st.error(f"执行失败：{exc}")
                return

    result = st.session_state.get("workflow_result")
    if result:
        st.markdown("#### 执行结果")
        # 展示从各文件提取的关键输入摘要
        with st.expander("组装输入摘要（实际参与匹配，单位：万元）", expanded=False):
            req_data = result.get("match_request") or {}
            if req_data:
                order = req_data.get("order") or {}
                receipt = req_data.get("warehouse_receipt") or {}
                invoice = req_data.get("invoice") or {}
                st.write(
                    {
                        "订单金额（万元）": order.get("total_amount"),
                        "入库金额（万元）": receipt.get("total_amount"),
                        "发票金额（万元）": invoice.get("total_amount"),
                        "入账日期": invoice.get("posting_date"),
                        "签收日期": receipt.get("receipt_date"),
                        "供应商": order.get("supplier_name"),
                    }
                )
            else:
                c_fields = ((merged.get("contract") or {}).get("fields") or {})
                o_fields = ((merged.get("order") or {}).get("fields") or {})
                r_fields = ((merged.get("receipt") or {}).get("fields") or {})
                i_fields = ((merged.get("invoice") or {}).get("fields") or {})
                st.write(
                    {
                        "合同账期": c_fields.get("paymentTerms") or o_fields.get("paymentTerms"),
                        "签收日期": _pick_receipt_date(r_fields),
                        "入账日期": i_fields.get("postingDate"),
                        "订单金额": o_fields.get("totalAmount") or o_fields.get("amount"),
                        "入库金额": r_fields.get("totalAmount") or r_fields.get("amount"),
                        "发票金额": i_fields.get("totalAmount") or i_fields.get("amount"),
                    }
                )
            st.caption(
                "金额已统一归一化为万元；入库单未识别金额时会自动沿用订单金额。"
                "底稿表「金额差异率」据此三列计算。"
            )
        _show_three_way_result(result)


def render_three_way_test() -> None:
    st.subheader("🔗 三单匹配 + 截止性测试 联动调试")
    st.caption("填写订单 / 入库单 / 发票后，调用 `/api/v1/three-way-match`。")

    with st.form("three_way_form"):
        t1, t2, t3 = st.tabs(["订单信息", "入库单信息", "发票信息"])
        with t1:
            order_no = st.text_input("订单编号", value="PO-DEBUG-001")
            supplier = st.text_input("供应商名称", value="甲供应商", key="tw_supplier")
            order_amount = st.number_input(
                "订单总金额（万元）", min_value=0.0, value=500.0, step=10.0
            )
            order_qty = st.number_input("数量", min_value=0.0, value=100.0, step=1.0)
            payment_terms = st.text_input("付款条款", value="签收后10日")
            contract_no = st.text_input("合同编号（可选）", value="HT-DEBUG-001")
        with t2:
            receipt_no = st.text_input("入库单编号", value="WR-DEBUG-001")
            receipt_order_no = st.text_input(
                "对应订单编号", value="PO-DEBUG-001", key="tw_receipt_po"
            )
            receipt_supplier = st.text_input(
                "供应商名称", value="甲供应商", key="tw_receipt_supplier"
            )
            receipt_amount = st.number_input(
                "入库金额（万元）", min_value=0.0, value=500.0, step=10.0
            )
            receipt_qty = st.number_input(
                "入库数量", min_value=0.0, value=100.0, step=1.0, key="tw_receipt_qty"
            )
            receipt_date = st.date_input("签收日期", value=DEFAULT_RECEIPT)
            receiver = st.text_input("签收人（可选）", value="张三")
        with t3:
            invoice_no = st.text_input("发票编号", value="INV-DEBUG-001")
            invoice_order_no = st.text_input(
                "对应订单编号", value="PO-DEBUG-001", key="tw_invoice_po"
            )
            invoice_supplier = st.text_input(
                "供应商名称", value="甲供应商", key="tw_invoice_supplier"
            )
            invoice_amount = st.number_input(
                "发票金额（万元）", min_value=0.0, value=500.0, step=10.0
            )
            invoice_qty = st.number_input(
                "发票数量", min_value=0.0, value=100.0, step=1.0, key="tw_invoice_qty"
            )
            posting_date = st.date_input("入账日期", value=DEFAULT_ENTRY)
            allow_empty_posting = st.checkbox(
                "入账日期留空（跳过截止性）", value=False
            )
        submitted = st.form_submit_button(
            "执行三单匹配 + 截止性测试", type="primary"
        )

    if not submitted:
        return
    if not order_no.strip():
        st.error("订单编号不能为空")
        return

    payload = {
        "order": {
            "order_no": order_no.strip(),
            "supplier_name": supplier.strip(),
            "total_amount": float(order_amount),
            "quantity": float(order_qty),
            "payment_terms": payment_terms.strip() or None,
            "contract_no": contract_no.strip() or None,
        },
        "warehouse_receipt": {
            "receipt_no": receipt_no.strip(),
            "order_no": receipt_order_no.strip() or order_no.strip(),
            "supplier_name": receipt_supplier.strip(),
            "total_amount": float(receipt_amount),
            "quantity": float(receipt_qty),
            "receipt_date": receipt_date.isoformat(),
            "receiver": receiver.strip() or None,
        },
        "invoice": {
            "invoice_no": invoice_no.strip(),
            "order_no": invoice_order_no.strip() or order_no.strip(),
            "supplier_name": invoice_supplier.strip(),
            "total_amount": float(invoice_amount),
            "quantity": float(invoice_qty),
            "posting_date": None if allow_empty_posting else posting_date.isoformat(),
        },
    }

    with st.spinner("调用 /api/v1/three-way-match …"):
        data, err = _post_three_way(payload)
    if err:
        st.error(err)
        return
    assert data is not None
    _show_three_way_result(data)


def main() -> None:
    st.set_page_config(page_title="截止性测试调试控制台", layout="wide")
    st.title("截止性测试 Agent · 调试控制台")
    st.caption("开发测试 / 离线验证入口（完整工作流可本地 OCR；其余页调用本地 API）")

    healthy = _api_health()
    if healthy:
        st.success(f"API 已连接：{API_BASE}")
    else:
        st.warning(
            f"无法连接 API（{API_BASE}）。「完整工作流」仍可本地执行；"
            "其它调试页请先运行 `python run_api.py` 或双击 `start_api.bat`。"
        )

    tab_workflow, tab_single, tab_batch, tab_book, tab_three = st.tabs(
        [
            "完整工作流",
            "单条截止性",
            "批量 JSONL",
            "查看底稿",
            "三单手动录入",
        ]
    )
    with tab_workflow:
        _ocr_status_banner()
        render_full_workflow()
    with tab_single:
        render_single_test()
    with tab_batch:
        render_batch_test()
    with tab_book:
        render_workbook_viewer()
    with tab_three:
        render_three_way_test()


if __name__ == "__main__":
    main()

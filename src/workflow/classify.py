"""单据分类（与 Streamlit 同源，无 UI 依赖）。"""

from __future__ import annotations

import re

DOC_TYPE_LABELS = {
    "contract": "合同",
    "order": "订单",
    "delivery": "发货单",
    "receipt": "签收/入库单",
    "invoice": "发票",
    "payment": "回款",
    "other": "其他",
}

DOC_TYPE_TO_OCR = {
    "contract": "contract",
    "order": "purchase_order",
    "delivery": "warehouse_receipt",
    "receipt": "warehouse_receipt",
    "invoice": "invoice",
    "payment": "other",
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


def classify_from_ocr_text(ocr_preview: str) -> str:
    """只凭识别正文判类型，不看文件名。"""
    text = ocr_preview or ""
    if not text.strip():
        return "other"
    if any(kw in text for kw in INVOICE_OCR_KEYWORDS):
        return "invoice"
    if any(kw in text for kw in ("银行流水", "回款金额", "收款账号", "付款人名称")):
        return "payment"
    if any(kw in text for kw in ("发货单号", "出库日期", "承运人")) and not any(
        kw in text for kw in ("签收人", "验收人", "签收日期")
    ):
        return "delivery"
    if any(kw in text for kw in ("签收人", "验收人", "收货日期", "入库单号", "签收日期")):
        return "receipt"
    if any(kw in text for kw in ("订单编号", "采购方", "供应商", "订单日期")):
        return "order"
    if any(kw in text for kw in ("合同编号", "甲方", "乙方", "签订日期", "付款条款")):
        return "contract"
    return "other"


def classify_document(
    file_name: str,
    ocr_preview: str = "",
    *,
    slot_hint: str = "",
) -> str:
    name = (file_name or "").strip()
    text = (ocr_preview or "").strip()

    def _name_has(*keywords: str) -> bool:
        lower = name.lower()
        for kw in keywords:
            if kw.lower() in lower:
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
        if _name_has("增值税发票"):
            return "invoice"
        if _name_has(*INVOICE_FILENAME_KEYWORDS) or _name_token("FP", "INV"):
            return "invoice"
        if _name_has("回款", "银行流水", "收款凭证", "收款单", "bank", "payment"):
            return "payment"
        if _name_has("销售发货单", "发货单", "出库单", "delivery note") or (
            _name_has("发货") and not _name_has("签收", "验收")
        ):
            return "delivery"
        if _name_has(
            "产品验收单",
            "验收单",
            "客户签收",
            "签收单",
            "入库单",
            "收货单",
            "receipt",
            "warehouse",
        ) or _name_has("签收", "验收", "入库", "收货"):
            return "receipt"
        if (
            _name_has("销售订单", "采购订单", "订单", "order", "采购单", "sales order")
            or _name_token("SO", "PO")
        ):
            return "order"
        if (
            _name_has("销售合同", "采购合同", "合同", "contract", "协议", "agreement")
            or _name_token("HT")
        ):
            return "contract"
        return "other"

    name_type = _classify_by_filename()
    if name_type != "other":
        return name_type
    ocr_type = classify_from_ocr_text(text)
    if ocr_type != "other":
        return ocr_type
    hint = (slot_hint or "").strip().lower()
    if hint in DOC_TYPE_LABELS:
        return hint
    return "other"


def fallback_fields_from_filename(file_name: str, doc_type: str) -> dict:
    from src.legacy_ocr.ledger_parser import extract_biz_ids_from_filename

    fields: dict = {}
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


def merge_fields(primary: dict, fallback: dict) -> dict:
    merged = dict(fallback)
    for key, val in primary.items():
        if val is not None and str(val).strip():
            merged[key] = val
    return merged


def peek_document_text(path: str, *, max_chars: int = 2500) -> str:
    """轻量正文预览：优先 PDF 文本层，不做完整 OCR。"""
    p = (path or "").strip()
    if not p:
        return ""
    lower = p.lower()
    try:
        if lower.endswith(".pdf"):
            from src.legacy_ocr.ocr_adapter import _extract_pdf_text_layer

            text = _extract_pdf_text_layer(p) or ""
            return str(text)[:max_chars]
        if lower.endswith((".txt", ".md", ".csv")):
            from pathlib import Path

            return Path(p).read_text(encoding="utf-8", errors="ignore")[:max_chars]
    except Exception:
        return ""
    return ""


def light_classify_file(
    file_name: str,
    path: str = "",
    *,
    slot_hint: str = "",
) -> dict:
    """上传后轻量分类：文件名 + 可选文本层；不定字段。"""
    from src.legacy_ocr.ledger_parser import extract_biz_ids_from_filename

    peek = peek_document_text(path) if path else ""
    doc_type = classify_document(file_name, peek, slot_hint=slot_hint)
    biz_ids = extract_biz_ids_from_filename(file_name)
    confident = doc_type != "other" or bool(slot_hint) or bool(biz_ids)
    # 文件名含 SO/HT 但类型 other 时，按序号后缀推断
    if doc_type == "other" and biz_ids:
        lower = file_name.lower()
        if re.search(r"[_-]0?1[_-]|合同|contract", lower):
            doc_type = "contract"
            confident = True
        elif re.search(r"[_-]0?2[_-]|订单|order", lower):
            doc_type = "order"
            confident = True
        elif re.search(r"[_-]0?3[_-]|发货|delivery", lower):
            doc_type = "delivery"
            confident = True
        elif re.search(r"[_-]0?4[_-]|签收|验收|receipt", lower):
            doc_type = "receipt"
            confident = True
        elif re.search(r"[_-]0?5[_-]|发票|invoice", lower):
            doc_type = "invoice"
            confident = True
        elif re.search(r"[_-]0?6[_-]|回款|payment|银行", lower):
            doc_type = "payment"
            confident = True
    return {
        "doc_type": doc_type,
        "doc_type_source": "light",
        "peek_chars": len(peek),
        "confident": confident,
        "biz_ids": biz_ids,
    }
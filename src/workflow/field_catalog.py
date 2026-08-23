"""OCR 前字段清单：系统必用（只读）+ 按类型可选 + 全局附加。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Optional

FIELD_LABELS: dict[str, str] = {
    "documentNo": "单据编号",
    "contractNo": "合同编号",
    "orderNo": "订单编号",
    "invoiceNo": "发票号码",
    "invoiceCode": "发票代码",
    "documentDate": "单据日期",
    "postingDate": "入账日期",
    "deliveryDate": "发货日期",
    "acceptanceDate": "签收日期",
    "paymentTerms": "付款条款",
    "controlTransferTerms": "控制权转移",
    "settlementTerms": "结算条款",
    "transportTerms": "运输条款",
    "performanceObligations": "履约义务",
    "totalAmount": "价税合计",
    "amount": "金额（不含税）",
    "taxAmount": "税额",
    "taxRate": "税率",
    "quantity": "数量",
    "unit": "单位",
    "supplierName": "销方/供应商",
    "buyerName": "购方",
    "supplierTaxId": "销方税号",
    "buyerTaxId": "购方税号",
    "discountRate": "折扣率",
    "discountAmount": "折扣额",
    "warehouseNo": "仓库/库位",
    "projectName": "项目名称",
    "remarks": "备注",
}

# 系统审阅必用：展示只读，不可取消
SYSTEM_REQUIRED: dict[str, tuple[str, ...]] = {
    "contract": (
        "contractNo",
        "documentNo",
        "documentDate",
        "buyerName",
        "supplierName",
        "totalAmount",
        "paymentTerms",
        "controlTransferTerms",
    ),
    "order": (
        "orderNo",
        "documentNo",
        "contractNo",
        "documentDate",
        "buyerName",
        "supplierName",
        "quantity",
        "totalAmount",
        "paymentTerms",
    ),
    "delivery": (
        "documentNo",
        "orderNo",
        "quantity",
        "deliveryDate",
        "documentDate",
        "buyerName",
    ),
    "receipt": (
        "documentNo",
        "orderNo",
        "quantity",
        "acceptanceDate",
        "deliveryDate",
        "documentDate",
        "buyerName",
    ),
    "invoice": (
        "invoiceNo",
        "documentNo",
        "quantity",
        "totalAmount",
        "amount",
        "taxAmount",
        "postingDate",
        "documentDate",
        "supplierName",
        "buyerName",
    ),
    "payment": ("documentNo", "totalAmount", "documentDate", "buyerName", "supplierName"),
    "other": ("documentNo", "documentDate", "totalAmount"),
}

# 按类型可选（默认勾选常见项）
TYPE_OPTIONAL: dict[str, tuple[str, ...]] = {
    "contract": (
        "settlementTerms",
        "transportTerms",
        "performanceObligations",
        "quantity",
        "remarks",
    ),
    "order": ("settlementTerms", "deliveryDate", "taxAmount", "amount", "remarks"),
    "delivery": ("supplierName", "warehouseNo", "remarks"),
    "receipt": ("supplierName", "warehouseNo", "remarks"),
    "invoice": ("invoiceCode", "taxRate", "orderNo", "contractNo", "remarks"),
    "payment": ("remarks", "orderNo", "contractNo", "invoiceNo"),
    "other": ("quantity", "buyerName", "supplierName", "remarks"),
}

DOC_TYPES = ("contract", "order", "delivery", "receipt", "invoice", "payment", "other")

# 金额字段业务口径：视觉仲裁 / 规则矿工共用，禁止各写一套
AMOUNT_FIELD_SPECS: dict[str, dict[str, Any]] = {
    "amount": {
        "field_name": "金额（不含税）",
        "definition": "商品或服务在扣除折扣后、计算增值税前的金额（折后未税合计）",
        "include_labels": [
            "不含税金额",
            "未税金额",
            "合计（不含税）",
            "合计不含税",
            "折后不含税",
            "金额合计（不含税）",
        ],
        "exclude_labels": [
            "价税合计",
            "含税总额",
            "含税合计",
            "应收合计",
            "授信额度",
            "可用额度",
            "信用额度",
            "税额",
            "折扣前",
            "行金额",
        ],
        "tax_basis": "tax_exclusive",
    },
    "taxAmount": {
        "field_name": "税额",
        "definition": "增值税税额合计（销项税额），不是价税合计也不是未税金额",
        "include_labels": ["税额", "税额合计", "合计税额", "增值税额"],
        "exclude_labels": [
            "价税合计",
            "不含税金额",
            "未税金额",
            "授信额度",
            "可用额度",
            "折扣前",
        ],
        "tax_basis": "tax_only",
    },
    "totalAmount": {
        "field_name": "价税合计",
        "definition": "价税合计／含税应收总额（未税+税额）；不是授信额度或行明细金额",
        "include_labels": [
            "价税合计",
            "含税总金额",
            "含税合计",
            "合计金额",
            "总金额",
            "总额",
            "金额合计",
            "本次应收",
        ],
        "exclude_labels": [
            "授信额度",
            "可用额度",
            "信用额度",
            "不含税金额",
            "未税金额",
            "税额",
            "折扣前",
            "行金额",
        ],
        "tax_basis": "tax_inclusive",
    },
}


def field_label(key: str) -> str:
    return FIELD_LABELS.get(key) or key


def amount_field_spec(field_key: str) -> dict[str, Any]:
    """Return amount semantics for vision / mining; unknown keys get a safe stub."""
    key = str(field_key or "").strip()
    if key in AMOUNT_FIELD_SPECS:
        return dict(AMOUNT_FIELD_SPECS[key])
    return {
        "field_name": field_label(key) or key,
        "definition": f"业务字段 {key}",
        "include_labels": [],
        "exclude_labels": ["授信额度", "可用额度", "信用额度"],
        "tax_basis": "unknown",
    }


def catalog_payload() -> dict[str, Any]:
    types: dict[str, Any] = {}
    for dt in DOC_TYPES:
        req = list(SYSTEM_REQUIRED.get(dt, ()))
        opt = list(TYPE_OPTIONAL.get(dt, ()))
        types[dt] = {
            "system_required": [{"key": k, "label": field_label(k), "locked": True} for k in req],
            "optional": [{"key": k, "label": field_label(k), "locked": False} for k in opt],
        }
    return {
        "doc_types": list(DOC_TYPES),
        "field_labels": dict(FIELD_LABELS),
        "by_type": types,
    }


def default_field_plan() -> dict[str, Any]:
    by_type: dict[str, Any] = {}
    for dt in DOC_TYPES:
        by_type[dt] = {
            "system_required": list(SYSTEM_REQUIRED.get(dt, ())),
            "selected_optional": [],
            "custom": [],
        }
    return {
        "confirmed": False,
        "confirmed_at": None,
        "global_extra": [],
        "by_type": by_type,
    }


def auto_confirm_field_plan(plan: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """底稿目标已定必填；主路径不再等人点「确认字段清单」。"""
    from datetime import datetime, timezone

    out = ensure_field_plan(plan)
    out["confirmed"] = True
    out["confirmed_at"] = datetime.now(timezone.utc).isoformat()
    return out


def ensure_field_plan(plan: Optional[dict[str, Any]]) -> dict[str, Any]:
    base = default_field_plan()
    if not isinstance(plan, dict):
        return base
    out = deepcopy(base)
    out["confirmed"] = bool(plan.get("confirmed"))
    out["confirmed_at"] = plan.get("confirmed_at")
    ge = plan.get("global_extra")
    if isinstance(ge, list):
        out["global_extra"] = _clean_keys(ge)
    raw_by = plan.get("by_type") if isinstance(plan.get("by_type"), dict) else {}
    for dt in DOC_TYPES:
        src = raw_by.get(dt) if isinstance(raw_by.get(dt), dict) else {}
        locked = list(SYSTEM_REQUIRED.get(dt, ()))
        selected = _clean_keys(src.get("selected_optional") or out["by_type"][dt]["selected_optional"])
        # 去掉与必用重复的可选
        selected = [k for k in selected if k not in locked]
        custom = _clean_keys(src.get("custom") or [])
        custom = [k for k in custom if k not in locked and k not in selected]
        out["by_type"][dt] = {
            "system_required": locked,
            "selected_optional": selected,
            "custom": custom,
        }
    return out


def _clean_keys(keys: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    if not isinstance(keys, list):
        return out
    for raw in keys:
        k = str(raw or "").strip()
        if not k or k.startswith("_") or k in {"documentType", "items"}:
            continue
        if k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out


def resolve_target_fields(doc_type: str, plan: Optional[dict[str, Any]]) -> list[str]:
    """合并：系统必用 + 该类型已选可选/自定义 + 全局附加。"""
    fp = ensure_field_plan(plan)
    dt = doc_type if doc_type in DOC_TYPES else "other"
    slot = fp["by_type"].get(dt) or fp["by_type"]["other"]
    keys: list[str] = []
    for k in list(slot.get("system_required") or []) + list(slot.get("selected_optional") or []) + list(
        slot.get("custom") or []
    ) + list(fp.get("global_extra") or []):
        if k not in keys:
            keys.append(k)
    return keys


def normalize_field_plan_update(body: dict[str, Any], *, confirm: bool = False) -> dict[str, Any]:
    from datetime import datetime, timezone

    plan = ensure_field_plan(body)
    if confirm:
        plan["confirmed"] = True
        plan["confirmed_at"] = datetime.now(timezone.utc).isoformat()
    return plan

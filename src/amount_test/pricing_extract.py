"""从单据 fields / Markdown 正文提取计价要素与交付数量。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.legacy_ocr.amount_resolve import _parse_number, _parse_tax_rate
from src.amount_test.models import SourceValues

_FORBIDDEN_NAME_PARTS = (
    "内部制作说明",
    "结构化元数据",
    "标准答案",
)


def is_forbidden_evidence_file(name: str) -> bool:
    n = str(name or "")
    return any(x in n for x in _FORBIDDEN_NAME_PARTS)


def _parse_pct(text: str) -> Optional[float]:
    if text is None:
        return None
    s = str(text).strip().replace(",", "")
    if not s or s.lower() in {"nan", "none", "-"}:
        return None
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*%", s)
    if m:
        return float(m.group(1)) / 100.0
    # 0%（出口零税率）
    if "零税率" in s or re.search(r"\b0\s*%", s):
        return 0.0
    num = _parse_number(s)
    if num is None:
        return None
    if num > 1:
        return num / 100.0
    return float(num)


def _zhe_to_rate(text: str) -> Optional[float]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*折", str(text))
    if not m:
        return None
    zhe = float(m.group(1))
    if 0 < zhe <= 10:
        return max(0.0, 1.0 - zhe / 10.0)
    return None


def parse_discount_rate(value: Any, text: str = "") -> Optional[float]:
    if value is not None and str(value).strip():
        z = _zhe_to_rate(str(value))
        if z is not None:
            return z
        p = _parse_pct(str(value))
        if p is not None:
            return p
    if text:
        z = _zhe_to_rate(text)
        if z is not None:
            return z
        m = re.search(r"商业折扣\s*[:：]?\s*(\d+(?:\.\d+)?)\s*%", text)
        if m:
            return float(m.group(1)) / 100.0
        m = re.search(r"折扣率\s*[:：]?\s*(\d+(?:\.\d+)?)\s*%", text)
        if m:
            return float(m.group(1)) / 100.0
    return None


def parse_vat_rate(value: Any, text: str = "") -> Optional[float]:
    if value is not None and str(value).strip():
        if "出口" in str(value) and "零" in str(value):
            return 0.0
        p = _parse_pct(str(value))
        if p is not None:
            return p
        tr = _parse_tax_rate({"taxRate": value}, "")
        if tr is not None:
            return tr
    if text:
        if re.search(r"出口零税率|税率\s*[:：]?\s*0\s*%", text):
            return 0.0
        m = re.search(r"税率\s*[:：]?\s*(\d+(?:\.\d+)?)\s*%", text)
        if m:
            return float(m.group(1)) / 100.0
        tr = _parse_tax_rate({}, text)
        if tr is not None:
            return tr
    return None


def _md_cells(line: str) -> List[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _header_index(headers: Sequence[str], *keywords: str) -> Optional[int]:
    for i, h in enumerate(headers):
        if all(k in h for k in keywords):
            return i
    for i, h in enumerate(headers):
        if any(k == h or k in h for k in keywords):
            return i
    return None


def _parse_markdown_price_table(text: str) -> Optional[Dict[str, float]]:
    """解析合同/订单中的价格明细表行（按表头定位列，避免把折后金额当税率）。"""
    if not text:
        return None
    lines = text.splitlines()
    header_idx = -1
    headers: List[str] = []
    for i, line in enumerate(lines):
        if "基础不含税单价" in line and ("商业折扣" in line or "折扣" in line):
            headers = _md_cells(line)
            header_idx = i
            break
    if header_idx < 0 or not headers:
        return None

    idx_qty = _header_index(headers, "数量")
    idx_price = _header_index(headers, "基础不含税单价") or _header_index(headers, "单价")
    idx_disc = _header_index(headers, "商业折扣") or _header_index(headers, "折扣")
    idx_vat = _header_index(headers, "税率")
    # 排除「折后不含税金额」被误识别为数量
    if idx_qty is not None and "折后" in headers[idx_qty]:
        idx_qty = None
        for i, h in enumerate(headers):
            if h == "数量" or (h.endswith("数量") and "折" not in h and "实收" not in h):
                idx_qty = i
                break

    for line in lines[header_idx + 1 : header_idx + 10]:
        if not line.strip().startswith("|"):
            continue
        if re.search(r"\|\s*---", line):
            continue
        cells = _md_cells(line)
        if len(cells) < 4:
            continue
        try:
            qty = (
                _parse_number(re.sub(r"[^\d.]", "", cells[idx_qty]))
                if idx_qty is not None and idx_qty < len(cells)
                else None
            )
            price = (
                _parse_number(cells[idx_price])
                if idx_price is not None and idx_price < len(cells)
                else None
            )
            disc = (
                parse_discount_rate(cells[idx_disc])
                if idx_disc is not None and idx_disc < len(cells)
                else 0.0
            )
            vat = None
            if idx_vat is not None and idx_vat < len(cells):
                vat = parse_vat_rate(cells[idx_vat])
            if qty and price and qty > 0 and price > 0:
                # 税率必须是比率；若误解析成金额（>1）则回退
                if vat is not None and vat > 1:
                    vat = None
                return {
                    "quantity": float(qty),
                    "unit_price_excl_tax": float(price),
                    "discount_rate": float(disc or 0.0),
                    "vat_rate": float(vat if vat is not None else 0.13),
                }
        except (TypeError, ValueError, IndexError):
            continue
    return None


def _parse_receipt_quantity(text: str) -> Optional[float]:
    from src.legacy_ocr.field_aliases import parse_quantity_from_delivery_table

    return parse_quantity_from_delivery_table(text or "")


def extract_from_text(text: str, *, role: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not text:
        return out
    if role in {"order", "contract", ""}:
        tab = _parse_markdown_price_table(text)
        if tab:
            out.update(tab)
    if role in {"receipt", "delivery", "bol", ""}:
        q = _parse_receipt_quantity(text)
        if q:
            out["delivered_quantity"] = q
            out["quantity"] = q
    if "discount_rate" not in out:
        d = parse_discount_rate(None, text)
        if d is not None:
            out["discount_rate"] = d
    if "vat_rate" not in out:
        v = parse_vat_rate(None, text)
        if v is not None:
            out["vat_rate"] = v
    if "unit_price_excl_tax" not in out:
        m = re.search(r"基础不含税单价\s*[:：]?\s*(\d+(?:\.\d+)?)", text)
        if m:
            out["unit_price_excl_tax"] = float(m.group(1))
    if "quantity" not in out:
        m = re.search(r"\b数量\s*[:：]?\s*(\d+(?:\.\d+)?)", text)
        if m:
            out["quantity"] = float(m.group(1))
    return out


def extract_from_fields(fields: Dict[str, Any], ocr_text: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    qty = _parse_number(fields.get("quantity") or fields.get("deliveredQuantity"))
    if qty:
        out["quantity"] = float(qty)
    price = None
    for key in (
        "unitPriceExclTax",
        "unit_price_excl_tax",
        "unitPrice",
        "price",
        "未税单价",
        "单价",
    ):
        price = _parse_number(fields.get(key))
        if price and price > 0:
            out["unit_price_excl_tax"] = float(price)
            break
    disc = parse_discount_rate(
        fields.get("discountRate") or fields.get("discount") or fields.get("折扣率"),
        ocr_text,
    )
    if disc is not None:
        out["discount_rate"] = disc
    vat = parse_vat_rate(
        fields.get("taxRate") or fields.get("vatRate") or fields.get("税率"),
        ocr_text,
    )
    if vat is not None:
        out["vat_rate"] = vat
    # items 行
    items = fields.get("items")
    if isinstance(items, list) and items and "unit_price_excl_tax" not in out:
        qsum = 0.0
        # 单行优先
        row = items[0] if isinstance(items[0], dict) else None
        if row:
            q = _parse_number(row.get("quantity"))
            p = _parse_number(row.get("unitPrice") or row.get("unitPriceExclTax"))
            if q and p:
                out["quantity"] = float(q)
                out["unit_price_excl_tax"] = float(p)
                ld = parse_discount_rate(row.get("discountRate") or row.get("discount"), "")
                if ld is not None:
                    out["discount_rate"] = ld
    if ocr_text:
        parsed = extract_from_text(ocr_text)
        for k, v in parsed.items():
            out.setdefault(k, v)
    return out


def merge_pricing_from_documents(
    documents: Sequence[Dict[str, Any]],
    *,
    existing_advisory: Optional[List[Dict[str, Any]]] = None,
    business_id: str = "",
) -> Tuple[SourceValues, List[str], List[str], List[Dict[str, Any]]]:
    """按权威来源合并：单价/折扣←合同+订单；数量←签收/提单；税率←发票优先。

    返回 (SourceValues, indexes, warnings, advisory_store)。
    LLM 补缺会写入单据字段三值候选，并经 verifier 闸门进入 advisory_store。
    """
    warnings: List[str] = []
    indexes: List[str] = []
    by_role: Dict[str, Dict[str, Any]] = {}

    for doc in documents:
        name = str(doc.get("file_name") or "")
        if is_forbidden_evidence_file(name):
            continue
        role = str(doc.get("doc_type") or doc.get("role") or "").lower()
        fields = dict(doc.get("fields") or {})
        text = str(doc.get("raw_text") or doc.get("ocr_text") or "")
        extracted = extract_from_fields(fields, text)
        if text and role in {"order", "contract", "receipt", "delivery", "invoice"}:
            extracted.update({k: v for k, v in extract_from_text(text, role=role).items() if v is not None})
        if not extracted and text:
            extracted = extract_from_text(text, role=role)
        by_role.setdefault(role, {})
        for k, v in extracted.items():
            if v is not None and k not in by_role[role]:
                by_role[role][k] = v
        if name:
            indexes.append(name)

    def _sane_vat(v: Any) -> Optional[float]:
        if v is None:
            return None
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return None
        # 税率不可能大于 100%；大于 1 视为误把金额当税率
        if fv < 0 or fv > 1:
            return None
        return fv

    price = None
    price_src = ""
    disc = None
    vat = None
    for role in ("order", "contract", "invoice"):
        block = by_role.get(role) or {}
        if price is None and block.get("unit_price_excl_tax"):
            price = float(block["unit_price_excl_tax"])
            price_src = role
        if disc is None and block.get("discount_rate") is not None:
            disc = float(block["discount_rate"])
        if vat is None:
            vat = _sane_vat(block.get("vat_rate"))

    # 税率：发票优先（仍须为比率）
    inv_vat = _sane_vat((by_role.get("invoice") or {}).get("vat_rate"))
    if inv_vat is not None:
        vat = inv_vat

    qty = None
    qty_src = ""
    for role in ("receipt", "delivery", "bol"):
        block = by_role.get(role) or {}
        q = block.get("delivered_quantity") or block.get("quantity")
        if q:
            qty = float(q)
            qty_src = role
            break
    if qty is None:
        for role in ("order", "invoice", "contract"):
            block = by_role.get(role) or {}
            if block.get("quantity"):
                qty = float(block["quantity"])
                qty_src = f"{role}(非交付权威)"
                warnings.append("未取到签收/提单实收数量，回退订单/发票数量")
                break

    if price is None:
        warnings.append("缺少基础不含税单价")
    if qty is None:
        warnings.append("缺少交易数量")

    # 出口检测：仅当税率文本明确为出口零税率，或文件名/条款含出口合同特征且表内税率为 0
    blob = " ".join(str(d.get("raw_text") or "")[:1500] for d in documents)
    names = " ".join(str(d.get("file_name") or "") for d in documents)
    explicit_export_zero = bool(
        re.search(r"出口零税率|0%\s*（出口零税率）", blob)
    )
    export_pack = bool(re.search(r"EXHT|EXKJHT|海运提单|E?BL\d{2}-", names + blob))
    if explicit_export_zero or (export_pack and (vat is None or vat == 0.0)):
        vat = 0.0
    elif vat is None:
        vat = 0.13

    # 规则抽不全 → LLM 仅补缺字段；重算仍走公式；候选进三值 + 顾问队列
    advisory_store: List[Dict[str, Any]] = list(existing_advisory or [])
    if price is None or qty is None:
        try:
            from src.llm.batch_assist import llm_fill_pricing_gaps
            from src.models.field_values import set_candidate
            from src.llm.verifier import evidence_blob_from_documents
            from src.audit.gap_fill_orchestrator import ingest_verified_claims

            patch, llm_notes, llm_claims = llm_fill_pricing_gaps(
                quantity=qty,
                unit_price_excl_tax=price,
                discount_rate=disc,
                vat_rate=vat,
                documents=documents,
            )
            warnings.extend(llm_notes)
            if patch.get("quantity") and qty is None:
                qty = float(patch["quantity"])
                qty_src = "llm"
            if patch.get("unit_price_excl_tax") and price is None:
                price = float(patch["unit_price_excl_tax"])
                price_src = "llm"
            if disc is None and patch.get("discount_rate") is not None:
                disc = float(patch["discount_rate"])
            if patch.get("vat_rate") is not None and not explicit_export_zero:
                cand = float(patch["vat_rate"])
                if 0 <= cand <= 1:
                    vat = cand

            # 写入字段三值（不自动 accepted）
            field_map = {
                "quantity": ("quantity", ("receipt", "delivery", "bol", "order")),
                "unit_price_excl_tax": (
                    "unitPriceExclTax",
                    ("order", "contract", "invoice"),
                ),
                "discount_rate": ("discountRate", ("order", "contract")),
                "vat_rate": ("vatRate", ("invoice", "order", "contract")),
            }
            for patch_key, value in patch.items():
                meta = field_map.get(patch_key)
                if not meta:
                    continue
                field_name, roles = meta
                for doc in documents:
                    role = str(doc.get("doc_type") or doc.get("role") or "").lower()
                    if role not in roles:
                        continue
                    set_candidate(
                        doc,
                        field_name,
                        value,
                        source="llm",
                        extractor="AMOUNT_GAP_FILL",
                    )
                    for claim in llm_claims:
                        if claim.get("field_name") == patch_key:
                            claim["file_name"] = str(doc.get("file_name") or "")
                    break

            if llm_claims:
                # 归属业务号：优先显式传入，其次从单据字段推断
                biz = str(business_id or "").strip()
                if not biz:
                    for d in documents:
                        fields = d.get("fields") or {}
                        for k in ("orderNo", "salesOrderNo", "documentNo", "contractNo"):
                            if fields.get(k):
                                biz = str(fields.get(k)).strip()
                                break
                        if biz:
                            break
                for claim in llm_claims:
                    claim.setdefault("business_id", biz)
                blob = evidence_blob_from_documents(
                    [
                        {
                            "file_name": d.get("file_name"),
                            "doc_type": d.get("doc_type"),
                            "raw_text": d.get("raw_text") or d.get("ocr_text") or "",
                        }
                        for d in documents
                    ]
                )
                # 有摘录才强制回查；无摘录允许弱入库但 DROPPED 若 require 失败
                require_ex = any(str(c.get("excerpt") or "").strip() for c in llm_claims)
                ingest = ingest_verified_claims(
                    advisory_store,
                    task_type="AMOUNT_GAP_FILL",
                    claims=llm_claims,
                    full_text=blob,
                    trigger_reasons=["PRICING_ELEMENT_MISSING"],
                    business_id=biz,
                    kind="fact",
                    require_excerpt=require_ex,
                    min_confidence=0.5 if not require_ex else 0.85,
                )
                advisory_store = ingest["store"]
                warnings.append(
                    f"顾问候选入库 proposed={len(ingest.get('proposed') or [])} "
                    f"dropped={len(ingest.get('dropped') or [])}"
                )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"LLM 计价补抽跳过：{exc}")

    source = SourceValues(
        currency="CNY",
        quantity=qty,
        unit_price_excl_tax=price,
        discount_rate=disc if disc is not None else 0.0,
        vat_rate=vat,
        price_basis="EXCL_TAX",
        quantity_source=qty_src,
        price_source=price_src,
    )
    return source, indexes, warnings, advisory_store

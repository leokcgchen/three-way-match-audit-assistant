"""同一人工业务组内的纸面一对多三单分配与累计。

本模块只处理 OCR/票面字段；不判断 ERP 过账，也不改变截止性口径。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional


ZERO = Decimal("0")


@dataclass
class LineAllocation:
    source_file: str
    source_line_id: str
    source_role: str
    order_line_id: Optional[str]
    qty: Decimal
    amount: Optional[Decimal]
    bind_status: str
    bind_rank: Optional[int]
    basis: list[str]
    review_status: str
    rejected_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["qty"] = _decimal_text(self.qty)
        out["amount"] = None if self.amount is None else _decimal_text(self.amount)
        return out


def _text(value: Any) -> str:
    return str(value or "").strip()


def _key(value: Any) -> str:
    return "".join(ch for ch in _text(value).upper() if ch.isalnum())


def _decimal(value: Any) -> Decimal:
    if value in (None, ""):
        return ZERO
    try:
        return Decimal(_text(value).replace(",", "").replace("¥", ""))
    except (InvalidOperation, ValueError):
        return ZERO


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _role(item: dict[str, Any]) -> str:
    doc_type = _text(item.get("doc_type")).lower()
    if doc_type in {"order", "purchase_order", "po"}:
        return "order"
    if doc_type in {"receipt", "warehouse_receipt", "delivery", "grn"}:
        return "receipt"
    if doc_type in {"invoice", "vat_invoice", "inv"}:
        return "invoice"
    return "other"


def _fields(item: dict[str, Any]) -> dict[str, Any]:
    return dict(item.get("fields") or {})


def _lines(item: dict[str, Any], role: str) -> list[dict[str, Any]]:
    fields = _fields(item)
    rows = fields.get("items")
    if isinstance(rows, list) and any(isinstance(row, dict) for row in rows):
        return [dict(row) for row in rows if isinstance(row, dict)]
    quantity = fields.get("quantity")
    if quantity in (None, ""):
        return []
    amount = fields.get("totalAmount") or fields.get("amount")
    return [
        {
            "lineNo": "H1",
            "itemCode": fields.get("itemCode") or fields.get("materialCode"),
            "quantity": quantity,
            "unit": fields.get("unit"),
            "amount": amount,
        }
    ]


def _line_id(row: dict[str, Any], index: int) -> str:
    return _text(row.get("poLineId") or row.get("lineNo")) or f"L{index + 1}"


def _qty(row: dict[str, Any]) -> Decimal:
    return _decimal(row.get("quantity") or row.get("qty"))


def _qty_missing(row: dict[str, Any]) -> bool:
    return row.get("quantity") in (None, "") and row.get("qty") in (None, "")


def _amount_with_basis(row: dict[str, Any]) -> tuple[Optional[Decimal], Optional[str]]:
    """统一金额口径：含税总额优先，其次未税金额加税额，最后才退回单一金额。"""
    if row.get("totalAmount") not in (None, ""):
        return _decimal(row.get("totalAmount")), "gross_total"
    amount = next(
        (row.get(key) for key in ("amount", "netAmount") if row.get(key) not in (None, "")),
        None,
    )
    if amount not in (None, "") and row.get("taxAmount") not in (None, ""):
        return _decimal(amount) + _decimal(row.get("taxAmount")), "amount_plus_tax"
    if amount not in (None, ""):
        return _decimal(amount), "amount_only"
    return None, None


def _amount(row: dict[str, Any]) -> Optional[Decimal]:
    return _amount_with_basis(row)[0]


def _filter_group(
    classified: list[dict[str, Any]], business_group_id: Optional[str]
) -> list[dict[str, Any]]:
    if not business_group_id:
        return list(classified or [])
    wanted = _text(business_group_id)
    return [
        item
        for item in (classified or [])
        if _text(item.get("business_group_id")) == wanted
        or wanted in [_text(value) for value in (item.get("business_ids") or [])]
    ]


def _document_event_key(
    item: dict[str, Any], role: str
) -> Optional[tuple[str, str, str, str, str]]:
    """用强业务字段识别同一履约事件；任一关键字段缺失时不自动去重。"""
    fields = _fields(item)
    if role == "order":
        document_no = _text(fields.get("orderNo") or fields.get("documentNo"))
        order_ref = document_no
        business_date = _text(fields.get("documentDate"))
    elif role == "receipt":
        document_no = _text(fields.get("documentNo") or fields.get("receiptNo"))
        order_ref = _text(fields.get("orderNo") or fields.get("salesOrderNo"))
        business_date = _text(
            fields.get("acceptanceDate")
            or fields.get("deliveryDate")
            or fields.get("documentDate")
        )
    else:
        document_no = _text(fields.get("invoiceNo") or fields.get("documentNo"))
        order_ref = _text(fields.get("orderNo") or fields.get("salesOrderNo"))
        business_date = _text(fields.get("documentDate"))
    quantity = sum((_qty(line) for line in _lines(item, role)), ZERO)
    if not document_no or not order_ref or not business_date or quantity <= ZERO:
        return None
    return (
        role,
        _key(document_no),
        _key(order_ref),
        business_date,
        _decimal_text(quantity),
    )


def _deduplicate_role_docs(
    by_role: dict[str, list[dict[str, Any]]]
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, str]]]:
    kept: dict[str, list[dict[str, Any]]] = {role: [] for role in by_role}
    duplicates: list[dict[str, str]] = []
    for role, docs in by_role.items():
        seen: dict[tuple[str, str, str, str, str], str] = {}
        for item in docs:
            file_name = _text(item.get("file_name"))
            event_key = _document_event_key(item, role)
            primary_file = seen.get(event_key) if event_key else None
            if primary_file and primary_file != file_name:
                fields = _fields(item)
                duplicates.append(
                    {
                        "role": role,
                        "primary_file": primary_file,
                        "duplicate_file": file_name,
                        "document_no": _text(
                            fields.get("invoiceNo")
                            if role == "invoice"
                            else fields.get("orderNo")
                            if role == "order"
                            else fields.get("documentNo") or fields.get("receiptNo")
                        ),
                    }
                )
                continue
            if event_key:
                seen[event_key] = file_name
            kept[role].append(item)
    return kept, duplicates


def _find_order_line(
    source: dict[str, Any],
    order_lines: list[dict[str, Any]],
    source_order_ref: Any = None,
) -> tuple[Optional[dict[str, Any]], Optional[int], list[str]]:
    candidates = order_lines
    reference_basis: list[str] = []
    order_ref = _key(source_order_ref)
    if order_ref:
        referenced = [row for row in order_lines if row.get("order_ref") == order_ref]
        if referenced:
            candidates = referenced
            reference_basis = ["订单号精确一致"]
    line_no = _key(source.get("poLineId") or source.get("lineNo"))
    if line_no:
        matches = [row for row in candidates if _key(row["line"].get("poLineId") or row["line"].get("lineNo")) == line_no]
        if len(matches) == 1:
            return matches[0], 1 if reference_basis else 2, [*reference_basis, "订单行号精确一致"]
    item_code = _key(source.get("itemCode") or source.get("materialCode"))
    if item_code:
        matches = [row for row in candidates if _key(row["line"].get("itemCode") or row["line"].get("materialCode")) == item_code]
        if len(matches) == 1:
            return matches[0], 2 if reference_basis else 3, [*reference_basis, "物料编码精确一致"]
    model = _key(source.get("model") or source.get("specification") or source.get("specModel"))
    if model:
        matches = [
            row
            for row in candidates
            if _key(
                row["line"].get("model")
                or row["line"].get("specification")
                or row["line"].get("specModel")
            )
            == model
        ]
        if len(matches) == 1:
            return matches[0], 3 if reference_basis else 4, [*reference_basis, "规格型号精确一致"]
        if any(
            _key(
                row["line"].get("model")
                or row["line"].get("specification")
                or row["line"].get("specModel")
            )
            for row in candidates
        ):
            return None, None, [*reference_basis, "STRONG_MODEL_CONFLICT"]
    goods_name = _key(source.get("goodsName") or source.get("productName") or source.get("itemName"))
    if goods_name:
        matches = [
            row
            for row in candidates
            if _key(
                row["line"].get("goodsName")
                or row["line"].get("productName")
                or row["line"].get("itemName")
            )
            == goods_name
        ]
        if len(matches) == 1:
            return matches[0], 4 if reference_basis else 5, [*reference_basis, "货品名称规范化一致"]
    if len(candidates) == 1:
        return candidates[0], 5 if reference_basis else 6, [*reference_basis, "候选范围内唯一订单行"]
    if len(candidates) > 1:
        return None, None, ["存在多个同等级订单行候选，须审计师确认"]
    return None, None, ["无法唯一定位订单行"]


def _header_reference_flags(
    by_role: dict[str, list[dict[str, Any]]], *, manual_group: bool
) -> list[str]:
    refs = {
        _key(_fields(item).get("orderNo") or _fields(item).get("salesOrderNo"))
        for docs in by_role.values()
        for item in docs
        if _key(_fields(item).get("orderNo") or _fields(item).get("salesOrderNo"))
    }
    if manual_group and len(refs) > 1:
        return ["HEADER_REFERENCE_CONFLICT"]
    return []


def run_one_to_many(
    classified: list[dict[str, Any]],
    *,
    complete_set: bool = False,
    business_group_id: Optional[str] = None,
) -> dict[str, Any]:
    """保留全部三角色单据，逐行绑定到订单并累计数量/金额。"""

    items = _filter_group(classified, business_group_id)
    by_role = {
        role: [item for item in items if _role(item) == role]
        for role in ("order", "receipt", "invoice")
    }
    role_files = {
        role: [_text(item.get("file_name")) for item in docs]
        for role, docs in by_role.items()
    }
    by_role, duplicate_evidence_files = _deduplicate_role_docs(by_role)
    amount_basis: dict[str, set[str]] = {
        "order": set(),
        "receipt": set(),
        "invoice": set(),
    }

    order_lines: list[dict[str, Any]] = []
    for doc_index, item in enumerate(by_role["order"]):
        for line_index, line in enumerate(_lines(item, "order")):
            order_amount, order_basis = _amount_with_basis(line)
            if order_basis:
                amount_basis["order"].add(order_basis)
            order_lines.append(
                {
                    "id": f"{_text(item.get('file_name'))}:{_line_id(line, line_index)}",
                    "file": _text(item.get("file_name")),
                    "order_ref": _key(_fields(item).get("orderNo") or _fields(item).get("salesOrderNo")),
                    "line": line,
                    "ordered_qty": _qty(line),
                    "ordered_qty_missing": _qty_missing(line),
                    "order_amount": order_amount,
                    "received_qty": ZERO,
                    "invoiced_qty": ZERO,
                    "received_qty_missing": False,
                    "invoiced_qty_missing": False,
                    "received_amount": ZERO,
                    "invoiced_amount": ZERO,
                    "received_amount_evidence": False,
                    "invoiced_amount_evidence": False,
                    "receipt_files": set(),
                    "invoice_files": set(),
                }
            )

    allocations: list[LineAllocation] = []
    used_source_lines: set[tuple[str, str, str]] = set()
    global_flags = _header_reference_flags(
        by_role, manual_group=bool(business_group_id)
    )
    for role in ("receipt", "invoice"):
        for item in by_role[role]:
            file_name = _text(item.get("file_name"))
            for line_index, line in enumerate(_lines(item, role)):
                source_line_id = _line_id(line, line_index)
                source_key = (file_name, source_line_id, role)
                if source_key in used_source_lines:
                    global_flags.append("DUPLICATE_SOURCE_LINE")
                    allocations.append(
                        LineAllocation(
                            source_file=file_name,
                            source_line_id=source_line_id,
                            source_role=role,
                            order_line_id=None,
                            qty=ZERO,
                            amount=None,
                            bind_status="REJECTED",
                            bind_rank=None,
                            basis=["同一来源文件和来源行已经分配"],
                            review_status="REJECTED",
                            rejected_reason="DUPLICATE_SOURCE_LINE",
                        )
                    )
                    continue
                item_fields = _fields(item)
                target, rank, basis = _find_order_line(
                    line,
                    order_lines,
                    item_fields.get("orderNo") or item_fields.get("salesOrderNo"),
                )
                quantity = _qty(line)
                amount, source_basis = _amount_with_basis(line)
                if source_basis:
                    amount_basis[role].add(source_basis)
                if target is None:
                    bind_status = "AMBIGUOUS" if len(order_lines) > 1 else "UNBOUND"
                    if "STRONG_MODEL_CONFLICT" in basis:
                        global_flags.append("STRONG_MODEL_CONFLICT")
                    else:
                        global_flags.append(
                            "AMBIGUOUS_LINK" if bind_status == "AMBIGUOUS" else "UNBOUND"
                        )
                    allocations.append(
                        LineAllocation(
                            source_file=file_name,
                            source_line_id=source_line_id,
                            source_role=role,
                            order_line_id=None,
                            qty=quantity,
                            amount=amount,
                            bind_status=bind_status,
                            bind_rank=None,
                            basis=basis,
                            review_status="REQUIRES_REVIEW",
                        )
                    )
                    continue
                used_source_lines.add(source_key)
                if role == "receipt":
                    target["received_qty"] += quantity
                    target["received_qty_missing"] = target["received_qty_missing"] or _qty_missing(line)
                    target["received_amount"] += amount or ZERO
                    target["received_amount_evidence"] = (
                        target["received_amount_evidence"] or amount is not None
                    )
                    target["receipt_files"].add(file_name)
                else:
                    target["invoiced_qty"] += quantity
                    target["invoiced_qty_missing"] = target["invoiced_qty_missing"] or _qty_missing(line)
                    target["invoiced_amount"] += amount or ZERO
                    target["invoiced_amount_evidence"] = (
                        target["invoiced_amount_evidence"] or amount is not None
                    )
                    target["invoice_files"].add(file_name)
                allocations.append(
                    LineAllocation(
                        source_file=file_name,
                        source_line_id=source_line_id,
                        source_role=role,
                        order_line_id=target["id"],
                        qty=quantity,
                        amount=amount,
                        bind_status="UNIQUE",
                        bind_rank=rank,
                        basis=basis,
                        review_status="AUTO",
                    )
                )

    rows: list[dict[str, Any]] = []
    for row in order_lines:
        ordered = row["ordered_qty"]
        received = row["received_qty"]
        invoiced = row["invoiced_qty"]
        flags: list[str] = []
        quantity_missing = bool(
            row.get("ordered_qty_missing")
            or row.get("received_qty_missing")
            or row.get("invoiced_qty_missing")
        )
        if quantity_missing:
            flags.append("QUANTITY_EVIDENCE_MISSING")
        elif received > ordered:
            flags.append("OVER_RECEIPT")
        elif ordered > ZERO and received < ordered:
            flags.append("PARTIAL_FULFILLMENT")
        if not quantity_missing:
            if invoiced > received:
                flags.append("OVER_INVOICE_QTY")
            elif received > ZERO and invoiced < received:
                flags.append("PARTIAL_INVOICE")
        order_amount = row["order_amount"]
        if order_amount is not None and row["received_amount_evidence"]:
            if row["received_amount"] > order_amount:
                flags.append("OVER_RECEIPT_AMT")
            elif ZERO < row["received_amount"] < order_amount:
                flags.append("PARTIAL_RECEIPT_AMT")
        if order_amount is not None and row["invoiced_amount_evidence"]:
            if row["invoiced_amount"] > order_amount:
                flags.append("OVER_INVOICE_AMT")
            elif ZERO < row["invoiced_amount"] < order_amount:
                flags.append("PARTIAL_INVOICE_AMT")
        if "DUPLICATE_SOURCE_LINE" in global_flags:
            flags.append("DUPLICATE_SOURCE_LINE")
        if "AMBIGUOUS_LINK" in global_flags:
            flags.append("AMBIGUOUS_LINK")
        elif "UNBOUND" in global_flags:
            flags.append("UNBOUND")
        if "STRONG_MODEL_CONFLICT" in global_flags:
            flags.append("STRONG_MODEL_CONFLICT")

        hard_flags = {
            "OVER_RECEIPT",
            "OVER_INVOICE_QTY",
            "OVER_RECEIPT_AMT",
            "OVER_INVOICE_AMT",
            "DUPLICATE_SOURCE_LINE",
        }
        partial_flags = {
            "PARTIAL_FULFILLMENT",
            "PARTIAL_INVOICE",
            "PARTIAL_RECEIPT_AMT",
            "PARTIAL_INVOICE_AMT",
        }
        review_flags = {"AMBIGUOUS_LINK", "UNBOUND", "STRONG_MODEL_CONFLICT", "QUANTITY_EVIDENCE_MISSING"}
        flag_set = set(flags)
        if flag_set & hard_flags:
            light = "RED"
        elif complete_set and flag_set & partial_flags:
            flags.append("SET_CLAIMED_INCOMPLETE")
            light = "RED"
        elif flag_set & (partial_flags | review_flags):
            light = "YELLOW"
        elif ordered == received == invoiced:
            light = "GREEN"
        else:
            light = "YELLOW"
        rows.append(
            {
                "order_line_id": row["id"],
                "order_file": row["file"],
                "ordered_qty": None if row.get("ordered_qty_missing") else _decimal_text(ordered),
                "received_qty": None if row.get("received_qty_missing") else _decimal_text(received),
                "invoiced_qty": None if row.get("invoiced_qty_missing") else _decimal_text(invoiced),
                "order_amount": None if row["order_amount"] is None else _decimal_text(row["order_amount"]),
                "received_amount": _decimal_text(row["received_amount"]),
                "invoiced_amount": _decimal_text(row["invoiced_amount"]),
                "complete_set": complete_set,
                "light": light,
                "flags": flags,
                "diffs": {
                    "received_minus_ordered": _decimal_text(received - ordered),
                    "invoiced_minus_received": _decimal_text(invoiced - received),
                    "received_amount_minus_order": (
                        None
                        if order_amount is None or not row["received_amount_evidence"]
                        else _decimal_text(row["received_amount"] - order_amount)
                    ),
                    "invoiced_amount_minus_order": (
                        None
                        if order_amount is None or not row["invoiced_amount_evidence"]
                        else _decimal_text(row["invoiced_amount"] - order_amount)
                    ),
                },
                "receipt_files": sorted(row["receipt_files"]),
                "invoice_files": sorted(row["invoice_files"]),
            }
        )

    ordered_total = sum((row["ordered_qty"] for row in order_lines), ZERO)
    received_total = sum((row["received_qty"] for row in order_lines), ZERO)
    invoiced_total = sum((row["invoiced_qty"] for row in order_lines), ZERO)
    ordered_amount_total = sum(
        (row["order_amount"] or ZERO for row in order_lines), ZERO
    )
    received_amount_total = sum(
        (row["received_amount"] for row in order_lines), ZERO
    )
    invoiced_amount_total = sum(
        (row["invoiced_amount"] for row in order_lines), ZERO
    )
    row_lights = [row["light"] for row in rows]
    if not rows:
        light = "NOT_TESTED"
    elif "RED" in row_lights:
        light = "RED"
    elif "YELLOW" in row_lights or global_flags:
        light = "YELLOW"
    elif rows:
        light = "GREEN"
    else:
        light = "YELLOW"
    all_flags = list(dict.fromkeys([
        *global_flags,
        *(flag for row in rows for flag in row["flags"]),
    ]))
    if rows and any(
        row.get("ordered_qty_missing") or row.get("received_qty_missing") or row.get("invoiced_qty_missing")
        for row in order_lines
    ):
        summary = "存在缺失的行级数量证据，未用 0 替代，累计结果等待复核"
    elif rows:
        summary = (
            f"订单 {_decimal_text(ordered_total)}，累计签收 {_decimal_text(received_total)}，"
            f"累计开票 {_decimal_text(invoiced_total)}"
        )
    else:
        summary = "未提取到可累计的订单行，履约累计未测"
    quantity_roles = (
        {
            "ordered_qty": None if any(row.get("ordered_qty_missing") for row in order_lines) else float(ordered_total),
            "received_qty": None if any(row.get("received_qty_missing") for row in order_lines) else float(received_total),
            "invoiced_qty": None if any(row.get("invoiced_qty_missing") for row in order_lines) else float(invoiced_total),
        }
        if rows
        else {}
    )
    amount_roles = (
        {
            "ordered_amount": float(ordered_amount_total),
            "received_amount": float(received_amount_total),
            "invoiced_amount": float(invoiced_amount_total),
        }
        if rows
        else {}
    )
    return {
        "complete_set": complete_set,
        "light": light,
        "flags": all_flags,
        "role_files": role_files,
        "duplicate_evidence_files": duplicate_evidence_files,
        "amount_basis": {
            role: sorted(bases) for role, bases in amount_basis.items()
        },
        "allocations": [allocation.to_dict() for allocation in allocations],
        "rows": rows,
        "summary": summary,
        "quantity_roles": quantity_roles,
        "amount_roles": amount_roles,
    }

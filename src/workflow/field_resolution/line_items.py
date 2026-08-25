"""Explainable line-item grouping for one-to-one and one-to-many evidence."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.workflow.field_resolution.normalizers import normalize_goods, normalize_unit, parse_decimal


ZERO = Decimal("0")


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if row.get(key) not in (None, ""):
            return row.get(key)
    return None


def _decimal(value: Any) -> Decimal | None:
    return parse_decimal(value)


def _decimal_text(value: Decimal | None) -> str:
    if value is None:
        return ""
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def extract_line_nodes(document: dict[str, Any]) -> list[dict[str, Any]]:
    fields = document.get("fields") if isinstance(document.get("fields"), dict) else {}
    rows = fields.get("items") if isinstance(fields.get("items"), list) else []
    if not rows and _first(fields, "quantity", "qty") not in (None, ""):
        rows = [fields]
    evidence_nodes = [
        node
        for node in list(document.get("field_evidence_nodes") or [])
        if isinstance(node, dict) and node.get("evidence_id") and node.get("usable_for_decision")
    ]
    lines: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            continue
        line_id = str(_first(raw, "line_id", "poLineId", "lineNo") or f"L{index + 1}")
        line_prefix = f"items.{index}."
        evidence_ids = [
            str(node.get("evidence_id"))
            for node in evidence_nodes
            if str(node.get("field_key") or "").startswith(line_prefix)
        ]
        if len(rows) == 1 and not evidence_ids:
            evidence_ids = [str(node.get("evidence_id")) for node in evidence_nodes]
        lines.append(
            {
                "line_id": line_id,
                "document_id": str(document.get("file_fingerprint") or document.get("file_name") or ""),
                "document_role": str(document.get("doc_type") or "other"),
                "item_code": str(_first(raw, "item_code", "itemCode", "materialCode") or ""),
                "goods_name": str(_first(raw, "goods_name", "goodsName", "productName", "itemName") or ""),
                "model": str(_first(raw, "model", "specification", "specModel", "goodsModel") or ""),
                "quantity": _first(raw, "quantity", "qty"),
                "unit": str(_first(raw, "unit", "quantityUnit") or ""),
                "total_amount": _first(raw, "total_amount", "totalAmount", "grossAmount", "amountWithTax"),
                "net_amount": _first(raw, "net_amount", "amount", "netAmount"),
                "tax_amount": _first(raw, "tax_amount", "taxAmount"),
                "unit_price": _first(raw, "unit_price", "unitPrice", "unitPriceExclTax"),
                "evidence_ids": list(evidence_ids),
            }
        )
    return lines


def _line_key(line: dict[str, Any], key: str) -> str:
    if key == "item_code":
        return normalize_goods(line.get("item_code"))
    if key == "model":
        return normalize_goods(line.get("model"))
    return normalize_goods(line.get("goods_name"))


def _match_candidates(source: dict[str, Any], orders: list[dict[str, Any]]) -> tuple[list[int], list[str]]:
    for key, label in (("item_code", "物料编码"), ("model", "规格型号"), ("goods_name", "货品名称")):
        source_key = _line_key(source, key)
        if not source_key:
            continue
        indexes = [index for index, order in enumerate(orders) if _line_key(order, key) == source_key]
        if indexes:
            return indexes, [f"{label}归一化一致"]
        if key in {"item_code", "model"} and any(_line_key(order, key) for order in orders):
            code = "STRONG_ITEM_CODE_CONFLICT" if key == "item_code" else "STRONG_MODEL_CONFLICT"
            return [], [code]
    if len(orders) == 1:
        return [0], ["候选范围内唯一订单行"]
    return [], ["未找到可唯一解释的订单行"]


def _gross(line: dict[str, Any]) -> tuple[Decimal | None, str]:
    total = _decimal(line.get("total_amount"))
    if total is not None:
        return total, _decimal_text(total)
    net = _decimal(line.get("net_amount"))
    tax = _decimal(line.get("tax_amount"))
    if net is not None and tax is not None:
        gross = net + tax
        quantity = _decimal(line.get("quantity"))
        unit_price = _decimal(line.get("unit_price"))
        if quantity is not None and unit_price is not None and quantity * unit_price == net:
            return gross, (
                f"{_decimal_text(quantity)} × {_decimal_text(unit_price)} + "
                f"{_decimal_text(tax)} = {_decimal_text(gross)}"
            )
        return gross, f"{_decimal_text(net)} + {_decimal_text(tax)} = {_decimal_text(gross)}"
    quantity = _decimal(line.get("quantity"))
    unit_price = _decimal(line.get("unit_price"))
    if quantity is not None and unit_price is not None:
        gross = quantity * unit_price
        return gross, f"{_decimal_text(quantity)} × {_decimal_text(unit_price)} = {_decimal_text(gross)}"
    return None, ""


def _quantity_calculation(
    receipt_lines: list[dict[str, Any]], order_qty: Decimal | None, invoice_lines: list[dict[str, Any]], unit: str
) -> str:
    receipt_values = [_decimal(line.get("quantity")) for line in receipt_lines]
    invoice_values = [_decimal(line.get("quantity")) for line in invoice_lines]
    if order_qty is None or any(value is None for value in [*receipt_values, *invoice_values]):
        return "数量证据不完整，未执行零值替代或累计判断"
    invoice_total = sum((value for value in invoice_values if value is not None), ZERO)
    receipt_expr = " + ".join(f"{_decimal_text(value)}{unit}" for value in receipt_values) or f"0{unit}"
    return f"{receipt_expr} = {_decimal_text(order_qty)}{unit}；发票{_decimal_text(invoice_total)}{unit}"


def match_line_groups(
    order_lines: list[dict[str, Any]],
    receipt_lines: list[dict[str, Any]],
    invoice_lines: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group source lines without greedy tie-breaking and retain every calculation."""
    orders = [dict(line) for line in order_lines]
    assignments: list[dict[str, list[dict[str, Any]]]] = [
        {"receipt": [], "invoice": [], "basis": []} for _ in orders
    ]
    ambiguous_by_order: list[bool] = [False for _ in orders]
    strong_conflicts: list[str] = []
    for role, lines in (("receipt", receipt_lines), ("invoice", invoice_lines)):
        for source in lines:
            candidates, basis = _match_candidates(source, orders)
            if len(candidates) == 1:
                target = candidates[0]
                assignments[target][role].append(dict(source))
                assignments[target]["basis"].extend(basis)
            elif len(candidates) > 1:
                for target in candidates:
                    ambiguous_by_order[target] = True
                    assignments[target]["basis"].append("存在多个同等级订单行候选")
            elif any(str(reason).startswith("STRONG_") for reason in basis):
                strong_conflicts.extend(str(reason) for reason in basis)

    groups: list[dict[str, Any]] = []
    for index, order in enumerate(orders):
        receipts = assignments[index]["receipt"]
        invoices = assignments[index]["invoice"]
        order_qty = _decimal(order.get("quantity"))
        receipt_values = [_decimal(line.get("quantity")) for line in receipts]
        invoice_values = [_decimal(line.get("quantity")) for line in invoices]
        receipt_qty = (
            None if any(value is None for value in receipt_values)
            else sum((value for value in receipt_values if value is not None), ZERO)
        )
        invoice_qty = (
            None if any(value is None for value in invoice_values)
            else sum((value for value in invoice_values if value is not None), ZERO)
        )
        reason_codes: list[str] = []
        reason_codes.extend(strong_conflicts)

        units = {
            normalize_unit(line.get("unit"))
            for line in [order, *receipts, *invoices]
            if normalize_unit(line.get("unit"))
        }
        if len(units) > 1:
            unit_result = "FAIL"
            reason_codes.append("UNIT_MISMATCH")
        elif units:
            unit_result = "PASS"
        else:
            unit_result = "REVIEW"
        unit = normalize_unit(order.get("unit")) or (next(iter(units)) if units else "件")

        if ambiguous_by_order[index]:
            quantity_result = "REVIEW"
            reason_codes.append("AMBIGUOUS_LINE_ASSIGNMENT")
        elif not receipts or not invoices:
            quantity_result = "REVIEW"
            reason_codes.append("LINE_ROLE_EVIDENCE_MISSING")
        elif order_qty is None or receipt_qty is None or invoice_qty is None:
            quantity_result = "REVIEW"
            reason_codes.append("LINE_QUANTITY_EVIDENCE_MISSING")
        elif order_qty == receipt_qty == invoice_qty:
            quantity_result = "PASS"
        else:
            quantity_result = "FAIL"
            if receipt_qty < order_qty or invoice_qty < order_qty:
                reason_codes.append("QUANTITY_SHORTAGE")
            if receipt_qty > order_qty or invoice_qty > order_qty:
                reason_codes.append("QUANTITY_OVERAGE")
            if receipt_qty != invoice_qty:
                reason_codes.append("RECEIPT_INVOICE_QUANTITY_MISMATCH")

        order_gross, order_formula = _gross(order)
        invoice_amounts = [_gross(line) for line in invoices]
        invoice_total = sum((amount or ZERO for amount, _ in invoice_amounts), ZERO) if invoices else None
        if order_gross is None or invoice_total is None or any(amount is None for amount, _ in invoice_amounts):
            amount_result = "NOT_TESTED"
            amount_calculation = ""
        elif order_gross == invoice_total:
            amount_result = "PASS"
            if len(invoice_amounts) == 1 and invoice_amounts[0][1].count("=") == 1:
                amount_calculation = invoice_amounts[0][1]
            else:
                amount_calculation = f"发票累计{_decimal_text(invoice_total)} = 订单{_decimal_text(order_gross)}"
        else:
            amount_result = "FAIL"
            reason_codes.append("AMOUNT_FORMULA_MISMATCH")
            amount_calculation = f"发票累计{_decimal_text(invoice_total)} ≠ 订单{_decimal_text(order_gross)}"

        evidence_ids = list(
            dict.fromkeys(
                str(evidence_id)
                for line in [order, *receipts, *invoices]
                for evidence_id in list(line.get("evidence_ids") or [])
                if str(evidence_id)
            )
        )
        groups.append(
            {
                "order_line_id": str(order.get("line_id") or f"L{index + 1}"),
                "order_line": order,
                "receipt_lines": receipts,
                "invoice_lines": invoices,
                "match_keys": list(dict.fromkeys(assignments[index]["basis"])),
                "evidence_ids": evidence_ids,
                "ordered_quantity": _decimal_text(order_qty),
                "received_quantity": _decimal_text(receipt_qty),
                "invoiced_quantity": _decimal_text(invoice_qty),
                "unit": unit,
                "unit_result": unit_result,
                "quantity_result": quantity_result,
                "amount_result": amount_result,
                "calculation": _quantity_calculation(receipts, order_qty, invoices, unit),
                "amount_calculation": amount_calculation or order_formula,
                "reason_codes": list(dict.fromkeys(reason_codes)),
            }
        )
    return groups


__all__ = ["extract_line_nodes", "match_line_groups"]

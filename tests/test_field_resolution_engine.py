from src.workflow.field_resolution.evidence_inventory import build_document_evidence
from src.workflow.field_resolution.normalizers import (
    normalize_address,
    normalize_legal_entity,
    normalize_unit,
    parse_decimal,
)
from src.workflow.field_resolution.resolution_engine import resolve_rule_edges


def _doc(role: str, fields: dict, raw_text: str | None = None) -> dict:
    if raw_text is None:
        labels = {
            "orderNo": "订单号",
            "quantity": "数量",
            "amount": "金额",
            "taxAmount": "税额",
            "totalAmount": "价税合计",
            "documentDate": "单据日期",
            "acceptanceDate": "验收日期",
        }
        raw_text = "\n".join(
            f"{labels.get(key, key)} {value}" for key, value in fields.items()
        )
    return {
        "file_name": f"{role}.pdf",
        "doc_type": role,
        "fields": fields,
        "raw_text": raw_text,
    }


def _resolve(documents: list[dict]) -> list[dict]:
    evidence = [node for document in documents for node in build_document_evidence(document)]
    return resolve_rule_edges(documents, evidence, {})


def _edge(edges: list[dict], concept: str) -> dict:
    return next(item for item in edges if item["concept"] == concept)


def test_rule_edge_explains_exact_quantity_match() -> None:
    documents = [
        _doc("order", {"quantity": 20}),
        _doc("receipt", {"quantity": 20}),
        _doc("invoice", {"quantity": 20}),
    ]
    edge = _edge(_resolve(documents), "quantity")
    assert edge["relation_type"] == "EXACT_EQUAL"
    assert edge["status"] == "CONFIRMED"
    assert edge["confirmed_facts"] == ["订单数量20台", "签收数量20台", "发票数量20台"]


def test_same_customer_name_cannot_override_different_tax_ids() -> None:
    documents = [
        _doc("order", {"buyerName": "宁波海岳机电有限公司", "buyerTaxId": "91330212AAA"}),
        _doc("invoice", {"buyerName": "宁波海岳机电有限公司", "buyerTaxId": "91330212BBB"}),
    ]
    edge = _edge(_resolve(documents), "buyer_identity")
    assert edge["status"] == "CONFLICT"
    assert edge["counter_evidence"][0]["reason_code"] == "TAX_ID_CONFLICT"


def test_normalizers_record_business_equivalence_without_unsafe_numeric_fuzziness() -> None:
    assert normalize_legal_entity("华东智造设备（上海）有限公司") == "华东智造设备上海有限公司"
    assert normalize_address("上海市，浦东新区 张江路88号") == "上海市浦东新区张江路88号"
    assert normalize_unit("臺") == "台"
    assert parse_decimal("￥113,000.00") == parse_decimal("113000")


def test_normalized_address_currency_and_unit_edges_explain_transformations() -> None:
    documents = [
        _doc("order", {"buyerAddress": "宁波市，鄞州区 学士路98号", "currency": "RMB", "unit": "臺"}),
        _doc("receipt", {"deliveryAddress": "宁波市鄞州区学士路 98 号", "currency": "CNY", "unit": "台"}),
    ]
    edges = _resolve(documents)
    assert _edge(edges, "buyer_address")["status"] == "CONFIRMED"
    assert "移除空格和标点" in _edge(edges, "buyer_address")["transformations"]
    assert _edge(edges, "currency")["status"] == "CONFIRMED"
    assert _edge(edges, "unit")["status"] == "CONFIRMED"


def test_exact_order_reference_is_confirmed_but_short_numeric_suffix_is_not() -> None:
    exact = [
        _doc("order", {"orderNo": "SO-251209-7214"}),
        _doc("receipt", {"orderNo": "SO-251209-7214"}),
        _doc("invoice", {"orderNo": "SO-251209-7214"}),
    ]
    assert _edge(_resolve(exact), "order_reference")["status"] == "CONFIRMED"

    unsafe = [
        _doc("order", {"orderNo": "SO-251209-7214"}),
        _doc("receipt", {"orderNo": "YS-260102-7214"}),
        _doc("invoice", {"orderNo": "FP-260102-7214"}),
    ]
    edge = _edge(_resolve(unsafe), "order_reference")
    assert edge["status"] == "CONFLICT"
    assert edge["reason_code"] == "IDENTIFIER_MISMATCH"


def test_unlocated_or_missing_evidence_never_auto_confirms() -> None:
    documents = [
        _doc("order", {"totalAmount": 113000}, raw_text=""),
        _doc("invoice", {"totalAmount": 113000}, raw_text=""),
    ]
    edge = _edge(_resolve(documents), "gross_amount")
    assert edge["status"] == "CANDIDATE"
    assert edge["reason_code"] == "DECISION_EVIDENCE_MISSING"

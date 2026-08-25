from src.workflow.field_resolution.evidence_inventory import (
    attach_document_evidence,
    build_document_evidence,
    evidence_for_field,
)


def test_inventory_anchors_exact_field_value_in_raw_text() -> None:
    doc = {
        "file_name": "order.pdf",
        "doc_type": "order",
        "raw_text": "货物名称 伺服电机\n规格型号 SM-130\n数量 20 台",
        "fields": {"goodsName": "伺服电机", "model": "SM-130", "quantity": 20},
    }
    nodes = build_document_evidence(doc)
    goods = next(x for x in nodes if x["field_key"] == "goodsName")
    assert goods["excerpt"] == "伺服电机"
    assert goods["char_start"] < goods["char_end"]
    assert goods["document_role"] == "order"
    assert goods["anchor_status"] == "ANCHORED"
    assert goods["usable_for_decision"] is True


def test_inventory_prefers_matching_text_block_location() -> None:
    doc = {
        "file_name": "receipt.pdf",
        "doc_type": "receipt",
        "raw_text": "验收货物：伺服电机 SM-130",
        "text_blocks": [
            {"text": "验收货物：伺服电机 SM-130", "page": 2, "bbox": [10, 20, 200, 40], "source": "ocr_line"}
        ],
        "fields": {"goodsName": "伺服电机"},
    }
    node = build_document_evidence(doc)[0]
    assert node["page"] == 3
    assert node["bbox"] == [10.0, 20.0, 200.0, 40.0]
    assert node["metadata"]["block_source"] == "ocr_line"


def test_inventory_keeps_unlocated_candidate_but_marks_it_invalid_for_decisions() -> None:
    doc = {
        "file_name": "scan.pdf",
        "doc_type": "invoice",
        "raw_text": "",
        "fields": {"totalAmount": 113000},
    }
    node = build_document_evidence(doc)[0]
    assert node["anchor_status"] == "UNLOCATED"
    assert node["usable_for_decision"] is False
    assert node["excerpt"] == ""


def test_inventory_keeps_dynamic_field_outside_fixed_catalog() -> None:
    doc = {
        "file_name": "bill-of-lading.pdf",
        "doc_type": "other",
        "raw_text": "Vessel EVER GIVEN",
        "fields": {"vesselName": "EVER GIVEN", "_private": "ignore"},
    }
    assert [node["field_key"] for node in build_document_evidence(doc)] == ["vesselName"]


def test_generic_number_without_label_context_is_not_decision_usable() -> None:
    doc = {
        "file_name": "order.pdf",
        "doc_type": "order",
        "raw_text": "内部流水第20号；字段尚未标注。",
        "fields": {"quantity": 20},
    }

    node = build_document_evidence(doc)[0]

    assert node["anchor_status"] == "ANCHORED"
    assert node["usable_for_decision"] is False
    assert node["metadata"]["reason_code"] == "AMBIGUOUS_TEXT_ONLY_ANCHOR"


def test_numeric_anchor_prefers_labeled_value_over_digits_inside_order_number() -> None:
    doc = {
        "file_name": "order.pdf",
        "doc_type": "order",
        "raw_text": "订单号 SO-251209-7214\n数量 20 台",
        "fields": {"quantity": 20},
    }

    node = build_document_evidence(doc)[0]

    assert node["excerpt"] == "20"
    assert node["char_start"] == doc["raw_text"].index("20 台")
    assert node["usable_for_decision"] is True
    assert node["metadata"]["reason_code"] == "TEXT_WITH_FIELD_CONTEXT"


def test_normalized_amount_anchors_to_formatted_original_value() -> None:
    doc = {
        "file_name": "invoice.pdf",
        "doc_type": "invoice",
        "raw_text": "价税合计（小写） ¥113,000.00",
        "fields": {"totalAmount": "113000.0"},
    }

    node = build_document_evidence(doc)[0]

    assert node["raw_value"] == "¥113,000.00"
    assert node["normalized_value"] == "113000.0"
    assert node["excerpt"] == "¥113,000.00"
    assert node["usable_for_decision"] is True


def test_normalized_acceptance_date_prefers_nearest_labeled_original_date() -> None:
    raw_text = (
        "编制日期 2026年01月02日\n"
        "到货时间 2026年01月02日 09:00 验收完成 2026年01月03日 09:40"
    )
    doc = {
        "file_name": "receipt.pdf",
        "doc_type": "receipt",
        "raw_text": raw_text,
        "fields": {"acceptanceDate": "2026-01-03"},
    }

    node = build_document_evidence(doc)[0]

    assert node["raw_value"] == "2026年01月03日 09:40"
    assert node["normalized_value"] == "2026-01-03"
    assert node["char_start"] == raw_text.index("2026年01月03日")
    assert node["usable_for_decision"] is True


def test_iso_date_anchors_to_english_long_date() -> None:
    doc = {
        "file_name": "order-en.pdf",
        "doc_type": "order",
        "raw_text": "Order No. SO-251229-7498 Order Date 29 December 2025",
        "fields": {"documentDate": "2025-12-29"},
    }

    node = build_document_evidence(doc)[0]

    assert node["raw_value"] == "29 December 2025"
    assert node["normalized_value"] == "2025-12-29"
    assert node["usable_for_decision"] is True


def test_accepted_value_uses_original_raw_text_as_evidence() -> None:
    doc = {
        "file_name": "invoice.pdf",
        "doc_type": "invoice",
        "raw_text": "价税合计（小写）￥113,000.00",
        "fields": {"totalAmount": 113000},
        "_field_meta": {
            "totalAmount": {
                "raw_value": "￥113,000.00",
                "normalized_candidate": 113000,
                "accepted_value": 113000,
                "status": "ACCEPTED",
                "source": "manual",
                "extractor": "hitl",
            }
        },
    }
    node = build_document_evidence(doc)[0]
    assert node["raw_value"] == "￥113,000.00"
    assert node["normalized_value"] == 113000
    assert node["excerpt"] == "￥113,000.00"


def test_refreshing_one_field_preserves_prior_node_in_history() -> None:
    doc = {
        "file_name": "order.pdf",
        "doc_type": "order",
        "raw_text": "货品 伺服电机；修订名称 高精度伺服电机",
        "fields": {"goodsName": "伺服电机", "quantity": 20},
    }
    attach_document_evidence(doc)
    old = evidence_for_field(doc, "goodsName")[0]
    doc["fields"]["goodsName"] = "高精度伺服电机"
    attach_document_evidence(doc, changed_keys={"goodsName"})
    current = evidence_for_field(doc, "goodsName")[0]
    assert current["evidence_id"] != old["evidence_id"]
    assert doc["field_evidence_history"][-1]["evidence_id"] == old["evidence_id"]
    assert len(evidence_for_field(doc, "quantity")) == 1


def test_inventory_builds_separate_located_evidence_for_every_item_line() -> None:
    doc = {
        "file_name": "order.pdf",
        "doc_type": "order",
        "raw_text": "1 工业相机镜头 VL-50 10只\n2 视觉检测相机 VC-500 15台",
        "fields": {
            "items": [
                {"goodsName": "工业相机镜头", "model": "VL-50", "quantity": 10},
                {"goodsName": "视觉检测相机", "model": "VC-500", "quantity": 15},
            ]
        },
    }

    nodes = build_document_evidence(doc)

    item_nodes = [node for node in nodes if node["field_key"].startswith("items.")]
    assert {node["field_key"] for node in item_nodes} >= {
        "items.0.goodsName",
        "items.0.model",
        "items.0.quantity",
        "items.1.goodsName",
        "items.1.model",
        "items.1.quantity",
    }
    assert all(node["anchor_status"] == "ANCHORED" for node in item_nodes)

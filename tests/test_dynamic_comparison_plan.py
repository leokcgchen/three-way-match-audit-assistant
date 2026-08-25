from src.workflow.field_resolution.comparison_plan import aggregate_status, build_comparison_plan


def _doc(role: str, fields: dict) -> dict:
    labels = {
        "orderNo": "订单号",
        "documentNo": {
            "order": "订单编号",
            "receipt": "验收单号",
            "invoice": "发票号码",
        }.get(role, "单据编号"),
        "invoiceNo": "发票号码",
        "quantity": "数量",
        "totalAmount": "价税合计",
        "documentDate": "单据日期",
        "acceptanceDate": "验收日期",
    }
    raw_text = "\n".join(
        f"{labels.get(key, key)} {value}"
        for key, value in fields.items()
        if value not in (None, "")
    )
    return {
        "file_name": f"{role}.pdf",
        "doc_type": role,
        "fields": fields,
        "raw_text": raw_text,
    }


def _job(customer_codes: tuple[str, str] = ("KH-330212-0142", "KH-NB-0062")) -> dict:
    common = {
        "orderNo": "SO-251209-7214",
        "sellerName": "华东智造设备有限公司",
        "buyerName": "宁波海岳机电有限公司",
        "goodsName": "伺服电机",
        "model": "SM-130",
        "quantity": 20,
        "totalAmount": 113000,
    }
    return {
        "classified": [
            _doc("order", {**common, "customerCode": customer_codes[0], "documentNo": "SO-251209-7214", "documentDate": "2025-12-09"}),
            _doc("receipt", {**common, "customerCode": customer_codes[1], "documentNo": "YS-260102-005", "acceptanceDate": "2026-01-02T09:40"}),
            _doc("invoice", {**common, "invoiceNo": "FP-260102-8305", "documentDate": "2026-01-02T14:50"}),
        ],
        "gospd_sample_results": {"SO-251209-7214": {"cutoff_test": {"test_status": "PASS"}}},
        "period_end": "2025-12-31",
    }


def test_status_precedence_is_exact() -> None:
    assert aggregate_status(["PASS", "PASS_WITH_WARNING"]) == "PASS_WITH_WARNING"
    assert aggregate_status(["PASS_WITH_WARNING", "MISSING_EVIDENCE"]) == "MISSING_EVIDENCE"
    assert aggregate_status(["MISSING_EVIDENCE", "CONFLICT"]) == "CONFLICT"


def test_3962_like_plan_is_warning_only_for_customer_code_mapping() -> None:
    plan = build_comparison_plan(_job(), "SO-251209-7214")
    assert plan["three_way_status"] == "PASS_WITH_WARNING"
    assert plan["cutoff_status"] == "PASS"
    assert plan["overall_status"] == "PASS_WITH_WARNING"
    assert [issue["issue_code"] for issue in plan["domains"]["issues"]] == ["CUSTOMER_CODE_MAPPING_REQUIRED"]


def test_dates_are_chronology_not_equality_rows() -> None:
    plan = build_comparison_plan(_job(), "SO-251209-7214")
    consistency_concepts = {row["concept"] for row in plan["domains"]["consistency"]}
    assert "document_date" not in consistency_concepts
    assert "acceptance_date" not in consistency_concepts
    labels = [event["label"] for event in plan["domains"]["chronology"]["events"]]
    assert labels == ["订单日期", "验收/控制权转移", "开票日期"]


def test_document_specific_fields_do_not_change_three_way_status() -> None:
    job = _job(customer_codes=("KH-1", "KH-1"))
    baseline = build_comparison_plan(job, "SO-251209-7214")
    job["classified"][1]["fields"]["vehiclePlate"] = "沪A12345"
    job["classified"][1]["raw_text"] += "\n沪A12345"
    changed = build_comparison_plan(job, "SO-251209-7214")
    assert baseline["three_way_status"] == changed["three_way_status"] == "PASS"
    assert any(row["field_key"] == "vehiclePlate" for row in changed["domains"]["document_specific"])


def test_structured_reason_text_is_derived_from_edge_facts() -> None:
    plan = build_comparison_plan(_job(customer_codes=("KH-1", "KH-1")), "SO-251209-7214")
    quantity = next(row for row in plan["domains"]["consistency"] if row["concept"] == "quantity")
    assert quantity["result"] == "PASS"
    assert "订单数量20台" in quantity["reason_text"]
    assert quantity["evidence_ids"]

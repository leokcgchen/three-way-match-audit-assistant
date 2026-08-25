from src.workflow.test_diff_summary import list_match_rules, sample_diff_lines


def test_match_rules_catalog_has_field_pair_rules():
    rules = list_match_rules()
    ids = {r["rule_id"] for r in rules}
    assert "qty_order_vs_receipt" in ids
    assert "qty_receipt_vs_invoice" in ids
    assert "amount_order_vs_invoice" in ids
    assert "buyer_name_consistent" in ids
    assert "docs_same_business" in ids
    assert "required_docs_present" in ids
    assert "required_fields_present" in ids
    assert "date_order_ship_receipt_posting" in ids
    assert all(r.get("left") and r.get("right") for r in rules)


def test_sample_diff_lines_show_left_right_values():
    sample = {
        "three_way_match": {
            "status": "FAIL",
            "field_consistency": {
                "comparisons": [
                    {
                        "field_name": "quantity",
                        "is_consistent": False,
                        "order_value": 100,
                        "receipt_value": 98,
                        "invoice_value": 100,
                        "diff_description": "数量不一致",
                    }
                ]
            },
        },
        "cutoff_test": {
            "status": "FAIL",
            "result": {
                "控制权转移日": "2025-08-10",
                "入账日期": "2025-07-31",
                "问题描述": "跨期入账",
            },
        },
    }
    lines = sample_diff_lines(sample)
    assert any("订单数量 100" in x and "签收/验收数量 98" in x for x in lines)
    assert any("签收/控制权 2025-08-10" in x and "入账 2025-07-31" in x for x in lines)


def test_sample_diff_lines_explain_fulfillment_model_conflict():
    sample = {
        "three_way_match": {
            "status": "FAIL",
            "fulfillment": {
                "flags": [
                    "STRONG_MODEL_CONFLICT",
                    "PARTIAL_FULFILLMENT",
                    "OVER_INVOICE_QTY",
                ],
                "summary": "订单 10，累计签收 0，累计开票 10",
            },
        }
    }

    lines = sample_diff_lines(sample)

    assert lines
    assert "规格型号冲突" in lines[0]
    assert "订单 10，累计签收 0，累计开票 10" in lines[0]

from src.models.field_values import effective_fields, seed_field_meta
from src.workflow.amount_ambiguity import decide_ambiguity, list_open_ambiguities, scan_document
from src.workflow.field_catalog import amount_field_spec
from src.llm.qianfan_vision import _prompt


def test_multiple_total_candidates_needs_review():
    item = {
        "file_name": "inv.pdf",
        "doc_type": "invoice",
        "raw_text": "价税合计 ¥5,650.00\n总额 5700\n金额 5000 税额 650",
        "fields": {"totalAmount": 5650, "amount": 5000, "taxAmount": 650},
    }
    seed_field_meta(item, source="test")
    rows = scan_document(item)
    assert rows
    total_rows = [r for r in rows if r["field_key"] == "totalAmount"]
    assert total_rows
    assert total_rows[0]["status"] == "NEEDS_REVIEW"
    assert "MULTIPLE_CANDIDATES" in total_rows[0]["trigger_reasons"]


def test_reconciliation_failed_needs_review():
    item = {
        "file_name": "inv2.pdf",
        "doc_type": "invoice",
        "raw_text": "价税合计 ¥100.00",
        "fields": {"totalAmount": 100, "amount": 80, "taxAmount": 10},
    }
    seed_field_meta(item, source="test")
    rows = scan_document(item)
    assert rows
    assert any("RECONCILIATION_FAILED" in (r.get("trigger_reasons") or []) for r in rows)


def test_accept_candidate_writes_accepted_and_closes():
    item = {
        "file_name": "inv3.pdf",
        "doc_type": "invoice",
        "raw_text": "价税合计 ¥5,650.00\n总额 5700",
        "fields": {"totalAmount": 5650},
    }
    seed_field_meta(item, source="test")
    rows = scan_document(item)
    amb = next(r for r in rows if r["field_key"] == "totalAmount")
    cand = amb["candidates"][0]
    decide_ambiguity(
        item,
        amb["ambiguity_id"],
        decision="ACCEPT_CANDIDATE",
        candidate_id=cand["candidate_id"],
        reason="test",
    )
    closed = [r for r in item["_amount_ambiguities"] if r["ambiguity_id"] == amb["ambiguity_id"]][0]
    assert closed["status"] == "CONFIRMED"
    assert float(effective_fields(item)["totalAmount"]) == float(cand["value"])
    assert list_open_ambiguities({"classified": [item]}) == []


def test_consistent_single_total_no_open_card():
    item = {
        "file_name": "inv4.pdf",
        "doc_type": "invoice",
        "raw_text": "价税合计（小写）¥10942.90 金额 9683.98 税额 1258.92",
        "fields": {"totalAmount": 10942.90, "amount": 9683.98, "taxAmount": 1258.92},
    }
    seed_field_meta(item, source="test")
    rows = scan_document(item)
    open_rows = [r for r in rows if r["status"] in {"NEEDS_REVIEW", "INSUFFICIENT_EVIDENCE"}]
    assert open_rows == []


def test_role_collision_opens_amount_fields():
    """amount 被误当成价税合计时应开角色冲突。"""
    item = {
        "file_name": "inv5.png",
        "doc_type": "invoice",
        "raw_text": (
            "合计（不含税）：¥ 64,660.80 税额合计：¥ 8,405.90 "
            "价税合计（小写）：¥ 73,066.70 授信额度 80,000.00 可用额度 6,933.30 "
            "折扣前商品金额为 68,400.00"
        ),
        "fields": {"totalAmount": 73066.70, "amount": 73066.70, "taxAmount": 8405.90},
    }
    seed_field_meta(item, source="test")
    rows = scan_document(item)
    assert rows
    assert any("ROLE_COLLISION" in (r.get("trigger_reasons") or []) for r in rows)
    keys = {r["field_key"] for r in rows}
    assert "amount" in keys


def test_zero_tax_export_amount_equals_total_no_open_card():
    """0285：出口零税订单 amount=total、无税额，不应误报角色冲突/多金额。"""
    item = {
        "file_name": "SO25-0285_EXHT25-0285_02_销售订单.pdf",
        "doc_type": "order",
        "raw_text": (
            "不含税单价 不含税金额 价税合计\n"
            "505 39.00 1% 0%（出口零税率） 19,498.05 19,498.05\n"
            "订单价税合计 人民币 19,498.05 元"
        ),
        "fields": {"amount": 19498.05, "totalAmount": 19498.05, "taxAmount": None},
    }
    seed_field_meta(item, source="test")
    rows = scan_document(item)
    open_rows = [r for r in rows if r["status"] in {"NEEDS_REVIEW", "INSUFFICIENT_EVIDENCE"}]
    assert open_rows == []


def test_so250296_wrong_amount_few_candidates():
    """0296：误抽折扣前应开卡，但候选应精简（无行税额 300、无授信串扰）。"""
    text = (
        "合计（不含税）：　¥ 64,660.80　　　税额合计：　¥ 8,405.90\n"
        "折扣前商品金额为 68,400.00 元，商业折扣为 3,739.20 元，"
        "折后不含税金额为 64,660.80 元\n"
        "价税合计（小写）：　¥ 73,066.70\n"
        "不含税金额 300 税额 300\n"
        "授信额度 80,000.00 可用额度 6,933.30\n"
    )
    item = {
        "file_name": "SO25-0296_HT25-0296_05_增值税发票.png",
        "doc_type": "invoice",
        "raw_text": text,
        "fields": {"totalAmount": 73066.70, "amount": 68400.0, "taxAmount": 8405.90},
    }
    seed_field_meta(item, source="test")
    rows = scan_document(item)
    assert rows
    assert len(rows) == 1
    amount_row = next(r for r in rows if r["field_key"] == "amount")
    vals = {float(c["value"]) for c in amount_row["candidates"]}
    assert 68400.0 in vals
    assert 64660.80 in vals
    assert 300.0 not in vals
    assert 80000.0 not in vals
    assert len(amount_row["candidates"]) <= 5


def test_html_table_header_not_amount_candidate():
    from src.workflow.amount_ambiguity import _collect_labeled_candidates

    text = (
        '<tr><td>项目名称</td><td>规格型号</td><td>单位</td><td>数量</td>'
        '<td>不含税单价</td><td>不含税金额</td><td>税率</td><td>税额</td></tr>'
        "<tr><td>轮毂</td><td>A</td><td>批</td><td>300</td><td>225</td><td>300</td><td>13%</td><td>39</td></tr>"
        "价税合计（小写）：　¥ 73,066.70"
    )
    labeled = _collect_labeled_candidates(text)
    vals = {float(c["value"]) for c in labeled if c.get("role") == "tax_exclusive"}
    assert 300.0 not in vals
    tax_only = {float(c["value"]) for c in labeled if c.get("role") == "tax_only"}
    assert 300.0 not in tax_only
    from src.legacy_ocr.ocr_adapter import extract_fields_heuristically

    fields = extract_fields_heuristically(text)
    amt = fields.get("amount")
    if amt is not None:
        assert abs(float(str(amt).replace(",", "")) - 300.0) > 0.01


def test_so250296_labels_mined():
    text = (
        "合计（不含税）：　¥ 64,660.80　　　税额合计：　¥ 8,405.90\n"
        "折扣前商品金额为 68,400.00 元，商业折扣为 3,739.20 元，"
        "折后不含税金额为 64,660.80 元\n"
        "价税合计（小写）：　¥ 73,066.70\n"
        "授信额度 80,000.00 可用额度 6,933.30\n"
    )
    item = {
        "file_name": "SO25-0296_HT25-0296_05_增值税发票.png",
        "doc_type": "invoice",
        "raw_text": text,
        "fields": {"totalAmount": 80000.0, "amount": 64660.80, "taxAmount": 8405.90},
    }
    seed_field_meta(item, source="test")
    rows = scan_document(item)
    assert rows
    total = next(r for r in rows if r["field_key"] == "totalAmount")
    vals = {float(c["value"]) for c in total["candidates"]}
    assert 73066.7 in vals
    assert 80000.0 in vals


def test_vision_prompt_includes_field_semantics():
    prompt = _prompt(field_key="amount", candidates=[{"candidate_id": "C1", "value": 1}], ocr_text="")
    spec = amount_field_spec("amount")
    assert spec["field_name"] in prompt
    assert "不含税" in prompt or "折后" in prompt
    assert "授信" in prompt


def test_parse_ocr_thousand_dots_as_thousands():
    from src.legacy_ocr.amount_resolve import _parse_number

    assert _parse_number("9.683.98") == 9683.98
    assert _parse_number("9,683.98") == 9683.98
    assert _parse_number("9.68") == 9.68


def test_header_row_tax_inclusive_not_first_column():
    from src.workflow.amount_ambiguity import _collect_header_row_amounts

    text = "合计金额 合计税额 价税合计\n62,274.76 8,095.72 70,370.48"
    found = _collect_header_row_amounts(text)
    by_lab = {c["label"]: (c["value"], c["role"]) for c in found}
    assert by_lab["价税合计"][0] == 70370.48
    assert by_lab["价税合计"][1] == "tax_inclusive"
    assert by_lab["合计金额"][0] == 62274.76
    assert by_lab["合计金额"][1] == "tax_exclusive"
    item = {
        "file_name": "inv_header.pdf",
        "doc_type": "invoice",
        "raw_text": text,
        "fields": {"totalAmount": 70370.48, "amount": 62274.76, "taxAmount": 8095.72},
    }
    seed_field_meta(item, source="test")
    rows = scan_document(item)
    open_rows = [r for r in rows if r["status"] in {"NEEDS_REVIEW", "INSUFFICIENT_EVIDENCE"}]
    assert open_rows == []


def test_material_code_not_taken_as_net_amount():
    from src.workflow.amount_ambiguity import _collect_labeled_candidates, apply_ai_recommendation

    item = {
        "file_name": "02_SO25-0021_销售订单.pdf",
        "doc_type": "order",
        "raw_text": "不含税金额 -01357 合计（不含税） 62,274.76 税额 8,095.72 价税合计 70,370.48",
        "fields": {"amount": 62274.76, "taxAmount": 8095.72, "totalAmount": 70370.48},
    }
    seed_field_meta(item, source="test")
    labeled = _collect_labeled_candidates(item["raw_text"])
    nets = [c for c in labeled if c.get("role") == "tax_exclusive"]
    assert nets
    assert all(float(c["value"]) > 0 for c in nets)
    assert not any(abs(float(c["value"]) - 1357) < 0.01 for c in nets)
    assert any(abs(float(c["value"]) - 62274.76) < 0.01 for c in nets)

    rows = scan_document(item)
    amount_rows = [r for r in rows if r["field_key"] == "amount"]
    if amount_rows:
        row = amount_rows[0]
        apply_ai_recommendation(
            item,
            row["ambiguity_id"],
            {
                "recommended_candidate_id": "C1",
                "reason": "C1 的 -1357 是物料编码误识别，62274.76 才对，因此 C1 是最佳候选",
                "review_status": "RECOMMENDED",
            },
        )
        rec = next(
            r for r in item["_amount_ambiguities"] if r["ambiguity_id"] == row["ambiguity_id"]
        )
        rec_id = (rec.get("ai_recommendation") or {}).get("candidate_id")
        rec_val = None
        for c in rec.get("candidates") or []:
            if c.get("candidate_id") == rec_id:
                rec_val = float(c.get("value") or 0)
        if rec_id:
            assert rec_val is not None and rec_val > 0


def test_ocr_dot_fragment_not_multiple_when_recon_ok():
    item = {
        "file_name": "inv_frag.pdf",
        "doc_type": "invoice",
        "raw_text": "价税合计（小写）¥10942.90 金额 9683.98 税额 1258.92 9.68",
        "fields": {"totalAmount": 10942.90, "amount": 9683.98, "taxAmount": 1258.92},
    }
    seed_field_meta(item, source="test")
    rows = scan_document(item)
    open_rows = [r for r in rows if r["status"] in {"NEEDS_REVIEW", "INSUFFICIENT_EVIDENCE"}]
    assert open_rows == []


def test_tax_rate_and_qty_not_multiple_on_consistent_docs():
    """0281/0282：表头税率13%、数量、单价、账号不是多金额。"""
    order = {
        "file_name": "SO25-0281_order.pdf",
        "doc_type": "order",
        "raw_text": (
            "<td>不含税金额 (元)</td><td>税率 13%</td><td>价税合计 (元)</td></tr>"
            "<tr><td>MAT-05777</td><td>2</td>"
            "折后不含税价 9,683.98 元；增值税率 13%；税额 1,258.92 元；价税合计 10,942.90"
        ),
        "fields": {"amount": 9683.98, "taxAmount": 1258.92, "totalAmount": 10942.90, "quantity": 357},
    }
    invoice = {
        "file_name": "SO25-0281_inv.pdf",
        "doc_type": "invoice",
        "raw_text": (
            "不含税金额 税额 税率 中国工商银行工业园支行3200100000000001 "
            "折后不含税单价27.13元 商业折扣额为27.40元 折扣率为1% "
            "价税合计（小写）¥10,942.90 金额 9,683.98"
        ),
        "fields": {"amount": 9683.98, "totalAmount": 10942.90, "quantity": 357},
    }
    contract = {
        "file_name": "SO25-0282_ht.pdf",
        "doc_type": "contract",
        "raw_text": (
            "商业折扣率 3%）：折后不含税价 11,580.05 元；增值税率 13%；"
            "税额1,505.41元；价税合计 13,085.46元"
        ),
        "fields": {"amount": 11580.05, "taxAmount": 1505.41, "totalAmount": 13085.46, "quantity": 394},
    }
    inv2 = {
        "file_name": "SO25-0282_inv.pdf",
        "doc_type": "invoice",
        "raw_text": (
            "数量 折后不含税单价 不含税金额 税率 税额\n"
            "394 29.391 11,580.05 13% 1,505.41\n"
            "合计（不含税）：11,580.05 税额合计：1,505.41 价税合计：13,085.46"
        ),
        "fields": {"amount": 11580.05, "taxAmount": 1505.41, "totalAmount": 13085.46, "quantity": 394},
    }
    for item in (order, invoice, contract, inv2):
        seed_field_meta(item, source="test")
        rows = scan_document(item)
        open_rows = [r for r in rows if r["status"] in {"NEEDS_REVIEW", "INSUFFICIENT_EVIDENCE"}]
        assert open_rows == [], (
            item["file_name"],
            [
                (r["field_key"], r["trigger_reasons"], [c["value"] for c in r["candidates"]])
                for r in open_rows
            ],
        )


def test_skip_unit_price_and_discount_rate_labels():
    from src.workflow.amount_ambiguity import _collect_labeled_candidates

    labeled = _collect_labeled_candidates(
        "折后不含税单价 29.391 不含税金额 11,580.05 商业折扣率 3% 增值税率 13%"
    )
    nets = [c for c in labeled if c["role"] == "tax_exclusive"]
    assert any(abs(float(c["value"]) - 11580.05) < 0.01 for c in nets)
    assert not any(abs(float(c["value"]) - 29.391) < 0.01 for c in nets)
    assert not any(abs(float(c["value"]) - 13) < 0.01 for c in nets)
    discounts = [c for c in labeled if c["role"] == "discount"]
    assert not any(abs(float(c["value"]) - 3) < 0.01 for c in discounts)

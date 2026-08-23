from src.ui.field_highlight import _value_search_variants, locate_pdf_boxes
from pathlib import Path


def test_amount_and_date_variants():
    amts = _value_search_variants("10942.9")
    assert "10942.9" in amts
    assert "10,942.90" in amts or any("," in a for a in amts)
    dates = _value_search_variants("2025-12-05")
    assert any("2025年" in d and "月" in d for d in dates)


def test_long_invoice_no_not_treated_as_amount():
    inv = "25322025000000002811"
    vs = _value_search_variants(inv)
    assert inv in vs
    assert not any(v.endswith(".00") for v in vs)
    assert not any("," in v for v in vs)


def test_decimal_rate_percent_variants():
    vs = _value_search_variants("0.13")
    assert any("13%" in x for x in vs)
    vs2 = _value_search_variants("0.01")
    assert any("1%" in x for x in vs2)


def test_field_value_text_from_items_list():
    from src.ui.field_highlight import field_value_text

    items = [{"商品名称": "动力电池托盘冲压总成", "数量": "357"}]
    assert field_value_text(items) == "动力电池托盘冲压总成"


def test_clause_variants_prefer_pure_chinese_prefix():
    vs = _value_search_variants("发票开具之日起30日内以银行转账方式支付全部价款")
    assert any(v == "发票开具之日起" or (v.startswith("发票开具") and "30" not in v) for v in vs)


def test_locate_order_amount_on_mock_pdf():
    p = Path("data/mock/SO25-0281/SO25-0281_HT25-0281_02_销售订单.pdf")
    if not p.is_file():
        return
    hits = locate_pdf_boxes(p, "10942.9")
    assert hits, "应能用金额变体在订单 PDF 上定位价税合计"
    assert len(hits) <= 2
    for _, box in hits:
        w = box[2] - box[0]
        h = box[3] - box[1]
        assert w < 200 and h < 40, f"金额框过大: {box}"


def test_locate_payment_terms_on_contract_pdf():
    p = Path("data/mock/SO25-0281/SO25-0281_HT25-0281_01_销售合同.pdf")
    if not p.is_file():
        return
    hits = locate_pdf_boxes(p, "发票开具之日起30日内以银行转账方式支付全部价款")
    assert hits, "付款条款应能字符级定位（含中文+数字）"
    for _, box in hits:
        assert box[2] - box[0] < 400


def test_locate_invoice_fields_on_mock_pdf():
    base = Path("data/mock/SO25-0281")
    invs = list(base.glob("*发票*.pdf"))
    if not invs:
        return
    p = invs[0]
    assert locate_pdf_boxes(p, "10942.9"), "发票价税合计应可定位"
    assert locate_pdf_boxes(p, "25322025000000002811") or locate_pdf_boxes(p, "357"), (
        "发票号码或数量应可定位"
    )

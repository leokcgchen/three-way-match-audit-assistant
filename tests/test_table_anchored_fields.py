from src.legacy_ocr.ocr_adapter import extract_table_anchored_fields


def test_invoice_anchor_prevents_material_code_becoming_quantity_or_total() -> None:
    text = """
    货物或应税劳务名称 规格/物料编码 单位 数量 折后不含税单价 不含税金额 税率 税额
    动力电池托盘冲压总成 MAT-05777 件 357 27.1260 9,683.98 13% 1,258.92
    价税合计（大写） 人民币壹万零玖佰肆拾贰元玖角整 （小写） ¥ 10,942.90
    """
    actual = extract_table_anchored_fields(text, "invoice")
    assert actual == {
        "totalAmount": "10942.90",
        "quantity": "357",
        "unitPrice": "27.1260",
        "amount": "9683.98",
        "taxRate": "13",
        "taxAmount": "1258.92",
    }


def test_receipt_anchor_uses_table_quantity_not_material_code_tail() -> None:
    text = """
    序号 物料编码 商品名称 单位 发货数量 实收数量 外观及包装
    1 MAT-05777 动力电池托盘冲压总成 件 357 357 包装完好
    """
    actual = extract_table_anchored_fields(text, "warehouse_receipt")
    assert actual["quantity"] == "357"

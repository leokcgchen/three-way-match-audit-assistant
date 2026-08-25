from __future__ import annotations

from src.legacy_ocr.ocr_adapter import extract_table_anchored_fields


def test_english_invoice_keeps_own_and_related_identifiers_in_separate_roles() -> None:
    text = """
    COMMERCIAL INVOICE
    Seller: East China Intelligent Manufacturing Co., Ltd.
    Consignee: NordWerk Verpackung GmbH
    Invoice No.: CI-260119-0068
    S/C No.: SC-251226-3995
    Invoice Date: 2026-01-21 14:30
    Order No.: SO-251229-7498
    Currency: USD
    No. Description of Goods Model Qty. Unit Unit Price Amount
    1 Intelligent Packaging Line PKG-600 1 SET USD 86,000.00 USD 86,000.00
    Total Amount USD 86,000.00
    """

    fields = extract_table_anchored_fields(text, "invoice")

    assert fields["invoiceNo"] == "CI-260119-0068"
    assert fields["documentNo"] == "CI-260119-0068"
    assert fields["contractNo"] == "SC-251226-3995"
    assert fields["orderNo"] == "SO-251229-7498"
    assert fields["sellerName"] == "East China Intelligent Manufacturing Co., Ltd."
    assert fields["buyerName"] == "NordWerk Verpackung GmbH"
    assert fields["items"] == [
        {
            "goodsName": "Intelligent Packaging Line",
            "model": "PKG-600",
            "quantity": "1",
            "unit": "SET",
            "unitPrice": "86000.00",
            "amount": "86000.00",
        }
    ]


def test_english_receipt_does_not_turn_certificate_number_into_invoice_number() -> None:
    text = """
    GOODS RECEIPT AND ACCEPTANCE CERTIFICATE
    Certificate No.: YS-260120-057
    Order No.: SO-251229-7498
    Receipt and Acceptance Time: 2026-01-20 09:15
    Seller: East China Intelligent Manufacturing Co., Ltd.
    Buyer: NordWerk Verpackung GmbH
    Item Description Qty. Unit Unit Price Amount
    PKG-600 Intelligent Packaging Line PKG-600 1 SET USD 86,000.00 USD 86,000.00
    Total Accepted Amount USD 86,000.00
    """

    fields = extract_table_anchored_fields(text, "warehouse_receipt")

    assert fields["documentNo"] == "YS-260120-057"
    assert fields["receiptNo"] == "YS-260120-057"
    assert "invoiceNo" not in fields
    assert fields["orderNo"] == "SO-251229-7498"
    assert fields["sellerName"] == "East China Intelligent Manufacturing Co., Ltd."
    assert fields["buyerName"] == "NordWerk Verpackung GmbH"
    assert fields["acceptanceDateTime"] == "2026-01-20T09:15"
    assert fields["items"][0]["model"] == "PKG-600"
    assert fields["items"][0]["quantity"] == "1"


def test_chinese_order_extracts_every_goods_line_instead_of_only_first() -> None:
    text = """
    销售订单
    订单编号：SO-251226-7461 订单日期：2025年12月26日
    序号 货物名称 规格型号 物料编码 数量 单位 含税单价 价税合计
    1 工业相机镜头 VL-50 VL-50 10 只 ¥2,260.00 ¥22,600.00
    2 视觉检测相机 VC-500 VC-500 15 台 ¥11,300.00 ¥169,500.00
    3 视觉光源 VL-200 VL-200 20 套 ¥5,650.00 ¥113,000.00
    价税合计（小写） ¥305,100.00
    """

    fields = extract_table_anchored_fields(text, "purchase_order")

    assert [item["model"] for item in fields["items"]] == ["VL-50", "VC-500", "VL-200"]
    assert [item["quantity"] for item in fields["items"]] == ["10", "15", "20"]
    assert fields["totalAmount"] == "305100.00"


def test_chinese_receipt_extracts_every_line_and_allows_missing_goods_label() -> None:
    text = """
    签收验收单
    验收单号：YS-260118-081 关联订单号：SO-251226-7461
    物料编码 物料名称 规格型号 批次 发货数量 实收数量 单位 含税单价 含税金额
    MAT-VL50-01 工业相机镜头 VL-50 B251226-01 10 10 只 ¥2,260.00 ¥22,600.00
    MAT-VC500-02 视觉检测相机 VC-500 B251226-02 15 15 台 ¥11,300.00 ¥169,500.00
    MAT-VL200-03 视觉光源 VL-200 B251226-03 20 20 套 ¥5,650.00 ¥113,000.00
    MAT-CT40-01 CT-40 18 18 套 ¥5,650.00 ¥101,700.00
    含税金额合计 ¥406,800.00
    """

    fields = extract_table_anchored_fields(text, "warehouse_receipt")

    assert [item["model"] for item in fields["items"]] == ["VL-50", "VC-500", "VL-200", "CT-40"]
    assert [item["quantity"] for item in fields["items"]] == ["10", "15", "20", "18"]
    assert "goodsName" not in fields["items"][-1]


def test_product_conflict_is_preserved_instead_of_normalized_away() -> None:
    order = "1 机器视觉控制器 MVC-300 MVC-300 2 台 ¥56,500.00 ¥113,000.00"
    receipt = "MAT-MC300-01 运动控制器 MC-300 B260101-01 2 2 台 ¥56,500.00 ¥113,000.00"

    order_fields = extract_table_anchored_fields(order, "purchase_order")
    receipt_fields = extract_table_anchored_fields(receipt, "warehouse_receipt")

    assert order_fields["items"][0]["model"] == "MVC-300"
    assert receipt_fields["items"][0]["model"] == "MC-300"
    assert order_fields["items"][0]["goodsName"] == "机器视觉控制器"
    assert receipt_fields["items"][0]["goodsName"] == "运动控制器"


def test_actual_3995_header_row_invoice_and_wrapped_goods_are_extracted() -> None:
    text = """
    COMMERCIAL INVOICE
    Seller Consignee
    East China Intelligent Manufacturing Equipment Co., Ltd. NordWerk Verpackung GmbH
    Invoice No. S/C No. Trade Term
    CI-260119-0068 SC-251226-3995 CIP HAMBURG, GERMANY
    Invoice Date: 2026-01-21 14:30 Order No.: SO-251229-7498 Currency: USD
    Marks & Nos. Description of Goods Quantity Unit Price Amount G.W./N.W. Packages
    N/M INTELLIGENT 1 SET USD 86,000.00 USD 86,000.00 12,500 KGS 1 CASE
    PACKAGING LINE 11,800 KGS
    PKG-600
    Total Amount in Words Total Amount
    EIGHTY SIX THOUSAND US DOLLARS ONLY USD 86,000.00
    """

    fields = extract_table_anchored_fields(text, "invoice")

    assert fields["invoiceNo"] == "CI-260119-0068"
    assert fields["contractNo"] == "SC-251226-3995"
    assert fields["sellerName"] == "East China Intelligent Manufacturing Equipment Co., Ltd."
    assert fields["buyerName"] == "NordWerk Verpackung GmbH"
    assert fields["totalAmount"] == "86000.00"
    assert fields["items"][0] | {
        "goodsName": "INTELLIGENT PACKAGING LINE",
        "model": "PKG-600",
        "quantity": "1",
    } == fields["items"][0]


def test_actual_3995_sales_order_contract_and_bill_of_lading_layouts() -> None:
    order = """
    EXPORT SALES ORDER
    Order No. SO-251229-7498 Order Date 29 December 2025
    Related S/C No. SC-251226-3995 Currency USD
    SELLER
    Name: East China Intelligent Manufacturing Equipment Co., BUYER
    Ltd. Name: NordWerk Verpackung GmbH
    No. Description of Goods Model Quantity Unit Unit Price Amount
    1 Intelligent Packaging Line PKG-600 1 SET USD 86,000.00 USD 86,000.00
    Total Order Value USD 86,000.00
    """
    contract = order.replace("EXPORT SALES ORDER", "SALES CONTRACT").replace(
        "Order No. SO-251229-7498 Order Date 29 December 2025\n    Related S/C No. SC-251226-3995",
        "Contract No. SC-251226-3995 Contract Date 26 December 2025",
    ).replace("Total Order Value", "Total Contract Value")
    bol = """
    BILL OF LADING
    Booking No. B/L No. Issue Date and Time
    BK-SHA-260106-4481 BL-SHAHAM-260120-6638 2026-01-20 11:00
    Shipper Consignee Notify Party
    East China Intelligent Manufacturing Equipment Co., Ltd. NordWerk Verpackung GmbH NordWerk Verpackung GmbH
    Marks & Nos. No. of Pkgs Description of Goods Gross Weight Measurement
    N/M 1 CASE INTELLIGENT PACKAGING LINE PKG-600 12,500 KGS 48.60 CBM
    """

    order_fields = extract_table_anchored_fields(order, "purchase_order")
    contract_fields = extract_table_anchored_fields(contract, "contract")
    bol_fields = extract_table_anchored_fields(bol, "transport_document")

    assert order_fields["documentNo"] == order_fields["orderNo"] == "SO-251229-7498"
    assert order_fields["contractNo"] == "SC-251226-3995"
    assert order_fields["items"][0]["model"] == "PKG-600"
    assert contract_fields["documentNo"] == contract_fields["contractNo"] == "SC-251226-3995"
    assert contract_fields["items"][0]["quantity"] == "1"
    assert bol_fields["documentNo"] == bol_fields["billOfLadingNo"] == "BL-SHAHAM-260120-6638"
    assert bol_fields["documentDate"] == "2026-01-20"


def test_actual_3992_invoice_extracts_all_three_lines() -> None:
    text = """
    No FP-260118-8468
    货物或应税劳务名称 规格型号 单位 数量 单价 金额 税率 税额
    *工业自动化设备*
    VL-50 只 10 2,000.00 20,000.00 13% 2,600.00
    工业相机镜头
    *工业自动化设备*
    VC-500 台 15 10,000.00 150,000.00 13% 19,500.00
    视觉检测相机
    *工业自动化设备*
    VL-200 套 20 5,000.00 100,000.00 13% 13,000.00
    视觉光源
    价税合计（小写） ¥305,100.00
    """

    fields = extract_table_anchored_fields(text, "invoice")

    assert [item["model"] for item in fields["items"]] == ["VL-50", "VC-500", "VL-200"]
    assert [item["goodsName"] for item in fields["items"]] == ["工业相机镜头", "视觉检测相机", "视觉光源"]

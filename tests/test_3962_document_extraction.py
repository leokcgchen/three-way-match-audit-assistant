from __future__ import annotations

from src.legacy_ocr.ocr_adapter import LegacyOcrAdapter


def _extract(monkeypatch, text: str, doc_type: str) -> dict:
    monkeypatch.setenv("FIELD_EXTRACT_MODE", "heuristic")
    adapter = LegacyOcrAdapter(llm_api_key="", use_mock_when_unavailable=False)
    return adapter.extract_fields(text, doc_type)


def test_contract_and_order_extract_roles_items_and_yuan_amount_without_wan_guess(monkeypatch) -> None:
    contract = """
    销售合同
    合同编号：HT25-3647 签订日期：2025年12月08日
    卖方（供货方） 买方（采购方）
    名称：华东智造设备有限公司 名称：宁波海岳机电有限公司
    地址：上海市浦东新区张江路88号 地址：浙江省宁波市鄞州区学士路98号
    统一社会信用代码：91310115MA1K4X8P2R 统一社会信用代码：91330212MA2A7D8K5Q
    序号 货物名称 规格型号 数量 单位 含税单价 价税合计
    1 伺服电机 SM-130 SM-130 20 台 ¥5,650.00 ¥113,000.00
    合同价税合计（小写） ¥113,000.00
    约定交付计划
    交付批次 约定交付日期 计划交付数量
    整批 2026年01月02日 20台
    """
    order = """
    销售订单
    订单编号：SO-251209-7214 订单日期：2025年12月09日
    客户编码：KH-330212-0142 计价方式：含税价（增值税税率13%）
    卖方（供货方） 买方（采购方）
    名称：华东智造设备有限公司 名称：宁波海岳机电有限公司
    地址：上海市浦东新区张江路88号 地址：浙江省宁波市鄞州区学士路98号
    统一社会信用代码：91310115MA1K4X8P2R 统一社会信用代码：91330212MA2A7D8K5Q
    序号 货物名称 规格型号 数量 单位 含税单价 价税合计
    1 伺服电机 SM-130 SM-130 20 台 ¥5,650.00 ¥113,000.00
    价税合计（小写） ¥113,000.00
    计划交期：2026年01月02日
    """

    contract_fields = _extract(monkeypatch, contract, "contract")
    order_fields = _extract(monkeypatch, order, "purchase_order")

    assert contract_fields["contractNo"] == "HT25-3647"
    assert contract_fields["documentNo"] == "HT25-3647"
    assert contract_fields["sellerName"] == "华东智造设备有限公司"
    assert contract_fields["buyerName"] == "宁波海岳机电有限公司"
    assert contract_fields["plannedDeliveryDate"] == "2026-01-02"
    assert contract_fields["totalAmount"] == "113000.0"
    assert contract_fields["items"][0] | {
        "goodsName": "伺服电机",
        "model": "SM-130",
        "quantity": "20",
        "unit": "台",
        "unitPriceGross": "5650.00",
        "totalAmount": "113000.00",
    } == contract_fields["items"][0]

    assert order_fields["orderNo"] == "SO-251209-7214"
    assert order_fields["documentNo"] == "SO-251209-7214"
    assert order_fields["customerCode"] == "KH-330212-0142"
    assert order_fields["plannedDeliveryDate"] == "2026-01-02"
    assert order_fields["taxRate"] in {"13", "0.13"}
    assert order_fields["totalAmount"] == "113000.0"


def test_receipt_keeps_own_number_and_related_order_separate(monkeypatch) -> None:
    text = """
    签 收 验 收 单
    验收单号 YS-260102-005 关联订单号 SO-251209-7214
    客户编码 KH-NB-0062 编制日期 2026年01月02日
    供货方 华东智造设备有限公司 上海市浦东新区张江路88号
    收货方 宁波海岳机电有限公司 浙江省宁波市鄞州区学士路98号
    到货时间 2026年01月02日 09:00 验收完成 2026年01月02日 09:40
    物料编码 物料名称 规格型号 批次 发货数量 实收数量 单位 含税单价 含税金额
    MAT-SM130-02 伺服电机 SM-130 B251209-02 20 20 台 ¥5,650.00 ¥113,000.00
    含税金额合计 ¥113,000.00
    """
    fields = _extract(monkeypatch, text, "warehouse_receipt")

    assert fields["documentNo"] == "YS-260102-005"
    assert "invoiceNo" not in fields
    assert fields["orderNo"] == "SO-251209-7214"
    assert fields["customerCode"] == "KH-NB-0062"
    assert fields["sellerName"] == "华东智造设备有限公司"
    assert fields["buyerName"] == "宁波海岳机电有限公司"
    assert fields["arrivalDateTime"] == "2026-01-02T09:00"
    assert fields["acceptanceDate"] == "2026-01-02"
    assert fields["acceptanceDateTime"] == "2026-01-02T09:40"
    assert fields["quantity"] == "20"
    assert fields["totalAmount"] == "113000.0"


def test_invoice_extracts_invoice_identity_parties_and_tax_recalculation_inputs(monkeypatch) -> None:
    text = """
    No FP-260102-8305
    发票代码：3100264130 增值税专用发票
    开票日期：2026年01月02日 开票时间：14:50
    购 名 称：宁波海岳机电有限公司 纳税人识别号：91330212MA2A7D8K5Q
    方 地 址：浙江省宁波市鄞州区学士路98号 8/36<9*21-57>4+68/12
    货物或应税劳务名称 规格型号 单位 数量 单价 金额 税率 税额
    *工业自动化设备*
    SM-130 台 20 5,000.00 100,000.00 13% 13,000.00
    伺服电机
    价税合计（小写） ¥113,000.00
    销 名 称：华东智造设备有限公司 纳税人识别号：91310115MA1K4X8P2R
    方 地 址：上海市浦东新区张江路88号
    对应销售订单：SO-251209-7214
    """
    fields = _extract(monkeypatch, text, "invoice")

    assert fields["invoiceNo"] == "FP-260102-8305"
    assert fields["documentNo"] == "FP-260102-8305"
    assert fields["invoiceCode"] == "3100264130"
    assert fields["orderNo"] == "SO-251209-7214"
    assert fields["buyerName"] == "宁波海岳机电有限公司"
    assert fields["sellerName"] == "华东智造设备有限公司"
    assert fields["buyerAddress"] == "浙江省宁波市鄞州区学士路98号"
    assert fields["sellerAddress"] == "上海市浦东新区张江路88号"
    assert fields["goodsName"] == "伺服电机"
    assert fields["quantity"] == "20"
    assert fields["unitPrice"] == "5000.00"
    assert fields["amount"] == "100000.00"
    assert fields["taxAmount"] == "13000.00"
    assert fields["totalAmount"] == "113000.0"

"""混装凭证拆包：切分 / 类别卡 / 分笔。"""

from __future__ import annotations

from src.workflow.packet_cards import classify_page_text, load_category_cards, map_to_host_type
from src.workflow.packet_cluster import UNIDENTIFIED_CHAIN, cluster_units
from src.workflow.packet_split import PageRec, split_pages_into_units


def _p(n: int, text: str, source: str = "pack.pdf") -> PageRec:
    from src.workflow.packet_split import _page_from_text
    from src.workflow.packet_cards import load_category_cards

    return _page_from_text(source, source, n, text, "pdf_text", load_category_cards())


CONTRACT_P1 = (
    "销售合同\n合同编号 HT25-0296\n甲方 甲公司\n乙方 乙公司\n服务范围 系统实施\n合同金额 1000"
)
CONTRACT_P2 = (
    "销售合同 第2页\n付款条款\n订单号 SO25-0296\n产品名称 组件\n数量 10\n单价 100\n价税合计 1000\n本合同自签署日起生效"
)
ORDER_P = (
    "销售订单\n订单号 SO25-0296\n采购订单\n产品名称 组件\n数量 10\n单价 100\n订单金额 1000\n交货日期 2025-12-01\n收货地址 上海"
)
INVOICE_A = (
    "增值税专用发票\n发票代码 111\n发票号码 11111111\n价税合计 1000\n税额 130\n购买方 甲\n销售方 乙\n开票日期 2025-12-02"
)
INVOICE_B = (
    "增值税专用发票\n发票代码 222\n发票号码 22222222\n价税合计 800\n税额 104\n购买方 甲\n销售方 乙\n开票日期 2025-12-03"
)
ACCEPT_P = (
    "验收报告\n验收项目 系统实施\n验收依据 合同\n验收结论 验收合格\n验收人 甲公司\n验收日期 2025-12-04"
)
RECEIPT_P = (
    "客户签收单\n送货单号 SH-1\n货物名称 组件\n数量 10\n收货人 张三\n签收日期 2025-12-04"
)


def test_category_cards_load_and_map_order():
    cards = load_category_cards()
    ids = {c["id"] for c in cards["categories"]}
    assert "order" in ids
    assert "invoice" in ids
    assert map_to_host_type("order") == "order"
    assert map_to_host_type("acceptance_record") == "receipt"
    assert map_to_host_type("delivery_receipt") == "delivery"
    assert map_to_host_type("delivery_note") == "delivery"
    assert map_to_host_type("delivery_acceptance") == "delivery"
    assert map_to_host_type("receipt_acceptance") == "receipt"
    assert map_to_host_type("bank_receipt") == "payment"
    assert map_to_host_type("confirmation") == "unresolved"


def test_contract_continuation_not_split_by_order_or_invoice_terms():
    drafts = split_pages_into_units(
        [_p(1, CONTRACT_P1), _p(2, CONTRACT_P2)],
        use_vlm=False,
    )
    assert len(drafts) == 1
    assert drafts[0].pages == [1, 2]


def test_contract_continuation_with_acceptance_wording_not_split():
    """合同续页写「验收合格后付款」不得被反证/表头误切成验收单。"""
    p2 = (
        "销售合同 第2页\n违约责任\n验收合格后付款\n签收人由买方指定\n"
        "交付方式 销售发货单\n争议解决 仲裁\n双方盖章"
    )
    drafts = split_pages_into_units([_p(1, CONTRACT_P1), _p(2, p2)], use_vlm=False)
    assert len(drafts) == 1
    assert drafts[0].pages == [1, 2]
    assert drafts[0].host_type == "contract"


def test_delivery_continuation_not_split_by_waybill_reference():
    p1 = "销售发货单\n发货单号 FH-001\n货物名称 组件\n数量 10\n收货地址 上海\n发货单位 甲\n收货单位 乙"
    p2 = (
        "销售发货单 第2页\n物流与单据关系\n运单编号 WL-001\n承运人 甲物流\n"
        "目的地 上海\n本发货单记录出库"
    )
    drafts = split_pages_into_units([_p(1, p1), _p(2, p2)], use_vlm=False)
    assert len(drafts) == 1
    assert drafts[0].pages == [1, 2]


def test_blank_page_merged_and_flagged():
    drafts = split_pages_into_units(
        [_p(1, CONTRACT_P1), _p(2, "   "), _p(3, ORDER_P)],
        use_vlm=False,
    )
    assert len(drafts) == 2
    assert 2 in drafts[0].uncertain_pages or 2 in drafts[0].pages
    # 空白页并入合同单元，订单仍切开
    assert drafts[0].pages == [1, 2]
    assert drafts[1].pages == [3]


def test_page_coverage_complete():
    from src.workflow.packet_split import page_coverage_ok

    pages = [_p(1, CONTRACT_P1), _p(2, ORDER_P), _p(3, INVOICE_A)]
    drafts = split_pages_into_units(pages, use_vlm=False)
    ok, warn = page_coverage_ok(pages, drafts)
    assert ok
    assert not warn


def test_category_cards_contrary_evidence_tightened():
    from src.workflow.packet_cards import load_category_cards

    load_category_cards.cache_clear()
    cards = load_category_cards()
    assert cards["version"] == "2.0.1"
    by_id = {c["id"]: c for c in cards["categories"]}
    assert by_id["contract"]["contrary_evidence"] == ["发票代码", "银行回单"]
    assert "甲方" not in by_id["order"]["contrary_evidence"]
    assert "验收合格" not in by_id["contract"]["contrary_evidence"]


def test_explicit_title_splits_mixed_packet():
    drafts = split_pages_into_units(
        [_p(1, CONTRACT_P1), _p(2, ACCEPT_P)],
        use_vlm=False,
    )
    assert len(drafts) == 2
    assert drafts[1].split_reason == "grounded_title_transition"
    assert drafts[0].host_type == "contract"
    assert drafts[1].host_type == "receipt"


def test_invoice_number_change_splits():
    drafts = split_pages_into_units(
        [_p(1, INVOICE_A), _p(2, INVOICE_B)],
        use_vlm=False,
    )
    assert len(drafts) == 2
    assert drafts[1].split_reason == "invoice_no_change"


def test_low_quality_page_merged_and_flagged():
    drafts = split_pages_into_units(
        [_p(1, CONTRACT_P1), _p(2, "模糊")],
        use_vlm=False,
    )
    assert len(drafts) == 1
    assert 2 in drafts[0].uncertain_pages
    assert drafts[0].needs_review


def test_situation_a_single_chain_default():
    drafts = split_pages_into_units(
        [
            _p(1, CONTRACT_P1),
            _p(2, ORDER_P),
            _p(3, INVOICE_A),
            _p(4, RECEIPT_P),
        ],
        use_vlm=False,
    )
    types = [d.host_type for d in drafts]
    assert "contract" in types
    assert "order" in types
    assert "invoice" in types
    units = [
        {
            "source_file": "a.pdf",
            "doc_type": d.host_type,
            "keys": dict(d.keys),
        }
        for d in drafts
    ]
    clustered, warnings = cluster_units(
        units,
        file_kinds={"a.pdf": "packet_single_chain"},
    )
    assert not warnings
    chains = {u["chain_id"] for u in clustered}
    assert chains == {"SO25-0296"}


def test_situation_b_multiple_so_not_merged():
    so1_contract = (
        "销售合同\n合同编号 HT25-0001\n甲方 甲\n乙方 乙\n服务范围 实施\n合同金额 100\n订单号 SO25-0001"
    )
    so2_contract = (
        "销售合同\n合同编号 HT25-0002\n甲方 甲\n乙方 乙\n服务范围 实施\n合同金额 200\n订单号 SO25-0002"
    )
    drafts = split_pages_into_units(
        [
            _p(1, so1_contract, "mix.pdf"),
            _p(2, INVOICE_A.replace("11111111", "33333333") + "\n订单号 SO25-0001", "mix.pdf"),
            _p(3, so2_contract, "mix.pdf"),
            _p(4, INVOICE_B + "\n订单号 SO25-0002", "mix.pdf"),
        ],
        use_vlm=False,
    )
    assert len(drafts) >= 3
    units = [
        {
            "source_file": "mix.pdf",
            "doc_type": d.host_type,
            "keys": dict(d.keys),
        }
        for d in drafts
    ]
    clustered, warnings = cluster_units(
        units,
        file_kinds={"mix.pdf": "packet_multi_chain"},
    )
    chains = {u["chain_id"] for u in clustered}
    assert "SO25-0001" in chains
    assert "SO25-0002" in chains
    assert UNIDENTIFIED_CHAIN not in chains or len(chains) >= 2
    # 禁止糊成一笔
    assert len(chains) >= 2


def test_no_strong_id_goes_unidentified():
    units = [
        {"source_file": "x.pdf", "doc_type": "invoice", "keys": {"invoiceNo": "11111111"}},
        {"source_file": "x.pdf", "doc_type": "receipt", "keys": {}},
    ]
    clustered, _ = cluster_units(
        units,
        file_kinds={"x.pdf": "packet_multi_chain"},
    )
    assert all(u["chain_id"] == UNIDENTIFIED_CHAIN for u in clustered)


def test_classify_page_needs_evidence():
    weak = classify_page_text("价税合计 见附件")
    assert weak["primary_type"] == "unresolved" or weak["needs_review"]


def test_header_with_company_prefix_is_delivery_note():
    from src.workflow.packet_cards import detect_header_card_type

    text = "华晨汽车零部件制造有限公司\n销售发货单\n发货单位 甲\n收货单位 乙\n发货明细\n数量 10"
    assert detect_header_card_type(text) == "delivery_note"
    assert classify_page_text(text)["primary_type"] == "delivery_note"
    assert classify_page_text(text)["host_type"] == "delivery"


def test_ocr_html_headers_from_0281_scan():
    """真实扫描件 OCR 带 HTML/Markdown，必须仍能认出表头。"""
    from src.workflow.packet_cards import detect_header_card_type

    samples = {
        1: '<div style="text-align: center;"><html><body><table border="1"><tbody><tr><td colspan="3">ICBC·企业金融服务</td><td colspan="3">银行电子回单</td><td>银行流水号 BK26-0281</td></tr>',
        2: "## 销售合同\n\n合同编号：HT25-0281业务编号：S025-0281\n\n甲方（买方）：华东某整车制造有限公司",
        4: '<div style="text-align: center;">销售订单</div>\n\n业务编号SO25-0281·合同索引HT25-0281',
        6: "## 销售发货单\n\n业务编号SO25-0281·关联合同HT25-0281",
        8: "发货单位信息：公司：华曜汽车零部件制造有限公司\n\n验收单位信息：\n\n公司：华东某整车制造有限公司",
        9: '<div style="text-align: center;">客户签收验收单</div>\n\n文件编号 YS26-0281',
        10: '<td colspan="2">数电发票（增值税专用发票）</td><td colspan="3">发票号码：26322026000000002811</td>',
    }
    expect = {
        1: "bank_receipt",
        2: "contract",
        4: "order",
        6: "delivery_note",
        8: "delivery_acceptance",
        9: "receipt_acceptance",
        10: "invoice",
    }
    for n, text in samples.items():
        got = detect_header_card_type(text)
        assert got == expect[n], (n, got, expect[n])
    # 订单续页正文写「增值税发票开具之日起」不能盖过页首「销售订单」
    order_with_vat_terms = (
        samples[4]
        + "\n产品名称 组件\n## 二、 交付与执行要求\n付款条件摘要 增值税发票开具之日起30日内"
    )
    assert detect_header_card_type(order_with_vat_terms) == "order"

    # 发货单续页表格里提到签收验收单，不能当成新表头
    p7 = (
        "## 二、 物流与单据关系\n"
        "<tr><td>交付证据类型</td><td>客户签收验收单</td><td>控制权资料</td>"
        "<td>客户签收验收单YS26-0281</td></tr>"
    )
    assert detect_header_card_type(p7) is None

    drafts = split_pages_into_units(
        [
            _p(1, samples[1] + "\n付款人 甲\n收款人 乙\n交易金额 10942.90\n交易日期 2026-01-18"),
            _p(2, samples[2] + "\n乙方（卖方）：华曜\n合同金额 10942.90\n违约责任 按合同\n争议解决 仲裁"),
            _p(3, "## 六、 履约义务、质量保证及售后服务\n乙方交付符合约定规格和数量的产品。本合同未尽事宜双方协商。"),
            _p(4, samples[4] + "\n产品名称 组件\n数量 357\n订单金额 9683.98\n交货日期 2025-12-30"),
            _p(5, "## 二、 交付与执行要求\n控制权资料 待到货后出具\n付款条件摘要 增值税发票开具之日起30日内"),
            _p(6, samples[6] + "\n发货单位 华曜\n收货单位 华东\n发货明细\n数量 357"),
            _p(7, p7 + "\n物流/运单编号 HY25-0281\n计划交付要求 2025年12月30日前送达"),
            _p(8, samples[8] + "\n实发数量 357\n发货人 李四"),
            _p(9, samples[9] + "\n收货单位 华东\n签收人 张三\n实收数量 357"),
            _p(10, samples[10] + "\n价税合计 10942.90\n税额 1258.92\n购买方 甲\n销售方 乙"),
        ],
        use_vlm=False,
    )
    pages = [d.pages for d in drafts]
    cards = [d.card_type for d in drafts]
    assert pages == [[1], [2, 3], [4, 5], [6, 7], [8], [9], [10]], (pages, cards)
    assert cards == [
        "bank_receipt",
        "contract",
        "order",
        "delivery_note",
        "delivery_acceptance",
        "receipt_acceptance",
        "invoice",
    ], cards


def test_so250281_plain_text_still_splits():
    """0281 一包多单：纯文本表头切开，不把发货单糊进发票。"""
    p1 = (
        "中国工商银行电子回单\n交易流水号 202512280001\n付款人 甲公司\n"
        "收款人 乙公司\n交易金额 10942.90\n交易日期 2025-12-28"
    )
    p2 = (
        "销售合同\n合同编号 HT25-0281\n甲方 甲公司\n乙方 乙公司\n"
        "服务范围 汽车零部件\n合同金额 10942.90\n违约责任 按合同执行\n争议解决 仲裁"
    )
    p3 = (
        "付款条款 签收后30日\n控制权转移 签收后转移\n本合同未尽事宜双方协商\n"
        "订单号 SO25-0281 仅为履行本合同"
    )
    p4 = (
        "销售订单\n订单号 SO25-0281\n产品名称 组件\n数量 357\n单价 27.13\n"
        "订单金额 9683.98\n交货日期 2025-12-30\n收货地址 沈阳"
    )
    p5 = "交货日期 2025-12-30\n收货地址 沈阳市\n备注 按销售订单执行不得变更数量"
    p6 = (
        "华晨汽车零部件制造有限公司\n销售发货单\n单据编号 HY25-0301\n"
        "交易号 SO25-0281\n发货单位 乙公司\n收货单位 甲公司\n发货明细\n数量 357"
    )
    p7 = "发货明细续\n物料编码 A-01\n规格型号 标准件\n数量 357\n发货日期 2025-12-28"
    p8 = (
        "发货验收单\n发货单位 乙公司\n实发数量 357\n发货人 李四\n"
        "发货日期 2025-12-28\n验收意见 实发无误"
    )
    p9 = (
        "签收验收单\n收货单位 甲公司\n签收人 张三\n实收数量 357\n"
        "签收日期 2025-12-30\n收货确认 已收货"
    )
    p10 = (
        "增值税专用发票\n发票代码 111001\n发票号码 12345678\n价税合计 10942.90\n"
        "税额 1258.92\n购买方 甲公司\n销售方 乙公司\n开票日期 2026-01-02"
    )
    drafts = split_pages_into_units(
        [
            _p(1, p1),
            _p(2, p2),
            _p(3, p3),
            _p(4, p4),
            _p(5, p5),
            _p(6, p6),
            _p(7, p7),
            _p(8, p8),
            _p(9, p9),
            _p(10, p10),
        ],
        use_vlm=False,
    )
    pages = [d.pages for d in drafts]
    hosts = [d.host_type for d in drafts]
    cards = [d.card_type for d in drafts]
    assert pages == [[1], [2, 3], [4, 5], [6, 7], [8], [9], [10]], (pages, cards)
    assert hosts == [
        "payment",
        "contract",
        "order",
        "delivery",
        "delivery",
        "receipt",
        "invoice",
    ], (hosts, cards)
    assert cards[0] == "bank_receipt"
    assert cards[3] == "delivery_note"
    assert cards[4] == "delivery_acceptance"
    assert cards[5] == "receipt_acceptance"


def test_0282_header_variants_from_merge_pdf():
    """合并.pdf 第二笔：空格拆开的合同标题、公司名在表头前、目的地交货签收单、回单在包尾。"""
    from src.workflow.packet_cards import detect_header_card_type

    assert detect_header_card_type("销 售 合 同\n合同编号：KJHT25-0282") == "contract"
    assert (
        detect_header_card_type(
            "华曜汽车零部件制造有限公司 销售循环业务单据\n销售订单\n业务编号 SO25-0282"
        )
        == "order"
    )
    assert (
        detect_header_card_type(
            "华曜汽车零部件制造有限公司 销售循环业务单据\n销售发货单\n文件编号 DAP25-0282"
        )
        == "delivery_note"
    )
    assert (
        detect_header_card_type(
            "产品验收单\n发货单位信息：公司：华曜\n验收单位信息：\n公司：广州某新能源"
        )
        == "delivery_acceptance"
    )
    assert detect_header_card_type(
        "HUAYAO AUTO COMPONENTS\n目的地交货签收单\n文件编号 DAP-QS25-0282"
    ) == "receipt_acceptance"
    assert (
        detect_header_card_type(
            "发票号码：25322025000000002821\nHUAYAO 数电发票（增值税专用发票）"
        )
        == "invoice"
    )
    assert (
        detect_header_card_type(
            "ICBC 企业金融服务 银行电子回单 银行流水号\nBK26-0282"
        )
        == "bank_receipt"
    )


def test_same_title_different_so_splits_chain():
    """两笔订单标题相同，必须按订单号切开，不能糊成一张单。"""
    drafts = split_pages_into_units(
        [
            _p(1, "销售订单\n订单号 SO25-0281\n产品名称 组件\n数量 10\n订单金额 1000"),
            _p(2, "销售订单\n订单号 SO25-0282\n产品名称 组件\n数量 2\n订单金额 2000"),
        ],
        use_vlm=False,
    )
    assert [d.pages for d in drafts] == [[1], [2]]
    assert drafts[1].split_reason == "chain_id_change"
    assert drafts[0].keys.get("orderNo") == "SO25-0281"
    assert drafts[1].keys.get("orderNo") == "SO25-0282"


def test_merge_0281_0282_two_chains():
    """训练样本：20 页混装两笔，切 14 张单并按 SO 分桶。"""
    p0281 = [
        (
            "中国工商银行电子回单\n交易流水号 BK26-0281\n付款人 甲\n收款人 乙\n"
            "交易金额 10942.90\n业务编号 SO25-0281 合同编号 HT25-0281"
        ),
        (
            "销售合同\n合同编号 HT25-0281\n甲方 甲公司\n乙方 乙公司\n"
            "服务范围 汽车零部件\n合同金额 10942.90\n违约责任 按合同执行"
        ),
        "六、 履约义务、质量保证及售后服务\n乙方交付符合约定规格和数量的产品。本合同未尽事宜双方协商。",
        (
            "销售订单\n业务编号SO25-0281·合同索引HT25-0281\n产品名称 组件\n"
            "数量 357\n订单金额 9683.98"
        ),
        "二、 交付与执行要求\n控制权资料 待到货后出具\n付款条件摘要 增值税发票开具之日起30日内",
        "销售发货单\n业务编号SO25-0281·关联合同HT25-0281\n发货单位 华曜\n收货单位 华东\n数量 357",
        "二、 物流与单据关系\n交付证据类型 客户签收验收单\n控制权资料 客户签收验收单YS26-0281",
        "发货单位信息：公司：华曜汽车零部件制造有限公司\n验收单位信息：\n公司：华东某整车制造有限公司\n实发数量 357\n业务编号 SO25-0281 合同编号 HT25-0281",
        "客户签收验收单\n文件编号 YS26-0281\n业务编号 SO25-0281\n签收人 张三\n实收数量 357",
        (
            "数电发票（增值税专用发票）\n发票号码：26322026000000002811\n"
            "价税合计 10942.90\n购买方 甲\n销售方 乙\n业务编号 SO25-0281"
        ),
    ]
    p0282 = [
        (
            "销 售 合 同\n合同编号：KJHT25-0282 业务编号：SO25-0282\n"
            "甲方（买方）：广州某新能源整车有限公司\n乙方（卖方）：华曜汽车零部件制造有限公司"
        ),
        "六、 履约义务、质量保证及售后服务\n乙方交付符合约定规格和数量的产品。本合同未尽事宜双方协商。",
        (
            "华曜汽车零部件制造有限公司 销售循环业务单据\n销售订单\n"
            "业务编号 SO25-0282 · 合同索引 KJHT25-0282\n文件编号 SO25-0282\n数量 2"
        ),
        "二、交付与执行要求\n控制权资料 目的地交货签收单\n付款条件摘要 增值税发票开具之日起30日内",
        (
            "华曜汽车零部件制造有限公司 销售循环业务单据\n销售发货单\n"
            "文件编号 DAP25-0282 发货日期 2025年12月15日\n发货单位 华曜\n收货单位 广州某新能源"
        ),
        "二、物流与单据关系\n交付证据类型 目的地交货签收单\n业务编号/销售订单号 SO25-0282",
        (
            "产品验收单\n发货单位信息：公司：华曜汽车零部件制造有限公司\n"
            "验收单位信息：\n公司：广州某新能源整车有限公司\n"
            "业务编号 SO25-0282 合同编号 KJHT25-0282"
        ),
        (
            "HUAYAO AUTO COMPONENTS\n目的地交货签收单\n文件编号 DAP-QS25-0282\n"
            "合同编号 KJHT25-0282\n业务编号 / 销售订单号 SO25-0282"
        ),
        (
            "发票号码：25322025000000002821\nHUAYAO 数电发票（增值税专用发票）\n"
            "购买方名称：广州某新能源整车有限公司\n销售方名称：华曜\n业务编号 SO25-0282"
        ),
        (
            "ICBC 企业金融服务 银行电子回单 银行流水号\nBK26-0282\n"
            "附加业务参考号 SO25-0282、VAT25-0282"
        ),
    ]
    pages = [_p(i, text) for i, text in enumerate(p0281 + p0282, start=1)]
    drafts = split_pages_into_units(pages, use_vlm=False)
    assert [d.pages for d in drafts] == [
        [1],
        [2, 3],
        [4, 5],
        [6, 7],
        [8],
        [9],
        [10],
        [11, 12],
        [13, 14],
        [15, 16],
        [17],
        [18],
        [19],
        [20],
    ], [d.pages for d in drafts]
    assert [d.card_type for d in drafts] == [
        "bank_receipt",
        "contract",
        "order",
        "delivery_note",
        "delivery_acceptance",
        "receipt_acceptance",
        "invoice",
        "contract",
        "order",
        "delivery_note",
        "delivery_acceptance",
        "receipt_acceptance",
        "invoice",
        "bank_receipt",
    ]
    units = [
        {
            "source_file": "合并.pdf",
            "doc_type": d.host_type,
            "keys": dict(d.keys),
        }
        for d in drafts
    ]
    clustered, _warnings = cluster_units(
        units,
        file_kinds={"合并.pdf": "packet_multi_chain"},
    )
    by_chain: dict[str, list[str]] = {}
    for u in clustered:
        by_chain.setdefault(str(u["chain_id"]), []).append(str(u["doc_type"]))
    assert set(by_chain) == {"SO25-0281", "SO25-0282"}
    assert by_chain["SO25-0281"] == [
        "payment",
        "contract",
        "order",
        "delivery",
        "delivery",
        "receipt",
        "invoice",
    ]
    assert by_chain["SO25-0282"] == [
        "contract",
        "order",
        "delivery",
        "delivery",
        "receipt",
        "invoice",
        "payment",
    ]
    assert drafts[7].keys.get("contractNo") in {"KJHT25-0282", "HT25-0282"}
    assert drafts[7].keys.get("orderNo") == "SO25-0282"

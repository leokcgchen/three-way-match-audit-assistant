"""图片 OCR 高亮：拒绝整表明细大块，避免「数量」框住半张发票。"""

from __future__ import annotations

from src.ui.field_highlight import match_ocr_blocks


def test_reject_table_sized_layout_block_for_quantity():
    blocks = [
        {
            "text": "乘用车轮胎…数量2单价…金额…税率13%税额…折扣行…价税合计",
            "bbox": [40, 200, 1100, 720],  # 整表明细
            "source": "layout",
        },
        {"text": "2", "bbox": [520, 310, 548, 332], "source": "ocr_line"},
    ]
    hits = match_ocr_blocks("2", blocks, image_size=(1200, 1600))
    assert hits, "应命中行级数量框"
    bb = hits[0]["bbox"]
    assert bb[2] - bb[0] < 80
    assert bb[3] - bb[1] < 40


def test_only_large_block_yields_empty_rather_than_huge_box():
    blocks = [
        {
            "text": "购买方信息销售方信息货物明细数量2金额合计一整表",
            "bbox": [30, 180, 1150, 780],
            "source": "layout",
        }
    ]
    hits = match_ocr_blocks("2", blocks, image_size=(1200, 1600))
    assert hits == []


def test_buyer_name_from_info_panel_is_tight():
    blocks = [
        {
            "text": "购买方信息\n\n名称：西南某商用车制造有限公司\n\n统一社会信用代码：91500000MA5AUTO003地址、电话：重庆",
            "bbox": [169.0, 578.0, 856.0, 782.0],
            "source": "layout",
        }
    ]
    hits = match_ocr_blocks("西南某商用车制造有限公司", blocks, image_size=(3508, 2480))
    assert hits, "购方名称应可高亮"
    bb = hits[0]["bbox"]
    assert bb[2] - bb[0] < 520
    assert bb[3] - bb[1] < 90


def test_date_prefers_tight_line():
    blocks = [
        {
            "text": "发票状态：正常\n\n开票日期：2025年12月28日发票业务索引：VAT25-0296",
            "bbox": [371.0, 332.0, 704.0, 466.0],
        },
        {"text": "开票日期：2025年12月28日", "bbox": [3030.0, 2354.0, 3354.0, 2382.0]},
    ]
    hits = match_ocr_blocks("2025-12-28", blocks, image_size=(3508, 2480))
    assert hits
    bb = hits[0]["bbox"]
    assert bb[3] - bb[1] < 50


def test_html_table_block_explodes_quantity_total():
    from src.ui.field_highlight import _expand_ocr_blocks_for_highlight

    html = (
        '<div><html><body><table border="1"><tbody>'
        "<tr><td>项目名称</td><td>数量</td></tr>"
        "<tr><td>轮毂</td><td>300</td></tr>"
        '<tr><td colspan="8">合计 数量合计：912件</td></tr>'
        "</tbody></table></body></html></div>"
    )
    blocks = [{"text": html, "bbox": [150.0, 900.0, 3350.0, 2000.0], "source": "layout"}]
    exp = _expand_ocr_blocks_for_highlight(blocks)
    assert any(str(b.get("text")) == "912" for b in exp)
    hits = match_ocr_blocks("912", exp, image_size=(3508, 2480))
    assert hits
    assert hits[0]["bbox"][2] - hits[0]["bbox"][0] < 160

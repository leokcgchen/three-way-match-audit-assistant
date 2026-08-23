from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.ui.preview_capture import capture_text_in_rect, list_page_text_blocks, render_preview_page

_MOCK_ORDER = Path("data/mock/SO25-0281/SO25-0281_HT25-0281_02_销售订单.pdf")


def test_render_and_capture_on_mock_order_pdf():
    p = _MOCK_ORDER
    if not p.is_file():
        return
    png, meta = render_preview_page(p, page_index=0)
    assert png and meta["page_count"] >= 1
    blocks = list_page_text_blocks(p, page_index=0)
    assert blocks["blocks"], "文本层/词应可点选"
    # 整页拖框应能取到字
    out = capture_text_in_rect(p, page_index=0, x0=0.05, y0=0.05, x1=0.95, y1=0.95)
    assert out.get("text"), out.get("message")


def test_concurrent_pdf_render_does_not_crash():
    p = _MOCK_ORDER
    if not p.is_file():
        return

    def one(_i: int) -> int:
        png, meta = render_preview_page(p, page_index=0)
        assert meta["page_count"] >= 1
        return len(png)

    with ThreadPoolExecutor(max_workers=4) as pool:
        sizes = list(pool.map(one, range(8)))
    assert all(n > 1000 for n in sizes)

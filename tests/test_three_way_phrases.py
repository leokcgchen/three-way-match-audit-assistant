from src.three_way_match.phrases import (
    expand_qty_role_shorthand,
    quantity_roles_phrase,
    strip_match_score_language,
)


def test_quantity_roles_phrase_full_words():
    s = quantity_roles_phrase(95, 100, 95)
    assert "订单数量 95" in s
    assert "签收/验收数量 100" in s
    assert "发票开票数量 95" in s
    assert "订 " not in s.replace("订单", "")


def test_expand_legacy_shorthand():
    raw = "数量（订/收/开）：订 95 vs 收 100 vs 开 95 — quantity超出±1%容差"
    out = expand_qty_role_shorthand(raw)
    assert "订单数量 95" in out
    assert "签收/验收数量 100" in out
    assert "发票开票数量 95" in out
    assert "订/收/开" not in out


def test_strip_match_score_language():
    assert "得分" not in strip_match_score_language("三单匹配通过，得分 100")
    assert "得分" not in strip_match_score_language("三单匹配失败（得分 30）：金额不一致")
    assert "得分" not in expand_qty_role_shorthand("三单匹配通过，匹配得分100分。订/收/开一致")

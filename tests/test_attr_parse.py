"""力量/智力/魅力缩写解析的回归测试。

真机截图（runs/career_tree_last.png）里是 `力量 1.18w`，旧正则只读了 1；
这里锁住 w/万/小数/普通整数/OCR 漏字等几种形态。
"""
from src.attr_parse import parse_attr


def test_wan_suffix_is_scaled():
    assert parse_attr('力量1.18w') == ('力量', 11800)
    assert parse_attr('力量 1.18W') == ('力量', 11800)
    assert parse_attr('智力1.2万') == ('智力', 12000)
    assert parse_attr('魅力2w') == ('魅力', 20000)


def test_decimal_without_suffix_is_treated_as_wan():
    # OCR 常把 w 漏掉：属性是整数，出现小数只可能是缩写
    assert parse_attr('力量1.18') == ('力量', 11800)


def test_plain_integers_are_unchanged():
    assert parse_attr('智力878') == ('智力', 878)
    assert parse_attr('魅力 595') == ('魅力', 595)
    assert parse_attr('力量11800') == ('力量', 11800)


def test_commas_and_spaces():
    assert parse_attr('力量 1,18w') == ('力量', 11800)
    assert parse_attr('力 量1.18w') is None  # 标签被拆开时不猜


def test_unrelated_text():
    assert parse_attr('职业树') is None
    assert parse_attr('') is None

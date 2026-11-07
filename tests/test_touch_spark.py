"""摸摸场景的离线单测（不连设备）：火花状态判定 + 好友卡匹配。"""
from types import SimpleNamespace

import numpy as np

from scenarios.touch import (TouchScenario, _gold_border_pixels, card_visit_count,
                             card_visited, current_card_index, names_match, same_name,
                             save_touch_progress, spark_state_from_texts,
                             touch_done_today)


def test_spark_state_active_days():
    assert spark_state_from_texts(['54天']) is True
    assert spark_state_from_texts(['54 天']) is True
    # RapidOCR 偶尔把一段文本拆成两块
    assert spark_state_from_texts(['54', '天']) is True


def test_spark_state_active_lit():
    # 游戏内活跃态文案（pet_home 源码 status==3 分支）
    assert spark_state_from_texts(['火花点亮中']) is True
    assert spark_state_from_texts(['点亮中']) is True


def test_spark_state_inactive():
    assert spark_state_from_texts(['待重燃']) is False


def test_spark_state_unknown():
    assert spark_state_from_texts([]) is None
    assert spark_state_from_texts(['加好友']) is None


def test_names_match_truncated_ocr():
    # 顶部资料卡昵称会被截断成 "喵帕…"
    assert names_match('喵帕…', '喵帕斯～')
    assert names_match('柠檬🍋', '柠檬..')
    assert names_match('明骏', '明骏')
    # 带括号/符号的昵称：顶部 OCR "小丑[..."/"小丑【..." 也要能对上卡片全名
    assert names_match('小丑[...', '小丑[Joker＞')
    assert names_match('小丑【...', '小丑[Joker＞')
    assert not names_match('明骏', '小汤圆')
    assert not names_match('', '明骏')


def test_current_card_index():
    cards = ['喵帕斯～', '柠檬..', '布耶可']
    assert current_card_index(cards, ['喵帕…']) == 0
    assert current_card_index(cards, ['柠檬']) == 1
    assert current_card_index(cards, ['小汤圆']) is None


def test_current_card_index_prefers_exact_over_prefix():
    # 真实好友列表里存在互相为前缀的两个昵称：顶部 OCR 只读到 "凌晨" 时，
    # 必须认成那个精确名为 "凌晨" 的好友，不能被更长的 "凌晨吃早饭~" 抢走。
    cards = ['陆港✔', '凌晨', '凌晨吃早饭~']
    assert current_card_index(cards, ['凌晨']) == 1
    assert current_card_index(cards, ['凌晨吃早饭~']) == 2
    # 单字截断无法可靠匹配（names_match 要求至少 2 字）；靠金色描边兜底，
    # 纯函数这里保守返回 None，调用方会停止而不是认错卡。
    assert current_card_index(cards, ['陆']) is None


def test_current_card_index_ambiguous_prefix_returns_none():
    # 两个卡都以 OCR 文本为前缀 → 无法判断当前是哪张；返回 None 让金边兜底，
    # 不能按"更长的那张"猜（会点错下一位）。
    assert current_card_index(['喵帕斯～', '喵帕斯～二号'], ['喵帕']) is None


def test_card_visit_count_distinguishes_duplicate_names():
    # QQ 允许重名：同一个昵称出现 2 次仍应允许摸第二位，超过才判循环。
    assert card_visit_count('小明', ['小明']) == 1
    assert card_visit_count('小明', ['小明', '小明']) == 2
    assert card_visit_count('小明', ['小红']) == 0


def test_visited_guard_is_exact_not_prefix():
    # 真实好友里有 "凌晨" 和 "凌晨吃早饭~"：摸了前者后，后者不能被当成
    # 已摸过而停止；只有昵称完全相同才算重复。
    assert card_visited('凌晨吃早饭~', ['凌晨']) is False
    assert card_visited('凌晨', ['凌晨']) is True
    assert card_visited('喵帕斯～', ['喵帕…']) is False


def test_gold_border_marks_selected_card():
    # 100x100 的合成屏幕：卡片 (10,10)-(90,90) 画 2px 金边；旁边一张白卡
    screen = np.full((120, 220, 3), 255, dtype=np.uint8)
    gold = (255, 190, 40)
    x1, y1, x2, y2 = 10, 10, 100, 100
    screen[y1:y2, x1:x2] = (40, 40, 40)
    for t in range(2):
        screen[y1 + t:y1 + t + 1, x1:x2] = gold
        screen[y2 - t - 1:y2 - t, x1:x2] = gold
        screen[y1:y2, x1 + t:x1 + t + 1] = gold
        screen[y1:y2, x2 - t - 1:x2 - t] = gold
    assert _gold_border_pixels(screen, (x1, y1, x2, y2)) > 0
    assert _gold_border_pixels(screen, (120, 10, 210, 100)) == 0


def test_pet_seconds_clamped_to_sane_range():
    scen = TouchScenario.__new__(TouchScenario)
    for raw, expected in ((3, 3), (0, 3), (-2, 1), (99, 10), ('x', 3)):
        scen.cfg = SimpleNamespace(touch=SimpleNamespace(pet_seconds=raw))
        assert scen._pet_seconds() == expected


def test_same_name_is_exact_after_normalization():
    assert same_name('凌晨', '凌晨') is True
    assert same_name('喵帕…', '喵帕斯～') is False
    assert same_name('', '') is False


def test_touch_progress_roundtrip(tmp_path):
    p = tmp_path / 'touch_progress.json'
    assert touch_done_today(p, today='2026-11-07') is False
    save_touch_progress(7, p, today='2026-11-07')
    assert touch_done_today(p, today='2026-11-07') is True
    assert touch_done_today(p, today='2026-11-08') is False
    # 损坏文件不能抛，也不能误判成今天已完成
    p.write_text('{bad', encoding='utf-8')
    assert touch_done_today(p, today='2026-11-07') is False
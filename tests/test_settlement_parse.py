"""结算页 OCR 文本 → 结构化收益的解析测试。

`parse_session_reward(kind, texts)` 是 109 行的密集正则解析（src/scenario.py:111），
是纯函数、无副作用，且承载了项目里最多的"实测踩坑记录"——因此它是最该被钉住的
一个点。本文件的用例全部来自 AGENTS.md 与 src/scenario.py 注释里记下的真实坑，
不是臆造的输入。

设计原则：**锁现状**。先用当前实现跑出结果再判断哪些是"应该的"。若某条断言失败，
先确认是测试写错了还是实现有 bug，再决定改哪边——不要为了让测试变绿而放宽断言。
"""
from __future__ import annotations

import pytest

from src.scenario import parse_session_reward


class TestWorkSettlement:
    """打工结算页（"工资明细"）。实测字段见 src/scenario.py:118-122。"""

    def test_basic_fields(self):
        """完整一屏：岗位 / 金币 / 工分 / 工资构成。"""
        texts = [
            '武馆教学助理(10分钟)',
            '大字 +28',
            '本金22，雇佣加成6',
            '限时补贴',
            '工分+3',
            '金币+28',
            '看视频+10',
            '疲惫，收益减少',
            '分享·继续打工',
        ]
        r = parse_session_reward('work', texts)
        assert r['coins'] == 28
        assert r['workpoints'] == 3
        assert r['job'] == '武馆教学助理'
        assert r['job_duration'] == '10分钟'
        assert r['tired'] is True

    def test_coins_takes_labelled_number_only(self):
        """页面上除「金币+28」还有裸的「+28」，金币只能取带标签的那个。

        这是 src/scenario.py:120-121 明确记下的坑："页面上还有裸的 '+28' 和
        '看视频+10'，不能乱认数字"。
        """
        texts = ['+28', '金币+7', '+999']
        r = parse_session_reward('work', texts)
        assert r['coins'] == 7, '不能把裸数字当金币'

    def test_ad_coins_is_separate(self):
        """「看视频+10」是独立一笔，不计进 coins（历史 23 条合计漏记 +589）。"""
        texts = ['金币+28', '看视频+10']
        r = parse_session_reward('work', texts)
        assert r['coins'] == 28
        assert r['ad_coins'] == 10

    def test_ad_coins_tolerates_official_wording(self):
        """官方文案是「看视频获得N金币」，正则要能吃下中间的杂字。"""
        r = parse_session_reward('work', ['看视频获得10金币'])
        assert r['ad_coins'] == 10
        assert r['coins'] is None, '「获得10金币」不该被当成工资金币'

    def test_pay_detail_fallback(self):
        """工资构成标题由服务端下发、客户端不硬编码——靠「本金/加成」整行兜底。"""
        r = parse_session_reward('work', ['本金22，雇佣加成6'])
        assert r['pay_detail'] == '本金22，雇佣加成6'


class TestSchoolSettlement:
    """学习结算页（"教师评语"）。"""

    def test_credits_and_course(self):
        texts = ['宠物成绩单', '成绩 A+', '课程 障碍挑战课', '学分 +5']
        r = parse_session_reward('school', texts)
        assert r['credits'] == 5
        assert r['course'] == '障碍挑战课'
        assert r['grade'] == 'A+'

    def test_attrs(self):
        """「力量+5」这类属性加成。"""
        r = parse_session_reward('school', ['力量+5', '智力+3'])
        assert r['attrs'].get('力量') == 5
        assert r['attrs'].get('智力') == 3

    def test_work_only_fields_stay_empty_on_school(self):
        """岗位/工分/工资构成是打工页专有字段——学习页不许被同形文案污染。

        见 src/scenario.py:145-146："万一两页出现同形文案也不互相污染"。
        """
        texts = ['武馆教学助理(10分钟)', '工分+3', '本金22']
        r = parse_session_reward('school', texts)
        assert r['job'] == '', '学习页不该解析岗位'
        assert r['workpoints'] is None, '学习页不该解析工分'
        assert r['pay_detail'] == '', '学习页不该解析工资构成'


class TestHeader:
    """结算页头部：宠物名 / 主人名（多宠多号时用来区分记录归谁）。"""

    def test_pet_and_owner(self):
        r = parse_session_reward('work', ['姓名杨萌萌', '主人 Serena'])
        assert r['pet'] == '杨萌萌'
        assert r['owner'] == 'Serena'

    def test_pet_name_length_guard(self):
        """超长文本不该被当成宠物名（正则允许 1-16 字）。"""
        r = parse_session_reward('work', ['姓名' + '很' * 30])
        assert r['pet'] == ''


class TestRobustness:
    """认不出一律留空/None，不猜。"""

    def test_empty_input(self):
        r = parse_session_reward('work', [])
        assert r['coins'] is None
        assert r['workpoints'] is None
        assert r['ad_coins'] is None
        assert r['job'] == ''

    def test_garbage_input_does_not_raise(self):
        r = parse_session_reward('school', ['', '   ', '???', '金币'])
        assert r['coins'] is None

    def test_non_string_tokens(self):
        """OCR 层偶尔会给出非字符串 token，不能炸。"""
        r = parse_session_reward('work', [None, 123, '金币+5'])  # type: ignore[list-item]
        assert r['coins'] == 5

    def test_all_expected_keys_present(self):
        """返回结构稳定——消费端（record_session_reward / dashboard）依赖这些键。"""
        r = parse_session_reward('work', ['金币+1'])
        expected = {
            'credits', 'attrs', 'course', 'grade', 'coins', 'tired',
            'workpoints', 'job', 'job_duration', 'pay_detail',
            'pet', 'owner', 'note', 'ad_coins',
        }
        assert expected <= set(r), f'缺少键：{expected - set(r)}'

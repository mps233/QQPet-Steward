"""日内收益汇总（收工总账 / 冒险日报）—— src/daily_summary.py 的行为测试。

重点盯三件事：
1. **归属哪天**：学习/打工按结算页自带日期（`sig`）归属，跨零点结算算前一天，
   与 dashboard.rewards_data 同口径；
2. **只统计能确定的字段**：没解析出的不收进合计，且用 `*_n` 区分"没收益"与"没解析出来"；
3. **重复记录不重复计**：冒险同一结算页可能被两条收尾路径各记一次。
"""
import json

from src import daily_summary as ds

DAY = '2026-10-08'


def write_jsonl(path, rows):
    path.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows) + '\n',
                    encoding='utf-8')


def school_rec(sig, attrs=None, credits=None, ts=None):
    return {'ts': ts or f'{DAY} 10:00:00', 'kind': 'school', 'sig': sig,
            'credits': credits, 'attrs': attrs or {}}


def work_rec(sig, coins=None, workpoints=None, ad_coins=None, ts=None):
    return {'ts': ts or f'{DAY} 11:00:00', 'kind': 'work', 'sig': sig,
            'coins': coins, 'workpoints': workpoints, 'ad_coins': ad_coins}


def test_aggregates_school_attrs_credits_and_work_rewards(isolate_runs):
    write_jsonl(ds.SESSION_REWARDS_FILE, [
        school_rec('2026/10/08 09:00', {'力量': 7}, 8),
        school_rec('2026/10/08 09:30', {'魅力': 5}, 6),
        work_rec('2026/10/08 11:00', coins=28, workpoints=3, ad_coins=10),
        work_rec('2026/10/08 12:00', coins=30, workpoints=4),
    ])
    data = ds.summarize(DAY, study_s=3600, work_s=1800)
    assert data['school']['sessions'] == 2
    assert data['school']['credits'] == 14
    assert data['school']['attrs'] == {'力量': 7, '智力': 0, '魅力': 5}
    assert data['work']['sessions'] == 2
    assert data['work']['coins'] == 58
    assert data['work']['workpoints'] == 7
    # 「看视频 +N」是独立一笔，不能混进工资金币
    assert data['work']['ad_coins'] == 10


def test_unparsed_fields_are_excluded_not_counted_as_zero(isolate_runs):
    """金币没解析出来的记录不计入合计，但要用 *_n 让文案能说明情况。"""
    write_jsonl(ds.SESSION_REWARDS_FILE, [
        work_rec('2026/10/08 11:00', coins=28, workpoints=3),
        work_rec('2026/10/08 12:00', coins=None, workpoints=None),
    ])
    data = ds.summarize(DAY)
    assert data['work']['sessions'] == 2
    assert data['work']['coins'] == 28
    assert data['work']['coins_n'] == 1
    assert work_text(data).count('未解析出') == 0  # 有解析出数值就不该报未解析


def test_cross_midnight_settlement_belongs_to_previous_day(isolate_runs):
    """跨零点结算：结算页日期（sig）是活动开始时间 → 算前一天。"""
    write_jsonl(ds.SESSION_REWARDS_FILE, [
        school_rec('2026/10/07 23:50', {'力量': 3}, 5, ts='2026-10-08 00:10:00'),
    ])
    assert ds.summarize(DAY)['school']['sessions'] == 0
    assert ds.summarize('2026-10-07')['school']['sessions'] == 1


def test_slash_spacing_in_settlement_date_is_tolerated(isolate_runs):
    """结算页日期实测是 `2026 / 09 /27 11:44`（斜杠两侧带空格），必须容忍。"""
    write_jsonl(ds.SESSION_REWARDS_FILE, [school_rec('2026/ 10/08 13:10', {'力量': 5}, 6)])
    assert ds.summarize(DAY)['school']['sessions'] == 1


def test_falls_back_to_record_time_when_sig_has_no_date(isolate_runs):
    """sig 退化成内容 md5 时按记录时刻归属。"""
    write_jsonl(ds.SESSION_REWARDS_FILE, [school_rec('deadbeef1234', {'力量': 1}, 2)])
    assert ds.summarize(DAY)['school']['sessions'] == 1


def test_adventure_duplicate_sig_counted_once(isolate_runs):
    """同一结算页被两条路径各记一次（sig 相同）：金币只算一次。"""
    write_jsonl(ds.ADVENTURE_LIVE_FILE, [
        {'ts': f'{DAY} 22:00:00', 'coins': 20, 'sig': '2026/10/08 21:55'},
        {'ts': f'{DAY} 22:00:05', 'coins': 20, 'sig': '2026/10/08 21:55'},
        {'ts': f'{DAY} 22:05:00', 'coins': 0, 'sig': '2026/10/08 22:01'},
    ])
    adv = ds.summarize(DAY)['adventure']
    assert adv['count'] == 2
    assert adv['coins'] == 20
    assert adv['paid'] == 1


def test_adventure_ignores_other_days(isolate_runs):
    write_jsonl(ds.ADVENTURE_LIVE_FILE, [
        {'ts': '2026-10-07 22:00:00', 'coins': 999, 'sig': 'a'},
        {'ts': f'{DAY} 22:00:00', 'coins': 20, 'sig': 'b'},
    ])
    assert ds.summarize(DAY)['adventure']['coins'] == 20


def test_broken_lines_do_not_break_aggregation(isolate_runs):
    ds.SESSION_REWARDS_FILE.write_text(
        'not json\n' + json.dumps(school_rec('2026/10/08 09:00', {'智力': 4}, 5)) + '\n',
        encoding='utf-8')
    assert ds.summarize(DAY)['school']['credits'] == 5


def test_missing_files_are_treated_as_no_income(isolate_runs):
    data = ds.summarize(DAY)
    assert data['school']['sessions'] == 0 and data['work']['sessions'] == 0
    assert data['adventure']['count'] == 0


def work_text(data):
    return ds.format_work(data)


def test_work_summary_reports_durations_and_per_side_rewards(isolate_runs):
    write_jsonl(ds.SESSION_REWARDS_FILE, [
        school_rec('2026/10/08 09:00', {'力量': 7}, 8),
        work_rec('2026/10/08 11:00', coins=28, workpoints=3, ad_coins=10),
    ])
    text = ds.format_work(ds.summarize(DAY, study_s=7 * 3600, work_s=5 * 3600))
    assert '学习 7.0 小时' in text and '打工 5.0 小时' in text
    assert '力量 +7' in text and '智力 +0' in text and '魅力 +0' in text
    assert '学分：+8' in text
    assert '工分：+3' in text and '金币：+28' in text
    assert '看视频补贴：+10' in text


def test_work_summary_says_not_run_when_side_has_no_sessions(isolate_runs):
    write_jsonl(ds.SESSION_REWARDS_FILE, [work_rec('2026/10/08 11:00', coins=28, workpoints=3)])
    text = ds.format_work(ds.summarize(DAY, study_s=0, work_s=3600))
    assert '今天没上课' in text
    assert '打工 1.0 小时（1 次）' in text


def test_work_summary_flags_sessions_without_parsed_rewards(isolate_runs):
    """时长账本有、结算明细却一条没记录：要说"没记录到明细"，不能自相矛盾。"""
    text = ds.format_work(ds.summarize(DAY, study_s=3600, work_s=0))
    assert '学习 1.0 小时' in text
    assert '没记录到结算明细' in text
    assert '今天没上课' not in text


def test_adventure_report_states_count_and_coins(isolate_runs):
    write_jsonl(ds.ADVENTURE_LIVE_FILE, [
        {'ts': f'{DAY} 10:00:00', 'coins': 20, 'sig': 'a'},
        {'ts': f'{DAY} 10:01:00', 'coins': 0, 'sig': 'b'},
        {'ts': f'{DAY} 10:02:00', 'coins': 100, 'sig': 'c'},
    ])
    text = ds.format_adventure(DAY, ds.summarize(DAY))
    assert '共 3 次' in text and '120 金币' in text
    assert '2 次有收益' in text and '1 次空手' in text


def test_adventure_report_handles_day_without_adventures(isolate_runs):
    assert '没有冒险记录' in ds.format_adventure(DAY, ds.summarize(DAY))


def test_tail_reader_ignores_earlier_records_beyond_limit(isolate_runs, monkeypatch):
    """只回读文件尾部：早期记录被截掉不影响"当天"这条账。"""
    rows = [school_rec(f'2026/01/01 0{i % 10}:00', {'力量': 1}, 1, ts='2026-01-01 10:00:00')
            for i in range(200)]
    rows.append(school_rec('2026/10/08 09:00', {'智力': 9}, 7))
    write_jsonl(ds.SESSION_REWARDS_FILE, rows)
    monkeypatch.setattr(ds, 'TAIL_BYTES', 500)
    # 截断后仍要能读到当天那条（它在文件末尾），且不把旧记录算进来
    data = ds.summarize(DAY)
    assert data['school']['credits'] == 7


def test_report_helpers_never_raise_on_garbage(isolate_runs):
    """收益文件是垃圾内容时也要能生成文案（调用点在调度主循环里）。"""
    ds.SESSION_REWARDS_FILE.write_text('\x00\x00broken', encoding='utf-8')
    ds.ADVENTURE_LIVE_FILE.write_text('broken', encoding='utf-8')
    title, content = ds.report_work_summary(DAY, 0, 0)
    assert title == ds.WORK_SUMMARY_TITLE and content
    title, content = ds.report_adventure(DAY)
    assert title == ds.ADVENTURE_REPORT_TITLE and content

"""日内收益汇总（收工总账 / 冒险日报）—— src/daily_summary.py 的行为测试。

重点盯三件事：
1. **归属哪天**：学习/打工按结算页自带日期（`sig`）归属，跨零点结算算前一天，
   与 dashboard.rewards_data 同口径；
2. **只统计能确定的字段**：没解析出的不收进合计，且用 `*_n` 区分"没收益"与"没解析出来"；
3. **重复记录不重复计**：冒险同一结算页可能被两条收尾路径各记一次。
"""
import json

from src import daily_summary as ds
from src import notify

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
    """总账正文结构（用户钉死）：标题行 → 学习汇总行 → 学习表 → 打工汇总行 → 打工表。"""
    write_jsonl(ds.SESSION_REWARDS_FILE, [
        school_rec('2026/10/08 09:00', {'力量': 7}, 8),
        work_rec('2026/10/08 11:00', coins=28, workpoints=3, ad_coins=10),
    ])
    text = ds.format_work(ds.summarize(DAY, study_s=7 * 3600, work_s=5 * 3600))
    # 首行：emoji + 日期 + 「打工学习日报」
    assert text.startswith(f'🐾 <b>{DAY} 打工学习日报</b>')
    # 学习汇总行：总学分 + **只列有数据的属性**（0 不写）
    assert '<b>今日学习</b> 学分 +8 力量 +7' in text
    assert '魅力 +0' not in text and '智力 +0' not in text
    # 学习表：时间 / 课程 / 学分 / 属性
    assert '<table bordered striped>' in text
    assert '<th>时间</th><th>课程</th>' in text
    assert '<td>09:00</td>' in text and '<td align="right">+8</td>' in text
    # 打工汇总行：总工分 + 总金币
    assert '<b>今日打工</b> 工分 +3 金币 +28' in text
    # 打工表：时间 / 岗位 / 金币 / 工分（本次没雇人 → 「雇佣」全空列被丢掉防折行）
    assert '<th>时间</th><th>岗位</th><th align="right">金币</th>' in text


def test_work_summary_uses_br_not_raw_newlines(isolate_runs):
    """段间必须用 `<br>`，**裸换行会被 HTML 折叠成空格**（用户实报：标题与
    「今日学习」挤成同一行）。表格之所以不黏是因为它是块级元素，与换行无关。
    """
    write_jsonl(ds.SESSION_REWARDS_FILE, [
        school_rec('2026/10/08 09:00', {'力量': 7}, 8),
        work_rec('2026/10/08 11:00', coins=28, workpoints=3),
    ])
    text = ds.format_work(ds.summarize(DAY))
    assert '<br>' in text
    # 标题与学习汇总行之间靠 <br> 分隔（不是裸换行）
    assert f'打工学习日报</b><br>' in text
    # 表格之外不该还有靠裸 \n 分段的正文（表格标签自身的 \n 是结构，不是分段）
    head = text.split('<table')[0]
    assert '\n' not in head


def test_br_is_not_confused_with_newline_in_plain_fallback(isolate_runs):
    """`<br>` 在纯文本渠道要被还原成换行 —— 不能漏成字面 `<br>`。"""
    write_jsonl(ds.SESSION_REWARDS_FILE, [
        school_rec('2026/10/08 09:00', {'力量': 7}, 8)])
    text = ds.format_work(ds.summarize(DAY))
    plain = notify._escape_plain(text)
    assert '<br>' not in plain
    assert plain.startswith(f'🐾 {DAY} 打工学习日报\n')


def test_all_dash_column_is_dropped_to_avoid_wrapping(isolate_runs):
    """整列都是 `—` 的列要丢掉 —— 它会把表挤到折行、连数字都劈开。

    实测用户截图：六列时 `01:43` 被折成 `01:4`+`3`、`+569` 折成 `+56`+`9`，
    而「雇佣」列在这些行全是 `—`。
    """
    write_jsonl(ds.SESSION_REWARDS_FILE, [
        work_rec('2026/10/08 11:00', coins=569, workpoints=54),
        work_rec('2026/10/08 12:00', coins=456, workpoints=54),
    ])
    text = ds.format_work(ds.summarize(DAY))
    assert '<th>雇佣</th>' not in text          # 全空列不输出
    assert '<th>时间</th><th>岗位</th><th align="right">金币</th>' in text


def test_hire_column_still_shows_when_someone_was_hired(isolate_runs):
    """只要有一行真有雇佣关系，该列就要保留（不能误删有数据的列）。"""
    write_jsonl(ds.SESSION_REWARDS_FILE, [
        {**work_rec('2026/10/08 11:00', coins=569, workpoints=54),
         'hired_pet': '张二狗', 'hired_owner': '柠檬'},
        work_rec('2026/10/08 12:00', coins=456, workpoints=54),
    ])
    text = ds.format_work(ds.summarize(DAY))
    assert '<th>雇佣</th>' in text
    assert '<td>张二狗</td>' in text and '<td>—</td>' in text


def test_time_and_job_columns_are_never_dropped(isolate_runs):
    """前两列（时间/岗位）永不删 —— 那是每行的身份，删了表就没意义了。"""
    write_jsonl(ds.SESSION_REWARDS_FILE, [
        {'ts': f'{DAY} 11:00:00', 'kind': 'work', 'sig': f'{DAY} 11:00',
         'coins': None, 'workpoints': None, 'job': '', 'job_duration': ''}])
    text = ds.format_work(ds.summarize(DAY))
    assert '<th>时间</th>' in text and '<th>岗位</th>' in text


def test_adventure_report_uses_br_too(isolate_runs):
    """冒险日报同样要 `<br>`：标题与表格之间不能靠裸换行。"""
    write_jsonl(ds.ADVENTURE_LIVE_FILE, [
        {'ts': f'{DAY} 10:00:00', 'coins': 20, 'sig': 'a', 'settle': []}])
    text = ds.format_adventure(DAY, ds.summarize(DAY))
    assert f'冒险日报</b><br><br><table' in text


def test_work_summary_drops_everything_else(isolate_runs):
    """**其余文案一律不要**（用户明确）：时长/合计行/平均/结束语都不该出现。"""
    write_jsonl(ds.SESSION_REWARDS_FILE, [
        school_rec('2026/10/08 09:00', {'力量': 7}, 8),
        work_rec('2026/10/08 11:00', coins=28, workpoints=3),
    ])
    text = ds.format_work(ds.summarize(DAY, study_s=7 * 3600, work_s=5 * 3600))
    for gone in ('小时', '合计', '平均', '两项都已跑完', '节）', '次）',
                 '属性未解析出', '节课'):
        assert gone not in text, gone


def test_attr_summary_omits_zero_attributes(isolate_runs):
    """属性**只写当天真有涨的**（用户要求）：`魅力 +0 / 智力 +0` 是噪音。"""
    write_jsonl(ds.SESSION_REWARDS_FILE, [
        school_rec('2026/10/08 09:00', {'力量': 216}, 30),
    ])
    text = ds.format_work(ds.summarize(DAY))
    assert '力量 +216' in text
    assert '魅力 +0' not in text and '智力 +0' not in text
    assert '/ 魅力' not in text and '/ 智力' not in text


def test_attr_summary_keeps_order_for_multiple_attrs(isolate_runs):
    """多门属性都有数据时按 力量/魅力/智力 顺序列（用户指定）。"""
    write_jsonl(ds.SESSION_REWARDS_FILE, [
        school_rec('2026/10/08 09:00', {'魅力': 8}, 10),
        school_rec('2026/10/08 10:00', {'力量': 7}, 10),
        school_rec('2026/10/08 11:00', {'智力': 5}, 10),
    ])
    text = ds.format_work(ds.summarize(DAY))
    assert '力量 +7 / 魅力 +8 / 智力 +5' in text


def test_attr_summary_is_dash_when_nothing_parsed(isolate_runs):
    """一条属性都没解析出来时给 `—`（区别于"这三项真没涨"）。"""
    write_jsonl(ds.SESSION_REWARDS_FILE, [
        {**school_rec('2026/10/08 09:00', {}, 8)}])
    assert f'学分 +8 {ds.DASH}' in ds.format_work(ds.summarize(DAY))


def test_job_duration_is_shown_on_summary_not_per_row(isolate_runs):
    """档位挪到汇总行：单元格里每行重复 6 个字会把岗位列撑宽、导致折行（用户实报）。

    实测本机 109 条打工里 100 条同档，所以档位属于"整天的属性"而不是"每行的属性"。
    """
    write_jsonl(ds.SESSION_REWARDS_FILE, [
        {**work_rec('2026/10/08 11:00', coins=569, workpoints=54),
         'job': '城区护卫委托', 'job_duration': '45分钟'},
    ])
    text = ds.format_work(ds.summarize(DAY))
    assert '（45分钟）' in text                      # 汇总行带档位
    assert '<td>城区护卫委托</td>' in text            # 单元格不带档位
    assert '<td>城区护卫委托(45分钟)</td>' not in text


def test_job_duration_lists_multiple_when_mixed(isolate_runs):
    """一天里混了不同档位就都列出来（去重）。"""
    write_jsonl(ds.SESSION_REWARDS_FILE, [
        {**work_rec('2026/10/08 11:00', coins=28, workpoints=3),
         'job': '武馆教学助理', 'job_duration': '10分钟'},
        {**work_rec('2026/10/08 12:00', coins=569, workpoints=54),
         'job': '城区护卫委托', 'job_duration': '45分钟'},
    ])
    assert '（10分钟 / 45分钟）' in ds.format_work(ds.summarize(DAY))


def test_job_duration_absent_leaves_summary_clean(isolate_runs):
    """没档位信息时汇总行不留空括号。"""
    write_jsonl(ds.SESSION_REWARDS_FILE, [
        {**work_rec('2026/10/08 11:00', coins=28, workpoints=3), 'job': '武馆教学助理'}])
    text = ds.format_work(ds.summarize(DAY))
    assert '（）' not in text and '（)' not in text


def test_ad_bonus_column_is_not_shown(isolate_runs):
    """「看视频」补贴不进通知（用户要求），但**数据仍要照常落盘/统计**。

    记录里有 `ad_coins` 时通知里既不出现该列、也不出现合计的「看视频」；
    而 `summarize()` 的汇总值要保留（仪表盘收益记录页仍在用）。
    """
    write_jsonl(ds.SESSION_REWARDS_FILE, [
        work_rec('2026/10/08 11:00', coins=28, workpoints=3, ad_coins=10)])
    data = ds.summarize(DAY)
    text = ds.format_work(data)
    assert '<th>补贴</th>' not in text and '看视频' not in text
    assert 'ad_coins' not in text
    assert data['work']['ad_coins'] == 10        # 数据没丢，只是不显示


def test_numeric_columns_are_right_aligned(isolate_runs):
    """数字列右对齐 —— 右对齐的数字才好逐位比大小，这是表格相对纯文本的价值。"""
    write_jsonl(ds.SESSION_REWARDS_FILE, [
        work_rec('2026/10/08 11:00', coins=28, workpoints=3, ad_coins=10)])
    text = ds.format_work(ds.summarize(DAY))
    assert '<td align="right">+28</td>' in text
    # 文本列不该带 align
    assert '<td>11:00</td>' in text


def test_work_row_shows_who_was_hired(isolate_runs):
    """打工表带「雇佣」列：这次雇了谁（用户要求）。"""
    write_jsonl(ds.SESSION_REWARDS_FILE, [
        {**work_rec('2026/10/08 11:00', coins=531, workpoints=54),
         'hired_pet': '张二狗', 'hired_owner': '柠檬'},
        {**work_rec('2026/10/08 12:00', coins=28, workpoints=3),
         'hired_pet': '杨小喵', 'hired_owner': '喵帕斯~'},
    ])
    text = ds.format_work(ds.summarize(DAY))
    assert '<th>雇佣</th>' in text
    assert '<td>张二狗</td>' in text and '<td>杨小喵</td>' in text


def test_being_hired_is_marked_with_direction(isolate_runs):
    """被别人雇（加成被对方拿走）要标出方向，别让人误读成"这次雇了他"。"""
    write_jsonl(ds.SESSION_REWARDS_FILE, [
        {**work_rec('2026/10/08 11:00', coins=357, workpoints=3),
         'hired_by': '张二狗', 'hired_by_owner': '柠檬'},
    ])
    assert '<td>被张二狗雇</td>' in ds.format_work(ds.summarize(DAY))


def test_work_row_without_hire_info_has_no_hire_column(isolate_runs):
    """没雇人时「雇佣」列整体不出现（全空列会被丢以防折行，见上）。"""
    write_jsonl(ds.SESSION_REWARDS_FILE, [work_rec('2026/10/08 11:00', coins=28, workpoints=3)])
    assert '<th>雇佣</th>' not in ds.format_work(ds.summarize(DAY))


def test_old_records_without_hire_fields_still_render(isolate_runs):
    """历史记录（落盘时还没有雇佣字段）照样能渲染，不能 KeyError 或漏列。"""
    write_jsonl(ds.SESSION_REWARDS_FILE, [work_rec('2026/10/08 11:00', coins=28, workpoints=3)])
    row = next(line for line in ds.summarize(DAY)['work_rows'])
    assert row[2] == ds.DASH


def test_only_attributes_that_actually_appeared_get_a_column(isolate_runs):
    """只列今天真出现过的属性列 —— 否则每天都有整列空值，手机上白占宽度。"""
    write_jsonl(ds.SESSION_REWARDS_FILE, [
        school_rec('2026/10/08 09:00', {'魅力': 5}, 6),
    ])
    text = ds.format_work(ds.summarize(DAY))
    header = next(line for line in text.splitlines() if line.startswith('<tr><th>'))
    assert '魅力' in header and '力量' not in header and '智力' not in header


def test_per_session_rows_show_time_course_and_reward(isolate_runs):
    """每次课的明细行要能定位到"几点、哪门课、拿到多少"。"""
    write_jsonl(ds.SESSION_REWARDS_FILE, [
        {**school_rec('2026/10/08 09:00', {'力量': 7}, 8), 'course': '武术身法课'},
        {**school_rec('2026/10/08 09:30', {'力量': 3}, 4), 'course': '障碍挑战课'},
    ])
    text = ds.format_work(ds.summarize(DAY))
    assert '09:00' in text and '武术身法课' in text
    assert '09:30' in text and '障碍挑战课' in text


def test_rows_fall_back_to_settlement_time_when_sig_has_no_clock(isolate_runs):
    """sig 退化成内容 md5 时，明细行的时间取落盘时刻，不能是空白。"""
    write_jsonl(ds.SESSION_REWARDS_FILE, [
        school_rec('deadbeef1234', {'力量': 1}, 2, ts=f'{DAY} 13:45:09')])
    assert '13:45' in ds.format_work(ds.summarize(DAY))


def test_table_rows_are_capped_and_summary_still_counts_all(isolate_runs):
    """明细表只列最近若干条，但**汇总行按全部记录算**（表被截断不能把账也算小）。"""
    rows = [school_rec(f'2026/10/08 0{i % 10}:{i % 60:02d}', {'力量': 1}, 1)
            for i in range(ds.MAX_TABLE_ROWS + 5)]
    write_jsonl(ds.SESSION_REWARDS_FILE, rows)
    text = ds.format_work(ds.summarize(DAY))
    assert f'仅列最近 {ds.MAX_TABLE_ROWS} 条' in text
    assert f'共 {len(rows)} 条' in text
    # 汇总行按全部 35 节算（学分与力量各 +1 × 35）
    assert f'学分 +{len(rows)} 力量 +{len(rows)}' in text


def test_unparsed_numbers_are_dashes_not_zeros(isolate_runs):
    """没解析出的数值显示破折号：写成 0 会被当成"这次真没拿到钱"。"""
    write_jsonl(ds.SESSION_REWARDS_FILE, [
        {**work_rec('2026/10/08 11:00'), 'job': '', 'coins': None,
         'workpoints': None, 'ad_coins': None},
    ])
    text = ds.format_work(ds.summarize(DAY))
    assert ds.DASH in text
    assert f'工分 {ds.DASH}' in text and f'金币 {ds.DASH}' in text


def test_summary_text_stays_inside_rich_message_budget(isolate_runs):
    """满表（30 行 × 两张）也要留在 Telegram 富消息上限内。

    富消息上限 32k（普通消息才 4k），所以这里按富消息算 —— 超限会被整条拒收。
    """
    from src.notify import RICH_LIMIT
    rows = [school_rec(f'2026/10/08 {i % 24:02d}:{i % 60:02d}', {'魅力': 12}, 30)
            for i in range(ds.MAX_TABLE_ROWS)]
    works = [work_rec(f'2026/10/08 {i % 24:02d}:{i % 60:02d}', coins=999, workpoints=54,
                      ad_coins=57) for i in range(ds.MAX_TABLE_ROWS)]
    write_jsonl(ds.SESSION_REWARDS_FILE, rows + works)
    text = ds.format_work(ds.summarize(DAY, study_s=12 * 3600, work_s=12 * 3600))
    assert len(text) < RICH_LIMIT


def test_work_summary_says_not_run_when_side_has_no_sessions(isolate_runs):
    write_jsonl(ds.SESSION_REWARDS_FILE, [work_rec('2026/10/08 11:00', coins=28, workpoints=3)])
    text = ds.format_work(ds.summarize(DAY, study_s=0, work_s=3600))
    assert '（今天没上课）' in text
    assert '<b>今日打工</b> 工分 +3 金币 +28' in text


def test_work_summary_flags_sessions_without_parsed_rewards(isolate_runs):
    """时长账本有、结算明细却一条没记录：说"没读到结算明细"，不能自相矛盾。"""
    text = ds.format_work(ds.summarize(DAY, study_s=3600, work_s=0))
    assert '（没读到结算明细）' in text
    assert '（今天没上课）' not in text


def test_adventure_report_is_a_metric_table_not_a_per_run_list(isolate_runs):
    """冒险日报是**指标表**（用户指定七项），不是逐次明细 —— 一天几百次会刷屏。

    指标口径由用户钉死：总次数 / 金币 / 心情 / 清洁 / 体力 / 命中率 / 平均每次。
    """
    write_jsonl(ds.ADVENTURE_LIVE_FILE, [
        {'ts': f'{DAY} 10:00:00', 'coins': 20, 'sig': 'a', 'settle': ['心情值+10']},
        {'ts': f'{DAY} 10:01:00', 'coins': 0, 'sig': 'b', 'settle': ['清洁值+5']},
        {'ts': f'{DAY} 10:02:00', 'coins': 100, 'sig': 'c', 'settle': []},
    ])
    text = ds.format_adventure(DAY, ds.summarize(DAY))
    assert '<table bordered striped>' in text
    assert '<th>指标</th>' in text
    for label in ('总次数', '金币', '心情', '清洁', '体力', '命中率', '平均每次'):
        assert f'<td>{label}</td>' in text, label
    assert '<td align="right">3 次</td>' in text
    assert '<td>金币</td><td align="right">+120</td>' in text
    assert '<td>命中率</td><td align="right">67%</td>' in text    # 3 次里 2 次有收益
    assert '+40.0' in text                          # 平均每次
    # 命中率**不带括号明细**（用户要求去掉"（2 次有收益 / 1 次空手）"）
    assert '有收益' not in text and '空手' not in text
    # 不是逐次列表：不该出现"每条一行"的 settle 明细
    assert '宠物来信' not in text


def test_adventure_report_drops_metrics_the_user_removed(isolate_runs):
    """用户点名删掉的指标不该再出现（含之前那两行结论）。"""
    write_jsonl(ds.ADVENTURE_LIVE_FILE, [
        {'ts': f'{DAY} 10:00:00', 'coins': 300, 'sig': 'a', 'settle': ['心情值+10']},
        {'ts': f'{DAY} 10:01:00', 'coins': 20, 'sig': 'b', 'settle': []},
    ])
    text = ds.format_adventure(DAY, ds.summarize(DAY))
    for gone in ('平均有收益时', '最欧一次', '大额', '属性', '平均每 '):
        assert gone not in text, gone


def test_adventure_report_shows_attribute_totals_per_row(isolate_runs):
    """心情/清洁/体力各占**独立一行**（不是挤在一行的「属性」汇总）。"""
    write_jsonl(ds.ADVENTURE_LIVE_FILE, [
        {'ts': f'{DAY} 10:00:00', 'coins': 20, 'sig': 'a',
         'settle': ['22:31', '心情值+10', '清洁值+5']},
        {'ts': f'{DAY} 10:01:00', 'coins': 0, 'sig': 'b', 'settle': ['体力值+20']},
    ])
    text = ds.format_adventure(DAY, ds.summarize(DAY))
    assert '<td>心情</td><td align="right">+10</td>' in text
    assert '<td>清洁</td><td align="right">+5</td>' in text
    assert '<td>体力</td><td align="right">+20</td>' in text


def test_adventure_report_shows_dash_when_no_attribute_parsed(isolate_runs):
    """属性一条都没解析出来时显示 `—` 而不是 `+0`（0 会被当成"今天真没涨"）。

    与其它指标不同：这三行**始终占位**（用户点名要），所以不能隐藏整行。
    """
    write_jsonl(ds.ADVENTURE_LIVE_FILE, [
        {'ts': f'{DAY} 10:00:00', 'coins': 20, 'sig': 'a', 'settle': []}])
    text = ds.format_adventure(DAY, ds.summarize(DAY))
    for label in ('心情', '清洁', '体力'):
        assert f'<td>{label}</td><td align="right">{ds.DASH}</td>' in text, label


def test_adventure_report_handles_all_empty_day(isolate_runs):
    """全空手的一天不能出现除零。"""
    write_jsonl(ds.ADVENTURE_LIVE_FILE, [
        {'ts': f'{DAY} 10:0{i}:00', 'coins': 0, 'sig': str(i), 'settle': []}
        for i in range(3)])
    text = ds.format_adventure(DAY, ds.summarize(DAY))
    assert '<td>命中率</td><td align="right">0%</td>' in text
    assert '<td>平均每次</td><td align="right">+0.0</td>' in text


def test_adventure_report_has_no_notification_title(isolate_runs):
    """通知标题去掉（用户要求）：正文首行已自报家门，再顶前缀就重复了。

    返回空标题而不是改常量 —— runner 仍按 (title, content) 契约投递，
    各渠道把空标题当"没有标题"处理、不留空行（见 src/notify.py `_join_title`）。
    """
    write_jsonl(ds.ADVENTURE_LIVE_FILE, [
        {'ts': f'{DAY} 10:00:00', 'coins': 20, 'sig': 'a', 'settle': []}])
    title, content = ds.report_adventure(DAY)
    assert title == ''
    assert content.startswith(f'🧭 <b>{DAY} 冒险日报</b>')


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
    assert title == '' and content        # 总账也不带通知标题（首行已自报家门）
    title, content = ds.report_adventure(DAY)
    assert title == '' and content        # 冒险日报不带通知标题（见上面那条测试）

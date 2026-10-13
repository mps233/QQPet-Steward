"""通知里的原生表格 —— src/reward_table.py 的行为测试。

Telegram 走 **Rich Messages 原生表格**（`<table bordered striped>`，
`sendRichMessage` 渲染，`parse_mode=HTML` 不支持 table 会 400）。这里盯三件事：
标签结构正确、数字列右对齐、单元格内容转义（不转义就是整条消息被拒收）。
"""
from src import reward_table as rt


def test_rich_table_emits_native_table_tags():
    html = rt.rich_table(['时间', '课程'], [['09:00', '武术身法课']])
    assert html.startswith('<table bordered striped>')
    assert html.endswith('</table>')
    assert '<th>时间</th><th>课程</th>' in html
    assert '<td>09:00</td><td>武术身法课</td>' in html


def test_rich_table_right_aligns_numeric_columns():
    """数字列右对齐：右对齐才好逐位比大小，是表格相对纯文本的核心价值。"""
    html = rt.rich_table(['时间', '金币'], [['09:00', '+550']], aligns=['left', 'right'])
    assert '<th align="right">金币</th>' in html
    assert '<td align="right">+550</td>' in html
    assert '<td>09:00</td>' in html          # 文本列不带 align


def test_rich_table_can_disable_borders_and_stripes():
    assert rt.rich_table(['a'], [['1']], bordered=False, striped=False).startswith('<table>')


def test_rich_table_escapes_markup_in_cells():
    """单元格里的 `<`/`&` 必须转义，否则 Telegram 解析失败、整条消息 400 拒收。"""
    html = rt.rich_table(['课程'], [['<b>&x']])
    assert '&lt;b&gt;&amp;x' in html
    assert html.count('<td>') == html.count('</td>') == 1


def test_rich_table_handles_ragged_rows():
    """行列数不齐时补空单元格，不能少开一个 `<td>` 把整张表撑坏。"""
    html = rt.rich_table(['a', 'b'], [['只有一列']])
    assert html.count('<td>') == 2 and html.count('</td>') == 2


def test_rich_table_without_rows_is_header_only():
    html = rt.rich_table(['时间', '课程'], [])
    assert html.count('<tr>') == 1 and '<td>' not in html


def test_escape_handles_ampersand_before_angle_brackets():
    assert rt.escape('<a & b>') == '&lt;a &amp; b&gt;'
    assert rt.escape('a &amp; b') == 'a &amp;amp; b'   # 已转义的实体再转义也安全


# ---------------------------------------------------------------- 富消息正文换行

def test_rich_lines_uses_br_because_raw_newlines_collapse():
    """**裸 `\\n` 会被 HTML 折叠成空格** —— 段间必须用 `<br>`（用户实报踩过）。

    现象：「🐾 日期 打工学习日报」与「今日学习 …」渲染成同一行。
    表格不黏是因为 `<table>` 是块级元素，与换行无关。
    """
    assert rt.rich_lines(['甲', '乙']) == '甲<br>乙'
    assert rt.rich_lines(['标题', '', '正文']) == '标题<br><br>正文'
    assert '\n' not in rt.rich_lines(['甲', '乙'])


def test_rich_lines_passes_table_html_through():
    """表格整段作为一个元素传入，内部换行是结构、不该被当分段处理。"""
    table = rt.rich_table(['a'], [['1']])
    out = rt.rich_lines(['标题', '', table])
    assert out == f'标题<br><br>{table}'
    assert out.count('<table') == 1


# ---------------------------------------------------------------- 丢全空列

def test_drop_empty_columns_removes_all_dash_column():
    """整列都是 `—` 的列要丢掉：它会把表挤到折行，连数字都劈开（用户实报）。"""
    header, rows, keep = rt.drop_empty_columns(
        ['时间', '岗位', '雇佣', '金币'],
        [['01:43', '城区护卫委托', '—', '+569'],
         ['02:29', '城区护卫委托', '—', '+456']])
    assert header == ['时间', '岗位', '金币']
    assert keep == [0, 1, 3]                 # 原列下标，供同步裁剪 aligns
    assert rows[0] == ['01:43', '城区护卫委托', '+569']


def test_drop_empty_columns_keeps_column_with_any_value():
    header, _rows, keep = rt.drop_empty_columns(
        ['时间', '雇佣'], [['01:43', '张二狗'], ['02:29', '—']])
    assert header == ['时间', '雇佣'] and keep == [0, 1]


def test_drop_empty_columns_never_drops_the_first_two():
    """前两列是每行的身份（时间/岗位），即使全空也不删，否则表没意义。"""
    header, _rows, _keep = rt.drop_empty_columns(['时间', '岗位'], [['—', '—']])
    assert header == ['时间', '岗位']


def test_drop_empty_columns_handles_no_rows():
    header, rows, keep = rt.drop_empty_columns(['时间', '岗位'], [])
    assert header == ['时间', '岗位'] and rows == [] and keep == [0, 1]


def test_pick_keeps_aligns_in_sync_with_dropped_columns():
    """裁列时必须同步裁 aligns，否则对齐属性整体错位（金币变左对齐，踩过）。"""
    aligns = ['left', 'left', 'left', 'right', 'right']
    assert rt.pick(aligns, [0, 1, 3, 4]) == ['left', 'left', 'right', 'right']
    assert rt.pick(aligns, [0, 1, 2, 3, 4]) == aligns
    assert rt.pick(aligns, [0, 1]) == ['left', 'left']

"""通知里的**原生表格**（Telegram Rich Messages）。

Bot API 10.1（2026-06）起支持 Rich Messages：`sendRichMessage` 能渲染原生表格
（`<table bordered striped>`，带框线与斑马纹）。这是拿到"真表格"观感的唯一途径：

- 旧的 `sendMessage` + `parse_mode=HTML` **不支持** `<table>` —— 塞进去会 400 拒收；
- 等宽 `<pre>` 会被渲染成灰底代码块（观感是代码，不是表格），已否掉；
- 画成图片再发（sendPhoto）也不对：用户要的是 TG 自己的表格样式，不是图片。

其它渠道（飞书/桌面/OnePush/仪表盘历史）没有富消息，由 `src/notify.py` 把这同一份
HTML 剥成「一行一条、制表符分列」的纯文本 —— 不要用空格假装对齐出一张表，那在非
等宽字体里只会更乱。

纯函数、不碰文件也不发通知，方便单独测。
"""
from __future__ import annotations


def escape(text) -> str:
    """转义富消息 HTML 里的裸 `&`/`<`/`>`。

    必须先 `&` 后尖括号，且**必须转义**：Telegram 解析失败会 400 拒收整条消息。
    """
    return (str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


# 富消息 HTML 里的换行必须写成 `<br>`，**裸 `\\n` 会被 HTML 折叠成空格**——
# 曾经把普通文本的换行规则套到 HTML 上，结果「标题」「今日学习」挤成同一行
# （用户实报截图）。`<table>` 之所以不黏是因为它是块级元素，与换行无关。
BR = '<br>'


def rich_lines(parts) -> str:
    """把若干段拼成富消息正文：段间用 `<br>`（不是 `\\n`）。

    段落之间要空一行就给一个空串（`['甲', '', '乙']` → `甲<br><br>乙`）。
    表格 HTML 整段作为一个元素传入 —— 它自带块级语义，内部换行会被忽略。
    """
    return BR.join(str(part) for part in parts)


def drop_empty_columns(header, rows, empty_mark: str = '—', keep_min: int = 2):
    """丢掉**整列都是空值**的列（默认判据是破折号），返回 (header, rows, keep)。

    手机上表格列数直接决定每格宽度：列一多，Telegram 会把窄列里的内容硬折行，
    连数字都会被劈开（实测 `01:43` 折成 `01:4`＋`3`、`+569` 折成 `+56`＋`9`，
    这种数字断开会被误读）。「雇佣」列在没雇人的日子整列都是 `—`，
    正是它把表挤到折行 —— 这类列直接不输出。

    **同时返回 `keep`（保留下来的原列下标）**：调用方要拿它同步裁剪 `aligns`，
    否则删了一列之后对齐属性会整体错位（实测：金币列变成了左对齐，这是 bug）。

    `keep_min` 保证不会把表删空（前几列是每行的身份，不该被删）。
    """
    header, rows = list(header), [list(r) for r in rows]
    if not rows:
        return header, rows, list(range(len(header)))
    keep = [i for i in range(len(header))
            if i < keep_min
            or any(str(r[i]).strip() not in ('', empty_mark)
                   for r in rows if i < len(r))]
    return [header[i] for i in keep], [[r[i] for i in keep] for r in rows], keep


def pick(items, keep) -> list:
    """按 `keep` 下标挑元素（与 `drop_empty_columns` 配套裁剪 aligns）。"""
    return [items[i] for i in keep if i < len(items)]



def rich_table(header, rows, *, aligns=None, bordered: bool = True,
               striped: bool = True) -> str:
    """生成 Telegram 富消息的**原生表格** HTML。

    首行用 `<th>` 当表头；`align="right"` 让数字列右对齐（右对齐的数字才好逐位
    比较大小，这是表格相对纯文本的核心价值）。
    `bordered`/`striped` 对应 Bot API `InputRichBlockTable` 的框线与斑马纹参数。
    """
    attrs = ' '.join(name for name, on in (('bordered', bordered),
                                           ('striped', striped)) if on)
    aligns = list(aligns or []) + ['left'] * len(header)
    out = [f'<table{" " + attrs if attrs else ""}>']
    cells = ''.join(
        f'<th{align_of(aligns, i)}>{escape(name)}</th>'
        for i, name in enumerate(header))
    out.append(f'<tr>{cells}</tr>')
    for row in rows:
        tds = ''.join(
            f'<td{align_of(aligns, i)}>'
            f'{escape(row[i] if i < len(row) else "")}</td>'
            for i in range(len(header)))
        out.append(f'<tr>{tds}</tr>')
    out.append('</table>')
    return '\n'.join(out)


def align_of(aligns, index: int) -> str:
    """该列的对齐属性（只有右对齐需要显式声明，左对齐是默认值）。"""
    return ' align="right"' if index < len(aligns) and aligns[index] == 'right' else ''

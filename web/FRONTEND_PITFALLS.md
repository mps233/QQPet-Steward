# 前端（React 版 web/）踩坑清单

**动手改 `web/` 之前先读这份。** 每一条都对应一次真实事故，写明"改了 X 就必须验 Y"，
而不是泛泛的"要小心"。

---

## 一、改完必须验证的三件事

改任何涉及**高度 / 定位 / overflow / 容器层级**的 CSS 或 JSX，提交前必须跑探针量数值。

| 改了什么 | 必须量 | 期望 |
| --- | --- | --- |
| `height` / `min-height` / `position` | `.home` 的高度 | ≈ 视口高 |
| `overflow` | `.home` 的 `scrollHeight - clientHeight` | 0（该页本来就不该滚时） |
| 包了一层容器 | `grep -n "main >"` 逐条确认还匹配 | 无失效 |

### ⚠️ 宽屏转场：位移量不能按视口算（2026-10-20，用户实报"旧页停在左侧空白区"）

`.page-slot` 是 `inset:0`（= 整个视口），而内容被 `--page-max` 收进中间 430 宽一列。
原来的转场 `translateX(-30%)` 百分比相对**元素自身宽度** → 手机 117px 看不出来，
**1440 宽桌面就是 432px**：旧页那一列（居中的 430）被推到 x73，而新页只有中间一列
有背景、slot 本身透明，左侧 432px 就把旧页露出来 —— 这就是"切完页它还赖在左边"。

**三层修法，缺一不可**：
1. 位移改走 `--slide-w: min(100vw, calc(var(--page-max) * 1.1))`，宽屏被版心卡住；
2. 离场必须**淡出到 opacity:0** —— 只位移不淡出的话，只要没被完全盖住就有残影；
3. 宽屏（≥640px）**直接不横滑**，只做 200ms 交叉淡入（`@media` 里重定义同名 keyframes，
   后写的胜出）。宽屏本来就没有"前后页"的空间感。

**别给 `.page-slot` 铺底色去"盖住"离场页**：总览页的房间背景图挂在 `html` 上
（`.v2-home` 自己是透明的），给 slot 一层纯色会把房间背景整片盖掉。

**兜底定时器也要跟着动画时长走**（`App.tsx` 的 `leaveTimer`）：原先写死 600ms，
而宽屏动画只有 200ms —— 万一 `animationend` 没抛，离场页会多停 400ms，症状同上。
现在在 `leaving` 的 layoutEffect 里读 `getComputedStyle(el).animationDuration` 重设，
动画被禁用（reduced-motion）时按 0ms 立即收尾。

**验证方法**（headless 的虚拟时钟不推进合成器，**量位置/opacity 全是死值**）：
用 `el.getAnimations()` 读实际 keyframes 与 duration；用轮询量"离场节点何时从 DOM 消失"：

| 视口 | animationDuration | 节点移除时刻 | 期望 |
| --- | --- | --- | --- |
| 1440×900 | 0.2s | ~300ms | ✓ |
| 390×844 | 0.32s | ~410ms | ✓ |
| 1440×900 + reduced-motion | 0s | 0ms | ✓ 立即 |

**为什么 `.home` 的高度这么脆**：`.home` 里全是绝对定位的浮动层（`.flt`），
它**撑不开任何父级**；而 `min-height:100%` 要求**整条祖先链都有确定高度**。
链条断在任何一层 → 百分比失效 → 整页塌成只剩背景。

**这个错犯过 3 次**，断点分别在 `.app`、`main`、`.page-slot`。当时的层级是：

```
body → #root(display:contents) → .app → main → .page-slot → section[data-page]
```

**给 `.home` 或其祖先加 `position:relative` 也会踩这个** —— 一旦某一层变成"由内容撑高"，
链条就断了（实测：加上 relative 之后立刻只剩背景）。

---

## 二、测量环境：headless 测不准什么

### 最小视口 500px（犯过 2 次）

**headless Chrome 的最小视口宽是 500px**。`--window-size=360` 截出的 PNG 是 360 宽，
但页面**仍按 500 排版后裁掉右侧** —— 直接看会误判成"窄屏正常"。

**必须用同源 iframe 承载**：

```html
<!-- 存成 web/dist/assets/_shot.html（放 / 下会 404：路由只服务 index.html 与 /assets/*） -->
<!doctype html><html><body style="margin:0;background:#888">
<iframe src="/?tab=adv" width="360" height="1400" style="border:0;display:block"></iframe>
</body></html>
```
```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new \
  --window-size=380,1420 --hide-scrollbars --virtual-time-budget=18000 \
  --screenshot=/tmp/x.png "http://127.0.0.1:8787/assets/_shot.html"
```

### 完全测不出的东西（只能靠真机）

- `env(safe-area-inset-*)` —— headless 里**恒为 0**
- iOS 的手势判定（`pointercancel`）
- 惯性滚动与弹性回弹

**→ 用户说这类问题"有"的时候，直接要真机截图，不要在自己的环境里换假设。**
（犯过：绕了四轮才定位到 `.drawer` 溢出，用户给一张截图就清楚了。）

### 宽屏：`--u` 恒 1px ⇒ 必须靠**版心限宽**，不能指望缩放

`computeUnit()` 恒返回 1，所以 `--u` 在 1440 宽的桌面上还是 1px —— 控件不会变大，
**但会被横向撑开**。改之前只有 `.v2-home` 限了 430px，所有内页都没有：实测 1440×900
时设置页 `.fsec` 拉到 1416px、标签与控件相隔 1400px，冒险页图表 1388×342.9
（viewBox 340×84 被等比放大 4.07 倍），日志缩略图每张 273×607。

现在统一走 `:root` 的 `--page-max:430px`：总览 V2 与
`main section[data-page]:not([data-page="main"])` 共用，`@media(max-width:639px)`
里把阴影关掉（视口比版心窄时限宽等于不生效，留着那圈阴影反而是多余的边）。

**⚠️ 同一个块里 `margin` 简写会静默吃掉居中**（2026-10-20，用户截图实报）：
内页那条规则里我先写了 `margin-left/right:auto`，紧接着原有的 `margin:0`
把左右又重置成 0 —— 限宽生效了、居中没生效，结果是**贴左的一条 430 宽窄栏、
右边一大片空白**。同块里的 `box-shadow:none` 也照样压掉了上面刚加的阴影。
改这类长规则时，**长写与简写不能共存**；探针必须同时断言 `left` 和 `width`，
只看宽度会漏掉"限宽了但没居中"（我当时就是只量了宽度就放过了）。

**职业树是例外**：它官方就整屏铺满（深棕渐变），不能套白卡那套限宽 ——
一旦限宽，背景也跟着被裁成一条。所以 `section[data-page="plan"]` 解除限宽、
自己铺满同色渐变（与 `.ct-page` 同一渐变，两层接缝看不出来），只让 `.ct-page`
这一层限宽居中。验证判据：`section` 宽 == 视口宽，且 `.ct-page` 左右留白相等。

**两处连带规则，别漏**：
- `.ct-attr` / `.ct-tiers` / `.ct-tier{padding-left}` 原用 `100vw` / `5vw` ——
  那是**视口**宽，与限宽后的 `.ct-page` 无关。改成 JS 与三把尺子同源写入的 `--ct-cw`。
  （不要用 `container-type:inline-size` + `cqw`：containment 会让 `.ct-page` 变成
  `position:fixed` 后代的包含块，弹窗遮罩 `.ct-mask` 就不再相对视口定位。）
- SVG 图表 `width:100% + height:auto` 会等比放大，配 `max-height` 封顶，
  否则宽屏下一张图占掉 2/5 屏高。

### 探针的输出必须单行

`JSON.stringify(x, null, 1)` 是多行 → `grep -oE 'data-probe="[^"]*"'` 抓不到。**别加缩进参数。**

---

## 三、iOS 手势 × 滚动容器（反复出现的模式）

**只要交互元素处在滚动容器里**（`.page-slot` / `.home` / `.advlist` / `#logbox` 都是），
iOS 会先判定"这是滚动还是交互"，判定成滚动就发 `pointercancel` —— **原来的手势永远收不到后续**。

| 现象 | 正确做法 |
| --- | --- |
| 圆钮点不开面板 | 用 **`onClick`**，不要 `onPointerUp` |
| 拖拽完全没反应 | 用 **`TouchSensor` + `MouseSensor`**，**不要 `PointerSensor`**（dnd-kit 文档里写着） |
| 拖拽不动（更隐蔽的一种） | `touch-action` 必须**静态**声明；等 `onDragStart` 再加 class 已经晚了——浏览器在**触摸开始那一刻**就读了它 |

### 滚动位置记忆：容器是 `.page-slot`，**不是** `html` / `body`

设置一级菜单滚到下面 → 点进二级 → 返回时停在顶部（用户实报）：二级内容短，滚动位置
被浏览器 clamp 掉，回一级时不会自动恢复。实现见 `App.tsx` 的 `scrollMemo` / `saveScroll`：
**在导航入口（DOM 更新前）**存下 scrollTop，视图 key（`page` 或 `page:二级键`）变了再写回。

**两个必须知道的坑**：

1. **取错容器 = 静默失效**。`document.scrollingElement` 指向 `html`（本项目 `html` 是
   `overflow:hidden` 的固定画布），`body` 也没有可滚高度——对它们写 `scrollTop` 读回恒 0，
   现象是"记忆了但没生效"。实测（390×420 的 iframe 打开设置页，把每个候选都拉出来量）：

   | 元素 | scrollHeight | clientHeight | 可滚 | 写 scrollTop=300 读回 |
   | --- | --- | --- | --- | --- |
   | `html` | 420 | 420 | 0 | 0 ✗ |
   | `body` | 420 | 420 | 0 | 0 ✗ |
   | `main` / `section` / `#root` | 420 / 1000 / 0 | 420 / 1000 / 0 | 0 | 0 ✗ |
   | **`.page-slot.page-enter`** | **1000** | **420** | **580** | **300 ✓** |

   所以取容器的正确方式是 `document.querySelector('.page-slot.page-enter')`（并校验
   `scrollHeight > clientHeight`）。
2. **转场期间退场页 `.page-leave-*` 也在 DOM 里**，选择器必须锁 `.page-enter`，否则可能
   记到／写回正在退场的那一页。

**验证**：同源 iframe 里 `slot.scrollTop = 500` → 点菜单进二级 → `history.back()` →
断言 `slot.scrollTop === 500`；**再换个值（250）复测一遍**，确认恢复的是"上次那个值"
而不是写死的常量。

---

## 四、选择器与特异性

### 直系子选择器会被"多包一层"整条打断

`main > section[data-page]:not([data-page="main"])` 这类规则，一旦中间插入 `.page-slot`，
**整条失效** → 样式静默回退（这次表现为内页背景从浅灰变米黄、内容贴边）。

**改结构前 `grep -n "main >"`，改后逐条确认。**

### 绝对定位子组件搬进正常流：`position:static` 会连带改掉「长高时盖住谁」

V2 把所有 `.flt` 组件改成 `position:static` 参与文档流。这**不只是**换定位：

- 视觉上"卡片变高了"在 V1 里是**浮层向下盖住**（`.idcard.open{z-index:6}`，
  胶囊/任务卡坐标恒定）；
- 在 V2 里同一份 `height` 变化会**把下面的胶囊和整个任务卡顶下去**。

用户实报（2026-10-09）："面板展开以后把下面的东西往下顶，之前不是覆盖上去吗"。
实测（390×844，`?ui=v1` 与默认 V2 并排，探针量 `getBoundingClientRect`）：

| | 卡片 | 胶囊 y | 任务卡 y |
| --- | --- | --- | --- |
| V1 收起 → 展开 | h 46 → 179 | 92.3 → **92.3** | 162.3 → **162.3** |
| V2 收起 → 展开（修前） | h 46 → 179 | 86.0 → **219.0** | 156.0 → **289.0** |

**修法**：用与增量**等量**的负 `margin-bottom` 在流里抵消（`overview-v2.css` 的
`.v2-home .v2-top .idcard.open`）—— 视觉照旧长高、占位恒定。
两个硬约束：① 负 margin 与行区高度必须同源（走 `app.css` 的 `--idrows-h`，
别各写一个 133）；② 过渡时长/缓动要与 `.idrows` 的 `height` 一致，
否则收起时下方"先跳一下再收"。**只在 V2 作用域内加，V1 一行都别动。**

→ **搬组件到正常流时，逐条问"这个元素在旧版是浮层还是流内元素"**：
浮层的"长高/位移"在流里都会变成"推挤兄弟"，全部要单独处理。

### `:not(...)` 的参数也计入特异性

`main section[data-page]:not([data-page="main"])` 是 **(0,2,2)**，不是 (0,1,2)。
想覆盖它得凑到同级别（加个 `.card` 之类），靠"同特异性时后写的胜出"。

### 改 CSS 前先 grep 有没有 `@media` 里的同名规则

`.advlist .ai` 有两份：基规则 126px + `@media(max-width:360px)` 里 96px。
只改基规则 → 真机（≤360 宽）没变化。**用户手机命中的是窄屏分支。**

### ⚠ 舞台已不缩放 ⇒ 断点判"放不放得下"只能用视口高度（2026-10-20）

`lib/stage.ts` 的 `computeUnit()` **恒返回 1**（1u = 1 CSS px，控件不随视口缩放）。
所以整个舞台是**固定像素**的（约 360×700），"放不放得下"只取决于视口高度。

两条 `@media (min-aspect-ratio:360/700)` / `360/620` 因此**判反了** ——
`min-aspect-ratio` 意思是"宽高比 ≥"，360/700≈0.514 太小，几乎把所有屏都圈进来：

| 视口 | 宽高比 | 命中 360/700 | 命中 360/620 | 实际该不该收紧 |
| --- | --- | --- | --- | --- |
| 390×844（iPhone 竖屏） | 0.462 | ✗ | ✗ | **该（却漏了）** |
| 430×932 | 0.461 | ✗ | ✗ | **该（却漏了）** |
| 320×568 | 0.563 | ✓ | ✗ | 该 |
| 768×1024（平板） | 0.750 | ✓ | ✓ | **不该（却命中了）** |
| 1440×900（桌面） | 1.600 | ✓ | ✓ | **不该（却命中了）** |

后果：竖屏手机的 `--gap-top` 一直是 12（没收紧）；平板/桌面被压到 6，
而且 `.funcbar .fb-group:nth-child(2){display:none}`（隐藏「画面」按钮）在 **V1 上把桌面**
**的画面按钮藏了、小屏反而留着**。`overview-v2.css` 当时发现了这点、打了 `!important` 补丁，
但那只是把 V2 遮住，判据本身还是错的。

现在两条都改成 `@media (max-height:700px)` / `(max-height:620px)`，V2 的补丁已撤。
规则：**新增断点先想清楚"这个量随视口高变还是随宽变"** —— 判宽度用 `max-width`，
判"舞台放不放得下"用 `max-height`，判字体/密度才考虑 `min-width`。

### ⚠ 文字居中别用 `transform: translate(-50%,-50%)`（2026-10-19，iOS PWA 糊字）

**症状**：iPhone 主屏 PWA 里，页面**上半部**的文字糊成一团，同一屏下半部的字却锐利。
第一反应都会怀疑安全区/背景图，都不是。

**真凶**：那些位置的元素用 `left:50% + translateX(-50%)`（或 `top:50% + translateY(-50%)`）
做居中 —— transform 会把元素提升成独立合成层，iOS 上这层文字按**低分辨率**栅格化，
再用 GPU 放大贴回去，于是笔画边缘全是半透明过渡。

**逐像素判据**（截图里量「最强水平梯度 ÷ 该处明暗跨度」，1.0 = 完全锐利）：

| 元素 | 相对锐度 | 居中方式 |
| --- | --- | --- |
| 设置页 `.navtitle`「设置」 | **0.31** | `left:50%+translateX(-50%)` |
| 职业树 `.ct-title`「职业树」 | **0.38** | `top:50%+translateY(-50%)` |
| 同屏「力量 8971」/ 列头 / 层级标签 / 名牌 | **0.79 ~ 0.91** | 正常流 |
| 系统状态栏时间（对照组） | 0.93 | 系统绘制 |

同一屏里差 2~3 倍，且**恰好全是带 transform 的那几个** —— 这就是判据。
（模糊的版本里文字最亮点只有 234，锐利的能到 255：高频细节被削掉了。）

**改法**（位置一模一样，只是不再建合成层）：

```css
/* 水平居中 */
{ left:0; right:0; text-align:center }            /* 文字 */
{ left:0; right:0; margin-left:auto; margin-right:auto; width:max-content }  /* 盒子 */
/* 垂直居中 */
{ top:0; bottom:0; display:flex; align-items:center }   /* 或 line-height */
```

已改：`.navhead .navtitle`、`.ct-title`、`.ct-toast`、`#sceneToast`、
`.shotpage .livetoast`、`.drawer`（V1/V2 两份）。**新增居中元素时别再用 translate 那套。**

**为什么 headless 验不出来**：Chromium 在桌面上默认就按 DPR 栅格化，不会出现这条
（写了个四行对照页量过一个字节都不差）。**这条只能在真机上认。**

**残留的合法 transform**：`.ct-lock`（锁遮罩）、`.ct-close`（圆形按钮）、
`.cap::before`（纯色胶囊体）、`.dtitle .dot`（小圆点）、热区伪元素的
`translate(-50%,-50%)` —— 它们**不含文字**，或形状本身对称到看不出来，别顺手改掉。

### ⚠ iOS 聚焦输入框会放大整个视口（字号 <16px）

全站输入框原先都是 `calc(var(--u)*N)` = 13~14px，而 **iOS Safari 在聚焦字号 <16px
的 input/select/textarea 时会把视口放大**，且失焦后不一定缩回来 —— 页面卡在半放大态。
`-webkit-text-size-adjust:100%` 压不住这条（那个只管横竖屏文字调整）。
viewport 里加 `user-scalable=no` 是反模式、iOS 也不完全尊重。

**唯一可靠解法：控件字号 ≥16px**（视觉上比 14px 的标签略大，可接受）：
`.form` 那组控件、`.logctl input`、`.fpinput`、`.plinedit input`、
`.form input[type=password]`（凭据框：内联样式没写字号 → 用浏览器默认 13.333px），
以及 `SettingsPage.tsx` 里内联的 `fontSize`。

**验证**：探针里遍历 `input,select,textarea` 读 `getComputedStyle(e).fontSize`，
断言全部 ≥16px。注意要**点进二级分组**（`?tab=set&grp=work`）才量得到，
一级菜单页上没有输入框。

---

## 五、重构/搬页面时最容易漏的：JS 副作用

**DOM 里看不出来的东西，搬页面时必须专门清点。** 已漏过并已补的：

- **历史栈**（`pushState` / `popstate`）→ 侧滑返回失效
- **`syncThemeColor()`** → 内页状态栏颜色不对
- **`html[data-page]` 属性** → 内页背景不切白
- **`place()`** → 功能栏与任务列表重叠
- **勾选框的 click 委托** → 点了没反应
- **拖拽守卫**（`e.target.closest('.mcb')` 时不启动拖拽）→ 点勾选框变成拖拽起手

**方法**：搬一个页面时，除了搬 DOM，必须把旧实现里的副作用逐条问"这个我搬了吗"。

旧版原生实现（`web/legacy/`，纯 HTML+CSS+JS）已随迁移完成删除，需要对照时从 git 历史取：

```bash
git show 7e142f1:web/legacy/app.js > /tmp/legacy-app.js   # 迁移落地那一次的完整旧版
grep -nE "addEventListener|setAttribute|\.style\.|dataset\." /tmp/legacy-app.js
```

---

## 六、临时探针的纪律

**不要用 `cp 文件 /tmp/x.bak` 再 cp 回来。** 备份随时可能是**已被污染的那一份**
（真实事故：探针被误提交进 `main.tsx`，页面每 2.6 秒自动弹一次背景面板）。

用 `git checkout -- <file>` 恢复；或者探针写进独立文件注入。

### headless 探针的四个必踩点（2026-10-09 又踩了其中三个）

1. **`--virtual-time-budget` 不推进 CSS 过渡**（合成器时钟不走）——不先注入
   `*{transition:none!important;animation:none!important}`，展开/折叠类改动会永远量到
   `height:0`、误判成"改了没生效"。早期探针版本有这一步，重写时丢过。
2. **点 `#idRing` 要按"当前节点"点并校验结果**：React 轮询会重渲染、替换节点，
   对着**已废弃的节点** `click()` 不冒泡到 root 的事件委托，静默无效。
   点完必须断言 `.idcard.open` 真的出现，没出现就重取节点重试。
   （症状是 `openEls=1` 但 `height=0`——"点上但过渡没跑"与"根本没点上"会混在一起。）
3. **探针页用静态 `?tab=` HTML 最稳**：若改成 JS 动态 `createElement('iframe')`，
   必须**先 append 探针再设 `f.src`** —— 探针在 `load` 事件里启动，
   先设 src 后注入会错过这次 load（表现为 `probeMissing`）。
4. 本机 Chrome 154 的 `--headless=new --dump-dom` **dump 完不退出**，要后台跑 + 轮询
   `data-probe` 出现后 kill（`tools/layout-check.sh` 直接串行调用会挂住到天荒地老）。
   另外 dashboard 开了访问验证时 `layout-check.sh` 会**跳过**（退出码 2）——
   等于布局改动没验证，探针页要先 `POST /api/auth` 拿 cookie 再挂 iframe。

---

## 七、交付前的最低检查

```bash
cd web
node node_modules/typescript/bin/tsc --noEmit     # 类型（用用户自装的 node）
node node_modules/vite/bin/vite.js build
# 布局类改动再加：探针量 homeH / homeExtra
```

**布局类改动没验证就不许提交。** 犯过：先提交再验证，用户看到整页空白。

---

## 八、沟通纪律（这条是给 AI 的）

- **别一次只修一层就交验。** 用户抱怨的是"改了七八轮还在犯同样的错"——
  该做的是把**整条链路**（如"iOS 手势在滚动容器里的完整行为"）一次走通再交。
- **探针说"没问题"但用户说"有"，立刻要截图**，不要继续在测不准的环境里换假设。
- **改用户的真实数据前先确认**（如点击勾选框会写回 `tasks.<k>_enabled`）——
  这类验证要么用幂等的同值，要么留给用户做。
---

## 九、长页面「背景被截断」＝内页盒子只有一屏高（2026-10-23，用户截图实报）

**症状**：冒险页滚到第二屏以后，「冒险足迹」标题那一行横着一条浅褐色带子，
再往下又是正常底色 —— 看着像背景图 / 渐变被从中间切断。

**真因（量出来，不是猜）**：`main section[data-page]:not([data-page="main"])`
原来写的是 `height:100%`，于是内页盒子**永远只有一屏高**：

| 页面 | section 盒子高 | 内容实际高 |
| --- | --- | --- |
| adv | **844px** | **22558px** |
| notify | 844px | 2945px |
| log | 844px | 1013px |

底色渐变、`border-radius`、`box-shadow` 全是画在**那个盒子**上的。
盒子只有 844 高，滚到 `scrollTop=700` 时盒子底边正好落在屏幕 `144px` 处
（实测截图里颜色突降点在设备像素 y=288 = 144×2，完全吻合）——
那条「截断线」就是盒子的底边 + 投影。

**修法**：内页基础规则改 `height:auto` + 保留 `min-height:100%`
（高度链不受影响：`min-height:100%` 仍然要求父级确定，`.page-slot` 是
`absolute+inset:0`，满足；内容超长时盒子跟着长）。

**⚠️ 两个连带必须一起改，漏一个就出新 bug**：

1. **整屏页要显式写回 `height:100%`**：`.ct-page`（职业树）与
   `.shotbody`（实时画面）的内部布局依赖父级有确定高度。已在
   `career-tree.css` / `live.css` 各自补上；漏了就是「职业树矩阵行高算错 /
   实时画面舞台撑不满」。
2. **百分比尺寸的背景渐变会跟着页面高度一起被拉爆**：冒险页原来的
   `radial-gradient(120% 42% ...)`，42% 相对 22558px 就是 9000px 高的巨型光晕，
   整个列表区都会泛黄。改成固定 `360px`（光晕只是给页头打光）。

**同一天顺手修的**：`@media(max-width:639px){main section[...]{box-shadow:none}}`
原来写在内页基础规则**之前**（1786 行 vs 1940 行），特异性相同、后写的胜出，
于是这条被整条压掉 —— 手机上内页一直带着那圈 `0 0 0 1px` 描边 + `0 8px 40px`
投影，长页面滚过第一屏时那道投影也横在内容中间（是那条带子的另一半原因）。
现已挪到文件末尾。**以后再加内页覆盖规则，先确认它写在基础规则之后。**

**验证手法**（可复用）：CDP 里写
`sec.getBoundingClientRect().height` vs `sec.scrollHeight`，两者必须相等；
再把 `.page-slot` 滚到多个位置各截一张，在**左边缘 4px 处**逐像素扫竖向突变 ——
正常情况下那里只该有页头一处变化。

## 十、桌面窗口的任务卡不能被 flex 拉成空壳（2026-10-23，用户截图实报）

**症状**：Tauri 窗口（真实 WebView 约 488×755）里任务卡下方一大片空白，
底栏状态卡像被挤到窗口外。

**真因**：`.v2-seat` 负责吃掉中间列剩余高度是对的，但桌面媒体查询又把
`.qpanel` 设成 `flex:1 1 auto`。于是任务卡被拉满 593px，而 `.tasklist`
仍按内容只有约 280px，剩下 270px 变成不可滚动的空玻璃；底栏其实仍在原位。

**规则**：桌面窗口多出来的是**宽度**，不是任务行。`.qpanel` 必须保持
`flex:0 1 auto`（内容高度），`.v2-seat` 继续承担剩余空间的占位，让房间背景
自然露出。不要把 `.tasklist` 也改成 `flex:1`，那只是把空白搬进卡片内部。

**验证**：`520×900` 与真实 `488×755` 下都要满足
`qpanel.height < seat.height - 40`、`deck.top - qpanel.bottom >= 16`、
`deck.bottom - drawer.bottom >= 4`，且 `.home` / `.page-slot` 溢出为 0。


## 十二、冒险页的滚动条是 `.page-slot`，不是横条（2026-10-23）

用户截图里右侧的灰色竖条来自 `.page-slot.page-enter` 的原生滚动条，不是
`.adv-ratio-bar`。冒险页内容长达 16000px，**不能把 `overflow` 改成 hidden**
来“去掉”它；正确做法是按 `html[data-page="adv"]` 只隐藏滚动条绘制：

```css
html[data-page="adv"] .page-slot.page-enter { scrollbar-width: none; }
html[data-page="adv"] .page-slot.page-enter::-webkit-scrollbar {
  display: none; width: 0; height: 0;
}
```

这次同时修掉了它“占很大位置”的根因：滚动条占掉 15px 后，卡片按
`clientWidth` 缩小；隐藏后 390 宽视口的 `clientWidth` 应恢复为 390，
页面仍是同一个 `.page-slot` 滚动容器。验证必须同时断言：
`overflowY === "auto"`、`scrollTop = 500` 可读回、`clientWidth === offsetWidth`。

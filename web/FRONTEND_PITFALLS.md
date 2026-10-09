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

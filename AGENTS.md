# AGENTS.md

给 AI 代理的项目说明：结构、约定、常用命令。

> ⚠️ **改 `web/`（React 前端）之前，必须先读 [`web/FRONTEND_PITFALLS.md`](web/FRONTEND_PITFALLS.md)**
> —— 那里记录了这个前端迁移中**反复**踩的坑：高度链断裂 ×3、headless 最小视口 500px 导致的
> 误判 ×2、iOS 手势被滚动容器吃掉 ×3、直系子选择器被"多包一层"整条打断、迁移时漏掉的
> JS 副作用清单（历史栈 / syncThemeColor / html[data-page] / place() / 勾选框 click 委托）等。
> 每条都写明"改了 X 就必须验 Y"。**别不看就动手。**

> ⚠️ **上游 PyQt6 GUI 已移除，当前已有 macOS / Windows 完整原生应用**：
> 上游 `main.py`、`src/stats_chart.py` 与运行时 pywin32/PyQt6 依赖仍不应恢复。
> 当前界面唯一真源是 `web/`（React）+ `dashboard.py` 后端；`desktop/macos/` 将 Tauri
> 界面、Python、后台与调度器一起打包，启动时自动准备本机服务，无需填写 URL。
> `desktop/windows/build_native.py` 复用同一 Tauri 界面，打包每用户 NSIS 安装器；
> Windows 打开独立窗口、自动启动本机后台并登录，关闭窗口后保留托盘爪子图标。
> `build_portable.py` 是内部 Python onedir 构建步骤（仪表盘和 scheduler 两个 exe）。
> `gui.theme`/`gui.mirror` 仍是死配置。

## 项目概述

QQ 宠物自动化托管脚本。技术栈：Python 3 + uiautomator2（画面、输入与控件定位）+
RapidOCR（文字/数字识别）。UI 定位分辨率无关：
优先 u2 控件选择器，游戏内 canvas 自绘按钮靠 OCR 文字（`src/locators.py` 注册表）。
平台：macOS / Windows 原生 App 与源码运行，目标设备：Android 手机（竖屏）。

游戏机制注意：**护理相关勋章如果要拿的话不能一键！！！**（一键护理不计入勋章进度，
要拿勋章必须把护理方式配成"ocr检测"手动喂食/洗澡，配置项 `care.method` /
`friend_care.method`）。

## 运行与测试命令

```bash
PY=.venv/bin/python              # 项目虚拟环境（macOS / Python 3.13），所有命令用它

$PY -m py_compile <files>        # 改完代码最基本的验证，必做
./run.sh                         # 控制台调度器（= $PY scenarios/runner.py，透传参数）
./tools/launchd/install.sh       # 源码手机仪表盘：交 launchd 托管（8787，KeepAlive 自愈）
#   --uninstall 卸载。源码长跑用这条；macOS 完整 App 会自动托管自己的本机后台。
#   `./dashboard.sh` 起的临时实例会被
#   "退出某个 App"连带杀掉（application coalition，见「关键约定」那条 2026-10-09 事故）
$PY scenarios/runner.py --test <target>   # 单模块测试：coins / recover / opener / school.X / work.X / adventure.X / care.X / friend_care.X / hire_friend.X
$PY -m pytest                    # 单元测试（tests/，依赖见 requirements-dev.txt）
# 模拟器模式：runner.py 加 --emulator [--emulator-device 127.0.0.1:7555]（Root 按需，见 src/opener.py 行）
```

- 无设备测试：用 `DeviceScenario.__new__(DeviceScenario)` 跳过 u2 设备连接，
  桩掉 `see()` / `click()` 后调用被测方法（见历史上对 `wait_end` 的测试方式）。
- 前端（`web/`）：`cd web && node_modules/.bin/tsc --noEmit`（类型检查）、
  `node src/lib/format.test.mjs`（另有 `stage/notify/optimistic.test.mjs`）、
  `node_modules/.bin/vite build`（构建到 `web/dist/`，仪表盘按请求读盘、无需重启）。
- **测试时不要污染真实进度文件**（`runs/*.json`）：`tests/conftest.py` 的 autouse
  fixture 已自动把各模块指向 `runs/` 的常量重定向到 tmp；新增模块若在模块级持有
  `runs/` 下的 Path 常量，**要把它加进 `conftest.py` 的 `_PATCH_MODULES`**。
- **状态新鲜度离线测试**（不连设备、不碰真实进度）：`$PY tools/test_state_freshness.py`
  （`--no-gui` 跳过界面相关用例）——覆盖队列状态 starting/stopped/pid 判定、
  仪表盘 `work_eta` 的"本轮启动"截断、场景 `probe_activity_state`、
  `wait_busy_end` 重构不变性、调度器启动实测四条分支。改这些逻辑必须跑。
  （该脚本里带 "GUI" 字样的历史用例已随桌面 GUI 移除，`--no-gui` 现在是默认路径。）

## 目录与模块职责

| 路径 | 职责 |
| --- | --- |
| `scenarios/runner.py` | 统一调度器，两种引擎（`runner.engine`）：`task_queue`（默认，`TaskQueueRunner`：执行顺序由 `tasks.order` 配置，> 分隔越靠前越优先，不在 order 里不调度；每任务独立 enabled / trigger（interval 间隔 / daily 每日时间点窗口）/ enabled_time_range / success_interval / failure_interval，见 `tasks` 段）/ `legacy`（`Runner.run` 老主循环，顺序写死：护理 → 冒险 → 踩踩 → PK → 好友雇佣 → 好友护理 → 学习/打工）。共通：场景异常分级重试（回主页面重进 → `recover()` 重启恢复）；都失败时主任务（学习/打工）发告警通知（`src/notify.py`）并退出，支线任务延后重试（legacy 用 `SIDE_TASK_RETRY_DELAY`，队列用各任务 `failure_interval`）；**task_queue 启动实测** `_startup_activity_probe()`：每次启动先出门实测一次“现在到底有没有进行中的活动”（重启会丢内存里的 pending，实测是唯一真相）——命中进行中→登记 pending 延时收尾、撞结算页→点 quit 并按 `count_cross` 计数、被雇佣→交召回流程、空闲→记日志后按最新配置调度；队列状态额外写 `pid`/`started` 供消费端判新鲜度 |
| `scenarios/school.py` `work.py` `adventure.py` `care.py` `visit.py` `pk.py` `friend_care.py` `hire_friend.py` `employed.py` | 各场景，均继承 `DeviceScenario`（`pk.py`/`friend_care.py` 继承 `visit.py` 复用好友导航；`hire_friend.py` 继承 `friend_care.py` 复用指定好友导航；`employed.py` 只做被雇佣检测，召回复用基类） |
| `src/scenario.py` | 场景基类：截图/u2+OCR 定位点击/回主页面/等待结束（阻塞 `wait_end` / 非阻塞延时收尾 `defer_busy_end`+`finish_pending`，OCR 剩余时间登记 `pending`）/被雇佣召回/四种进行中状态检测；**结算收益记录** `record_session_reward`/`parse_session_reward`（学习「教师评语」/打工「工资明细+打工总结」结算页 OCR → `runs/session_rewards.jsonl`，页面自带时间戳当去重 `sig`，挂在 `finish_pending`/`wait_end`/`_detect_settlement` 三处；打工页实测字段：岗位「武馆教学助理(10分钟)」/「金币+28」/「工分+3」/「本金22，雇佣加成6」；**另解析** `pet`/`owner`（结算页头部「姓名杨萌萌」/「主人 Serena」；打工页宠物名没有标签 → 用「日期行之后、主人行之前」兜底并排除标题词）、`note`（「小提示」段原文）、`ad_coins`（「看视频+10」，官方文案是「看视频获得N金币」，独立一笔、不计进 `coins`，历史 23 条合计漏记 +589；`pay_detail` 兜底：官方工资构成标题（本金/限时补贴/基础工资）由**服务端下发**、客户端不硬编码，具体词没命中时用「标签+数字」整行兜底并排除已知标签，实测 23/23 命中）、**雇佣对象**（打工总结正文：「我雇佣了张二狗（主人：柠檬）」= 正向、我雇了别人 → `hired_pet`/`hired_owner`；「我被张二狗（主人：柠檬）雇佣了」= 反向、别人雇了我、加成被对方拿走 → `hired_by`/`hired_by_owner`；**两种句式语义相反必须分开**，实测 102 条打工记录正向 83 / 反向 4、互斥无冲突，合计恰好等于"有雇佣加成"的 87 条，可拿这个判据自校验；**主人名会被 OCR 按视觉行劈成两半**（「…（主人：放肆的青春诠释了悲」＋「伤）。…」），单 token 匹配整条失配 → 兜底做**相邻 2~3 项窗口合并**再匹配，修后 102/102 命中）；**两个坑**：① 结算页时间戳是 `2026 / 09 /27 11:44`（斜杠两侧带空格），日期正则不容忍空格时 `sig` 会退化成内容 md5（已修）；② 页面上除「金币+28」还有裸的「+28」「看视频+10」，金币只能取带标签的那个数，不能乱认数字；岗位/工分/工资构成/雇佣对象**只在 `kind=work` 时解析**，学习页不碰这几个字段；`_classify_busy_screen()` = 出门后一屏的状态判定（结算页优先，再 school/work/adventure/employed），`wait_busy_end()` 与**启动实测** `probe_activity_state()` 共用它——后者非阻塞（不 wait_end、被雇佣不等到分成比例），供调度器启动时实测当前状态） |
| `src/recover.py` | 异常恢复链路：adb reboot → 等开机 → 启动 QQ → **先试官方 scheme 直开宠物主页**（`mqqapi://qpet/open`，JumpActivity 零点击零权限，`SCHEME_TRY_ROUNDS`=2 轮，平板身份 `ro.build.characteristics` 含 tablet 会被告门禁拦、跳过直开）→ 失败回退点 `Q宠-*` 入口（descriptionStartsWith 前缀匹配，后缀数字不固定；入口紧凑双击用 minitouch 两连击——`d.click` JSON-RPC 往返慢，0.3s 间隔会被识别成两次单击进单击页，点不进主页 back 退回重试）回宠物页，返回新 U2Device；模拟器模式"重启设备"改为重启模拟器整机（MuMu 不支持 adb reboot，会把 adb 服务卡死；**opener 失败但设备在线时先强停 QQ 重开一次**——冷启动 QQ 首启失败比整机重启便宜得多），优先级：配置的 `recover.emulator_restart_cmd` > 留空自动探测模拟器实例分步停/启（`src/emulator.py`，serial 匹配多个实例时依次用 `emulator.type/name/path`、端口实际监听进程命令行消歧）> 回退 adb reboot，随后 connect → 等开机（`_adb_back_online` **不默认 kill-server**——kill-server 会把所有设备（其它模拟器实例/USB 真机）踢下线且 host:port 不会自动恢复，只在 connect **连续 `CONNECT_TIMEOUT_KILL_AFTER`=3 次**超时（服务疑似卡死，单次/偶发超时只是开机慢）才 kill-server 兜底并恢复之前在线过的所有远程设备）；`launch_emulator_if_offline(adb, emulator_cfg)` 启动时目标设备不在线则自动探测并**启动**所属实例（只启动不重启，等开机完成；探测不到返回 False 由调用方按原逻辑报错）——模拟器模式下仪表盘启动链路与 `Runner.__init__`（U2Device 连接前）都会调用 |
| `src/emulator.py` | 多模拟器实例自动探测（参考 ALAS module/device/platform）：MuMu 12/6/X、雷电 3/4/9、夜神、蓝叠 4/5、逍遥；exe→类型靠 `path_to_type`（exe 名+上级目录名），安装目录来源 = 卸载项注册表（子键名精确匹配）+ MuiCache/UserAssist（ROT13）+ 雷电 InstallDir 注册表；serial 算法 = vbox/nemu/memu 的 hostport→5555 转发正则（MuMu12 兜底 16384+32*index、雷电 5555+2*index、蓝叠4 固定 5555、蓝叠5 读 bluestacks.conf）；`find_instance(serial, type/name/path 消歧)`、`get_serial_pair()`（127.0.0.1:5555+X ↔ emulator-5554+X）、`restart_instance()` 按类型分步停/启 = stop 半边 + `launch_instance()`（仅启动半边，设备未运行时拉起用——模拟器模式启动链路 `launch_emulator_if_offline`，见 recover 行）（有控制台 exe 走控制台：MuMuManager/ldconsole/bsconsole/memuc，蓝叠5/MuMu6/X 杀进程再用主 exe 拉起）；serial 命中多个实例且配置消歧不够时，按端口实际监听进程命令行反查（`_match_by_listener`：MuMu12 每个实例的 .nemu 都转发 7555，MuMuVMMHeadless.exe 的 --comment 即实例名，0.0.0.0 通配与 127.0.0.1 精确绑定并存时精确绑定优先） |
| `src/u2dev.py` | uiautomator2 封装：连接（含 atx-agent 首装）、截图、`d.touch` 持续按压；**控制方案**（`control.method`，设置页下拉）：`injectInputEvent`（默认，`d.touch.down/up`，**不用 `d.click`**——UiDevice.click 走 JSON-RPC 模拟器上偶发失效；down/up 间保持 `CLICK_PRESS_SECONDS`=0.05s）/ `minitouch`（openstf minitouch，`MiniTouchSession`：二进制 `resources/minitouch/minitouch-<abi>` 推送设备 → `adb forward localabstract:minitouch` → socket 直发；forward 学习 ALAS 先 `forward --list` 复用、没有才在 20000-21000 随机高端口新建（低端口 Windows bind 10013）；坐标按握手 `^ max_contacts max_x max_y` 等比换算，不硬编码屏幕分辨率；minitouch 单连接限制，连接超时=被 atx-agent /minitouch 等占用），`click/touch_down/move/up` 按方案分派，minitouch 会话懒加载；**自动回退**：minitouch 因非 root/SELinux 权限拒绝打不开 `/dev/input/event*`（`_start_server` 读启动日志分类，抛 `MinitouchUnavailableError`）时自动回退 `injectInputEvent` 并把 `control.method` 写回 config.yaml（下次启动/热加载也走默认方案）；minitouch 自身错误（不可用/被占用/会话断开）不算 u2 连接故障，`_is_conn_error` 不触发 u2 重连自愈；**设备掉线重连**：`_reconnect` 先 `_wait_device_online`——远程模拟器（host:port）adb 抖动时先 `adb connect` + 轮询等 `DEVICE_ONLINE_WAIT`=45s 回线再重连 u2，避免"device not found"被误判成需要整机重启（USB 真机掉线不等待直接抛） |
| `src/locators.py` | UI 定位注册表 `LOCATORS`：名字 → u2 选择器 / OCR 候选文案；`see()` / `see_all()` |
| `src/adb/device.py` | adb 封装：设备在线管理（start-server）、`getprop()`（recover 判平板身份用）、`reboot_and_wait()` / `launch_app()`（异常恢复用） |
| `src/ocr.py` / `src/coins.py` | RapidOCR 封装；主页金币 = 顶部状态栏最右侧数值（全屏 OCR） |
| `src/progress_store.py` | 进度文件统一管理（`runs/*_progress.json`）：跨天规整（旧日期次数归档进 history、清掉旧日期的 `study_secs/work_secs`；`school/duration` 作为会话元数据跨天保留供跨零点结算累计）、原子写入（先写 `.tmp` 再 `os.replace`，进程被杀不留损坏文件）、损坏兜底（解析失败备份成 `*.corrupted.bak` 后按空档继续）；全部进度读写走这里，`src/progress.py` 只做兼容层 |
| `src/progress.py` | 进度持久化对外兼容入口（常量 + 日志 + 各场景函数签名，内部转发 `src/progress_store.py`）：`log()`（控制台 + 按日写 `runs/logs/`）、`load_progress/save_progress/increment_progress`（含 history 跨天归档）、`count_cross` 交叉计数、学习/工作时长累计与迁移；进度文件固定 `runs/*.json` 单文件（曾按账号重定向到 `runs/accounts/<账号>/`，账号名靠状态面板 OCR 识别不稳定、数据被拆散，已取消多账号区分） |
| `src/opener.py` | 模拟器模式集成（**零注入方案**：MuMu 机型伪装 / 设备门禁本地翻转 + 官方 scheme 跳转，旧版常驻 frida 注入被 QQ 风控"使用外挂插件"）：**Root 按需**——`_has_root()` 软检查（`su -c id`），有 Root 才做 `ensure_device_spoof()` 伪装重挂与 MMKV 补丁自检，无 Root 直接 scheme 直开（伪装/补丁都已持久化的设备日常零权限运行；scheme 失败且无 Root 时报错提示开一次 Root 打补丁，之后可永久关闭）；伪装改 MuMu app 级机型映射（`emulator.device_spoof` 开关控制，**默认关闭**——门禁补丁已翻转的设备不需要，设置页"MuMu 机型伪装"开关，opener 每次运行重新 load_config 天然热生效）（`/system/etc/mumu-configs/app-device-prop-*.config` 里 QQ 行从 `yyb.config` 原始模拟器 dump 换成真实手机 profile，root `mount --bind` 覆盖，重启模拟器失效、每次运行幂等重挂，非 MuMu 静默跳过），QQ 以真机身份运行、门禁原生通过、搜索卡片"宠物"入口可用；无伪装时模拟器被 QQ 判成 TABLET（`ro.build.characteristics` 含 tablet），门禁 `PetQQMC.e()` 读 UnitedConfig 107805 的 `enable_tablet`（默认 0）拦截官方跳转，此时 `ensure_gate_open()` 用 root 改本地 MMKV 缓存（`united_config_mmkv_<uin>` 追加 `enable_tablet:1` 记录 + 重算 CRC，`_patch_gate_mmkv` 有格式自检，不符就放弃不冒损坏风险）翻转门禁兜底；然后 `am start -a VIEW -d "mqqapi://qpet/open?..."`（JumpActivity，普通 shell 可发）由 QQ 官方路径打开宠物主页并初始化 SDK；三级调度：scheme 直开 → MMKV 补丁后 scheme → frida 一次性 SDK init + `am_start_pet_page` fragment 直开兜底（伪装名 `perf_daemon`、随机端口、几秒注入窗口，格式变化时启用）；`EMULATOR_MODE`/`GATE_OPEN` 标记由 `run_scheduler`/`--test`/`open_pet_page` 置位，visit 据此选游戏内导航或 am start 好友入口分支；好友入口 uin 由 `ensure_friend_entry()` 一次性注入捕获缓存 `runs/friend_entry.json`（仅兜底模式需要）；`_start_qq` 冷启动重试（`START_QQ_ATTEMPTS`=3）；QQ 服务端重发 107805 会覆盖补丁，每次启动自检重补 |
| `src/status_cache.py` | 宠物状态缓存（`runs/status_cache.json`，单 default 条目——曾按账号名称组织兼容多账号，账号 OCR 误识别导致状态条多行，已取消）：体力/清洁/心情（care 状态面板 OCR 后）、金币（主页 OCR 后）、香皂/饼干（喂食/洗澡结束时 OCR 控件附近小图；库存角标无文字，取离 `feed_10`/`shower_10` 控件最近的数字）；一键护理后清空体力/清洁/心情/饼干/香皂；消费方是仪表盘（dashboard 直接读这份 JSON，总览页状态环每秒刷新） |
| `src/queue_status.py` | 任务队列状态缓存（`runs/queue_status.json`）：TaskQueueRunner 每轮调度后写当前任务/下一任务（含等待点时间，HH:MM:SS + next_ts 时间戳，前端显示"xx秒后"倒计时）/待执行数量（在等退避/每日窗口/pending 收尾时间，主任务组 pending 也算一项）/等待中数量（现在就可执行、等调度器轮到），执行中任务在 `_execute` 里先写一次；仪表盘每秒读一次（调度器未运行时不读，显示"调度器未运行"）；legacy 引擎不写。**状态新鲜度**（用户实报"宠物被送去上课、我手动召回，停掉调度器再启动，显示还是在上课"）：调度器一起来就 `mark_starting()`（清空上一轮活动 + 写自己的 PID）、停止时 `mark_stopped()`（调度器自己退出与仪表盘 `scheduler_stop` 两条路径都写）；消费端用 `status_is_current()` / `startup_placeholder_active()` 判断"这份状态是不是当前这个进程写的"，对不上就显示"启动检查中/按配置推算"，而不是把上一轮遗留的 `pending: 上课` 当现状（宽限期 `STARTUP_PLACEHOLDER_GRACE_SECONDS`=600s，超过说明该引擎根本不写队列状态） |
| `src/config.py` | dataclass 配置 + 路径规划：`APP_ROOT`（可写数据根，`QQPET_DATA_DIR` 优先；macOS 冻结版默认 `~/Library/Application Support/QQPetCopilot`，Windows 原生 App 默认 `%LOCALAPPDATA%\QQPetCopilot`；开发用便携模式保留 exe 同级数据）/ `RESOURCE_ROOT`（独立只读资源根，冻结版为 `sys._MEIPASS`）。冻结版 `resource_path()` 优先 `APP_ROOT/runs/<相对路径>` 覆盖，再读包内资源 |
| `src/settings.py` | ruamel 往返读写 config.yaml（保留注释）+ 字段校验（`DEFAULTS`/`validate_field`）；消费方是 `dashboard.py`（设置页读写）与 `scenarios/runner.py` |
| `src/notify.py` | 告警与事件通知：桌面通知（macOS `osascript` / Windows winotify）+ 飞书群机器人 + Telegram Bot + OnePush（Bark/PushPlus/Server酱/SMTP/自定义 webhook 等），发送失败只记日志、发不出去也不吃调度。**正文可带富消息 HTML**：含 `<table>` 时 Telegram 走 **`sendRichMessage` 富消息**（原生表格；`_RICH_ONLY_RE` 判定），其余渠道由 `_escape_plain()` 剥成纯文本（表格 → 一行一条、制表符分列、表体不留空行；段落空行保留）。**四条必须守的点**：① `<table>` **绝不能**用 `sendMessage` + `parse_mode=HTML` 发 —— 旧解析器不支持 table，会 400 拒收整条（富消息上限 32k，普通消息 4096）；② 富消息失败要**回落普通消息**，表格只是排版、通知不能丢；③ 普通正文超 4096 字符是**整条拒收**而非截断，发送前用 `_fit_telegram()` 自己砍（顺手补齐被砍断的 `<pre>`）；④ `notifications.jsonl` 历史存**剥标签的纯文本**（仪表盘按 pre-wrap 渲染，存原文会看到一堆标签） |
| `src/tg_bot.py` | **Telegram 控制机器人（项目里唯一的反向通道）**：`src/notify.py` 只做单向推送（程序→你），这里做双向——仪表盘进程里起一个后台线程长轮询 `getUpdates`，把消息解析成**改 config.yaml / 查状态 / 启停调度器**。两条轨道同一个执行器：① 确定性指令（`/help` `/set 字段 值` `/get [字段]` `/status` `/log [N]` `/run` `/halt` `/pause` `/resume`）；② 自然语言（`tgbot.llm_enabled` 打开且配好 OpenAI 兼容接口时走 tool calling，**工具集合与指令完全同一套**，越不出确定性指令的边界）。**三条刻意划死的边界**：① **只认白名单 chat**（`tgbot.allowed_chats`，留空回落 `notify.telegram_chat_id`），非白名单**静默丢弃、不回话**——token 泄露时这是唯一闸门；② **凭据类不可经 TG 修改**（`blocked_reason()`：`tgbot_*` 整段 + `notify.*token/webhook/secret` + `*_set` 状态位），聊记录永久留存且改控制通道自身凭据等于把一次泄露升级成永久接管；③ 不碰设备/场景，只改配置、看状态、开关调度器进程。与 dashboard 靠 `Bridge` 依赖注入解耦（dashboard 是脚本不是包，反向 import 会循环），因此可离线测试。**`/start` 绝不能绑成「启动调度器」**——它是 Telegram 的"开始"按钮，点开机器人就会发，绑了就是误触；启停走 `/run` `/halt`。轮询游标存 `runs/tgbot_state.json`（重启不重放旧消息）；`enabled` 每轮重读，设置页改完立即生效不用重启仪表盘；长轮询 25s 超时是正常现象不当错误记。**TG 改配置必须留审计**：HTTP 那条路的审计写在 `/api/settings` 路由里，TG 经 Bridge 直接调 `apply_settings` 不经过它，所以 dashboard 的 `_tg_apply()` 自己补一笔 audit + 写 `reload.signal` |
| `src/reward_table.py` | 通知里的**原生表格**：`rich_table()` 生成 Telegram 富消息 HTML（`<table bordered striped>` + `<th>` 表头 + 数字列 `align="right"` 右对齐；右对齐的数字才好逐位比大小，这是表格相对纯文本的核心价值）；`escape()` 把单元格里的 `&`/`<`/`>` 转义 —— **不转义会 400 拒收整条消息**。纯函数、无副作用。（曾试过等宽 `<pre>` 与画图 sendPhoto 两条路：前者被渲染成灰底代码块、后者不是 TG 自己的表格样式，用户均否掉） |
| `src/daily_summary.py` | 日内收益汇总通知的两条文案（数据全来自**已落盘记录**，不 OCR、不碰设备）：**打工学习日报**（学习+打工当天都跑完时推）+ **冒险日报**（23:59）。**两条口径故意不同**：打工一天只有几次 → 日报用**逐次明细表**，篇章结构由用户钉死为五段——`🐾 <b>日期 打工学习日报</b>` → `今日学习 学分 +N 力量 +N / 魅力 +N / 智力 +N` → 学习表（时间/课程/学分/按需属性列）→ `今日打工 工分 +N 金币 +N` → 打工表（时间/岗位/**雇佣**/金币/工分），**其余文案一律不要**（无时长、无合计行、无平均值）。属性汇总顺序由 `ATTR_SUMMARY_ORDER`（力量/魅力/智力）决定，与表格列顺序（`ATTR_KEYS` = 力量/智力/魅力，解析层字段顺序）**无关**，别混。「雇佣」列见 `_hire_cell`：正向直接显示名字、反向（被别人雇）显示「被XX雇」以标明方向、无雇佣关系显示 `—`；**「看视频补贴」不显示**（用户要求，数据照常落盘、仪表盘收益记录页仍能看到）；**冒险一天几百次**（实测 491 次）→ 日报用**指标表**，**不列逐次**（36% 命中率下列 30 行大半是 `+0`，纯刷屏）。日报指标口径由用户钉死为七项：**总次数 / 金币 / 心情 / 清洁 / 体力 / 命中率 / 平均每次**（命中率只给百分比，**不带「N 次有收益 / M 次空手」括号明细**——用户要求去掉；心情·清洁·体力各占独立一行，值来自 `_adventure_attrs()` 从 settle token 解析的属性收益，实测 436/491 条命中；**一条都没解析出来时显示 `—` 而不是 `+0`**，且这三行始终占位不隐藏）。**两条都不带通知标题**（用户要求）：`report_work_summary` / `report_adventure` 都返回空标题，正文首行已自报家门；各渠道对空标题做了容错——消息正文用 `_join_title()` 不留空行、富消息不产生空 `<b></b>`、桌面通知/OnePush 这类**标题是系统级独立字段**的渠道回落 `_fallback_title()` 到通用名。未解析出的数值显示 `—` 而不是 `0`；明细表超 30 行只列最近 30 条（完整明细看仪表盘）。归属哪天按结算页自带日期 `sig`（跨零点算前一天），与 `dashboard.rewards_data` 同口径 |
| `tools/dump_hierarchy.py` | 抓当前屏幕控件树 XML 存到 `xml/page.xml`（校准 locators 的 xpath/content-desc 用；`xml/` 已 git 排除） |
| `tools/fetch_scrcpy.py` | 从官方 GitHub Release 下载解压 scrcpy（win64）到 `resources/scrcpy-win64/`（不入库，主要提供随包 `adb.exe` 兜底）；`--version` 指定版本、`--force` 强制覆盖。直播已移除，不再由代码自动调用 |
| `tools/fetch_frida_server.py` | 下载 frida-server 离线包到 `resources/frida-server/`（不入库）；`--version`/`--arch`（可多个）/`--force`，GitHub 直连失败自动试镜像；源码运行 `src/opener.py` 缺失时自动调用 |
| `tools/fetch_minitouch.py` | 下载 minitouch 预编译二进制到 `resources/minitouch/minitouch-<abi>`（不入库，jsDelivr/unpkg/GitHub 多源）；`--arch`（可多个）/`--force`；源码运行 `src/u2dev.py` 控制方案选 minitouch 且缺失时自动调用 |
| `resources/` | 第三方二进制/离线包（不入库）：`scrcpy-win64/`（tools/fetch_scrcpy.py 拉取，Windows 只收 ADB 与两份 DLL）、`frida-server/`（离线 xz，macOS / Windows 完整 App 都随包提供 arm64 与 x86_64）、`minitouch/`（minitouch 二进制，tools/fetch_minitouch.py 拉取，Windows 包含四种 ABI） |
| `tools/test_locator.py` | 测试 locator 的 xpath 在当前页面的命中稳定性（连设备连续多轮 dump，统计 live/snapshot 两种调用方式的命中率与 bounds 漂移，定位深层 xpath 时有时无/位置漂移问题） |
| `tools/capture_visit_jump.py` | 抓取 QQ 宠物"访问好友"跳转参数（doJumpAction URL + doAction attrs），真机/模拟器对比、QQ 更新后排查用；`-s` 设备、`-c` 自动点 好友->访问；frida-server 按 opener 的隐身方式自动部署（伪装名 + 随机端口，用完即杀） |
| `dashboard.py` | 仪表盘后台（标准库 HTTP 服务，默认端口 8787；源码临时调试用 `./dashboard.sh`，长跑交 launchd，macOS 完整 App 自动准备随包本机后台）。**后端只提供接口与静态资源，界面是 React 前端 `web/`**（构建产物 `web/dist/`，按请求读盘、**改前端无需重启仪表盘**；改 `web/` 前必读 [`web/FRONTEND_PITFALLS.md`](web/FRONTEND_PITFALLS.md)）。访问需鉴权：访问码存 `runs/`，`/api/auth` 校验后发 Cookie（`src/dashboard_auth.py`），改 dashboard 路由要留意这条守卫。路由：`/`（`web/dist/index.html`）、`/assets/*`（Vite 产物，带 hash 的长缓存）、`/manifest.json`、`/sw.js`、`/qp-icons/*`（图标与背景，路径归属校验；`Cache-Control: max-age=3600`）、`/files/*`（异常截图等）、`GET /api/data`（总览聚合）`/api/logs` `/api/rewards` `/api/adventure` `/api/friends` `/api/plan` `/api/adb` `/api/notify/history` `/api/screenshot`、`POST /api/auth` `/api/access-key` `/api/settings` `/api/runner/start|stop|pause|resume` `/api/adb/connect` `/api/plan/sync` `/api/preset/alt` `/api/notify/test`。验证：`python3 dashboard.py --port 8899` 起副本（不影响 8787 在跑的实例）。**实时画面**：前端每 15 秒轮询 `GET /api/screenshot`（切后台暂停、回前台立即补一张）；`capture_phone()` 走 adb `exec-out screencap -p` → PIL 缩到 390 宽 → JPEG，服务端 5 秒节流缓存，截图失败时回退 120 秒内的缓存并标注「(缓存)」。网页上的**直播与接管已整体移除**（`src/screen_stream.py`、`/stream/*`、`/api/stream/*` 均已删除，不要再加回来）；但**Telegram `/pause` / `/resume`** 仍复用同一套让路机制：写/删 `runs/pause.signal` → 调度器在安全点（每轮调度开头 + `sleep_interruptible` 睡眠切片里的 `wait_if_paused()`）原地等待，**不退出进程、pending 与进度全保留**，`PAUSE_MAX_SECONDS`=300 秒无更新自动恢复。**房间背景**：总览页圆钮开选择面板——**点按开面板、长按切下一张**；背景库见 `web/src/lib/scene.ts` 的 `SCENE_INFO`（「随机」+ 官方 4 套房间场景 + 20 款家居 `home-*` + 8 个职业主题 `career-*`；**`room-store` 已按用户要求移除**，图片仍在磁盘随时可加回，`readManualScene` 会校验旧 localStorage 值并回落「随机」，旧的 `auto`（跟随任务）也归一化成随机）；那 20 款的真原图来自 app 下载的**素材包**（root 可读），归档 `qqpet_assets/_raw/pet_home_bg_packs/`（静态 17 款）与 `qqpet_assets/_raw/pet_home_bg_dynamic/`（动态「花田诗意」「奇遇嘉年华」「魔女杂货铺」），重建 `extract_home_bgs.py --from-packs` / `--from-dynamic-packs`；**动态背景（官方叫「灵动背景」，QQ 9.3.75 起才有）**：`SCENE_INFO` 条目多了两个可选字段 `video` / `videoDark`（`huatian-day-loop` / `huatian-night-loop`；「奇遇嘉年华」「魔女杂货铺」是 `carnival|witchshop-day/night-loop`——**夜版子包只有 QQ 处于夜间模式时点卡片才下发**，素材抓取时别漏），`applyScene` 按当前场景在 `body` 下插/删一个 `<video class="qp-room-video">` 当**视频层**（fixed + `z-index:-1` + `object-fit:cover;object-position:center top`，与静态图 `background-size/position` 完全一致；`body{isolation:isolate}` 让负 z-index 锁在 body 层级里，否则会沉到 html 背景后面、什么都看不见）。**静态图与视频必须同构图**（视频由 `_raw` 里 508×1368 的原始帧按页面比例裁到 508×1088 再编码，静态图就是第 1 帧超分到 1008×2161）——比例不一致时，视频加载完成那一刻画面会跳。系统开了「减弱动态效果」或背景没有 `video` 时**完全不创建** `<video>`（切走要 `pause()` + 移除，别让解码器常驻）。`/qp-icons/*` 的 `Range` 请求走 `_send_media`（**iOS Safari 对 `<video>` 强制先发 `Range: bytes=0-1`，只回 200 全量会直接不播**；越界按 RFC 7233 回 416）；**深色模式必须换图**（`SCENE_INFO.dark` + `sbDark`），别拿亮版调暗（「花田诗意」的夜版是月光场景、「奇遇嘉年华」夜版有烟花、「魔女杂货铺」夜版是月夜，都不是白天调暗）；**换背景图必须把 `web/src/lib/scene.ts` 的 `BG_VER` +1**（`/qp-icons/*` 走 `max-age=3600`，同名覆盖时浏览器一小时内仍显示旧图）；手动选择存 `localStorage('qpet_scene')` 并**暂停「随机」轮换**；「随机」模式每 `RANDOM_ROTATE_MINUTES`（10）分钟从**全部背景**（含三套灵动背景）里抽一张、排除当前那张，选择面板与提示文案读同一个常量；背景与状态栏采样色由 JS 内联写到 `html`（色表只在 `SCENE_INFO` 一份）。**调度器单实例守卫**：`src/instance.py`（`runs/runner.pid` + 内核判活，**绝不依赖 ps**；runner 自守卫、多开时后启动者退出 2）。**状态新鲜度**：`work_eta()` 只认最近一次调度器启动（日志里的 `调度引擎: task_queue|legacy` 行，常量 `RUN_START_LOG_RE`，改 runner 那行日志时须同步）之后的"进行中"登记——重启后上一轮的登记作废，否则界面会继续显示"上课中/剩余 xx 分钟"（用户实报）；队列状态 `starting`/`stopped`/PID 与当前调度器对不上时状态条显示"启动中"、任务列表退回按配置渲染，超过 10 分钟宽限期不再显示"启动中"（legacy 引擎不写队列状态）；`scheduler_stop()` 顺手 `mark_stopped()` |
| `src/client_protocol.py` / `src/dashboard_auth.py` | **客户端协议 v1**：`GET /api/client/meta` 公开协议/能力；同源浏览器用 `/api/auth` Cookie，手机通过 `POST /api/client/pairing`（需已登录）取得 5 分钟、一次性配对票据，再 `POST /api/client/pair` 换取长期设备 Bearer Token。磁盘只存票据/token 的 SHA-256 哈希（`runs/client_protocol.json`，0600）。设备路由 `GET /api/client/session|devices`、`POST /api/client/heartbeat`、`DELETE /api/client/devices/<id>`；跨域只允许设备 Bearer 并返回 CORS，Cookie 与旧访问码 Bearer 始终限同源。根页面 `web/dist/index.html` 公开（否则远程客户端看不到配对页），所有 `/api/*`、`/files/*` 仍鉴权。二维码由服务端标准 `qrcode` 库生成 PNG，不把二维码编码器塞进前端。 |
| `src/runtime_process.py` | 跨平台本地托管进程层：源码选 `.venv/bin/python` / `.venv/Scripts/python.exe`；macOS 冻结版调用同一 `QQPetRuntime scheduler`（职业识别用 `career-sync`），Windows 冻结版仍用同级 `QQPetCopilotScheduler.exe`；`spawn_detached_scheduler` POSIX 用 `start_new_session`、Windows 用 `CREATE_NEW_PROCESS_GROUP\|CREATE_NO_WINDOW`；停止 POSIX `SIGINT→SIGTERM→SIGKILL`、Windows `CTRL_BREAK_EVENT→taskkill /T→/F`；便携/安装数据根解析。`dashboard.py` 的启停已接这套，不再硬编码 POSIX 路径。 |
| `desktop/macos/` | 完整本机 App：Tauri UI + PyInstaller onedir `QQPetRuntime`，随包 Python/后台/调度器/前端/ADB/OCR/控制资源；打开自动启动本机后台并登录，无 URL 输入。App 自动 bootstrap 用户级 launchd 作业 `ai.qqpet.dashboard.app`，采用已有 `ai.qqpet.dashboard` 时不抢生命周期；后台按构建指纹复制到控制端 Application Support 的 `backends/`，`data-root.txt` 记录沿用的数据根，移动 App 不影响后台路径。关闭窗口留菜单栏、退出 App 不主动停任务。`scripts/build-macos.sh` 先构建 runtime 再打完整包；构建脚本本身不启停服务。 |
| `desktop/windows/` | Windows 原生 App：`build_native.py` 先用 `build_portable.py --no-zip` 构建并验收两个 Python exe，再按内容指纹暂存到共享 Tauri 资源目录，生成 ICO 并用 Windows override 打每用户 NSIS 安装器。用户入口 `QQPet-Steward.exe` 打开独立窗口，自动启动本机后台并登录；关闭留托盘爪子、再次打开恢复窗口，退出 App 不主动停任务。数据默认 `%LOCALAPPDATA%\QQPetCopilot`，内部后台按指纹复制到用户运行缓存。CI 先离线/HTTP 验收 Python，再静默安装正式 NSIS、检查开始菜单入口，验收安装后的后台、真实 Tauri 窗口与自动登录，并可保存截图；真实设备/防火墙仍需实机验证。 |

## 关键约定（改动时必须遵守）

- **Windows 后台命令不得弹控制台**：界面和后台调用 ADB、职业识别子进程、`taskkill`
  等控制台程序时，必须传 `creationflags=CREATE_NO_WINDOW`（POSIX 为 0）。
  `capture_output=True` 只重定向输出，不能阻止黑框；模拟器本身的 GUI 启动仍保留正常窗口。
  回归见 `tests/test_dashboard_process_windows.py` 与 `tests/test_windows_adb_console.py`。
- **定位方式**：新 UI 元素登记到 `src/locators.py` 的 `LOCATORS`（沿用 `xxx_in` 进行中标志、
  `xxx_end` 结束标志、`quit`、`back`、`main_sign` 命名）。优先 xpath / u2 选择器
  （`main_sign` 是 xpath `//*[@content-desc="金币胶囊"]` 检测主页面——只有自己主页面
  有该元素，好友宠物页没有；care 解析面板名称时另有"加好友"守卫，防止把好友昵称
  当自己宠物的名称写进状态缓存）；
  进行中状态文字（`xxx_in`）用整屏 OCR 关键词（状态区域 xpath  bounds 随页面
  结构漂移，裁剪 OCR 不可靠；同一张 screen 连续多次 `see()` 共享一次整屏 OCR，
  见 `_ocr_texts_cached`）；整屏 OCR 统一走 `src/ocr.py` 的 `ocr_fullscreen()`
  （先保持长宽比缩到接近 720×1280 再识别，坐标还原回原图）；游戏内按钮用整屏 OCR 文案（候选列表按优先序）。
  位置固定、只需点击的元素可加 `'cache': True`（如 `back`）：第一次命中后坐标
  记入 `_locate_cache`，之后 `see()` 直接返回缓存点不再识别。
  位置固定的裁剪区域（如宠物状态面板 `status_region`）登记 xpath + `'cache': True`
  后用 `see_bounds()` 取范围：第一次命中后 bounds 记入 `_bounds_cache`，之后直接复用。
  （`care.read_status` 读状态面板已不走 `status_region` 定位——深层 xpath 在好友
  宠物页会误命中底部好友列表栏，直接 OCR 屏幕上半部分；`status_region` 仅
  `tools/test_locator_all.py` 校准用。）
  **出门地图建筑入口**（school/town/adventure）：2026-09 起直接用各建筑专属
  content-desc（`study`/`work`/`adventure`）锚定——旧的 `map_blank/FrameLayout[n]`
  序号路径随建筑增删漂移（曾指向错误建筑，点"职业小镇"开出学校面板），仅留作兜底。
  **学习/打工轮播选择框**（`select_box_container`）：锚定嵌套双层 RecyclerView 的
  内层卡片行容器 `//RV//RV/FrameLayout[1]`（对中间 FrameLayout 层级增减免疫），
  容器 bounds 按 2:2:1 分割出三个可见卡槽中心。
- **洗澡搓洗**：搓洗点位按分辨率百分比（`care.py` 的 `SCRUB_TOP_PCT` / `SCRUB_BOTTOM_PCT`），
  起点取 `shower_10` 控件中心；搓洗中清洁连续 `SCRUB_STALL_REPRESS` 回合不提升判定
  按压失效（模拟器 minitouch 会话静默中断，touch_move 全丢），自动抬手重按肥皂自愈；
  喂食/洗澡达到尝试上限仍不达阈值时**跳过本次护理**（不抛异常——游戏有概率显示 bug，
  实际已达标但界面/OCR 没刷新，抛异常会误触发重启恢复/告警退出）。
- **一轮语义**：场景的 `run(max_times, max_rounds)` 中一轮 = 一节课 / 一次打工 / 一次冒险，
  结束后回主页面；执行器以 `max_rounds=1` 调用，每轮后重新判断金币/学习工作时长。
  学习场景的毕业处理（"去找同学玩"面板）不算一轮：关闭后立即重新进学校选下一阶段
  课程（不等 success_interval），连续毕业由 `goto_school` 的 `_graduated_once` 抛异常防循环。
- **学园选课（课时时长）**：每阶段课程轮播固定 7 张 = 卡1-3 短课 + 卡4-6 长课 +
  卡7 萌芽夏令营（组内顺序 力量/智力/魅力，各学园顺序略有不同，见 `resolve_course_box`）。
  配置项 `school.duration` 的取值是**档位名「短课/长课」，不是分钟数**——各学园的实际
  分钟数不同（实测：初级 10/20、中级 20、高级 30、**进修学院 45/135**），写死分钟数必然
  过时。选完课从详情面板读「用时:XX分钟」写进 `school_progress.json`，结算按它累计。
  **旧值兼容**：`course_kind()` 把 `10分钟`→短课、其余任何分钟数→长课。
  ⚠ 踩过的坑：设置页下拉一度还写着 `10分钟/30分钟`（旧语义残留），用户选「30分钟」
  实际落到**长课**（进修学院 = 135 分钟），"设了 30 分钟却一直上 135 分钟的课"就是这么来的。
  改档位语义时必须四处同步：`src/settings.py` 的 `validate_field` / `web/src/lib/settings-schema.ts`
  的下拉 / `src/tg_bot.py` 的 `OPTIONS` / `dashboard.py` 的 `editable_snapshot`（回填前归一化）。
  长课选课 = 小步慢拖前滑 3 张（**快拖惯性大、点选会点空——踩过坑**）+ 点后按详情
  "奖励<属性>+N"核对（OCR 会把"+5点"读成"+50"、正则取第一位数字），不通过归位重试一次；
  未知阶段名字表（`COURSE30_NAMES`）待毕业实测补。
- **出门处理**：`goto_*` 出门后必须调 `wait_busy_end()` 检测四种进行中状态
  （school/work/adventure/employed）；等完的活动计入对应进度后**本轮直接结束**，
  由执行器重新判断限制条件，不得继续原定任务。出门后也可能直接出现**结算页**
  （上次活动已结束未收尾，如调度器重启丢失 pending 后）：`_detect_settlement()`
  按 OCR 文案区分——"教师评语"=学习、"打工总结"=打工（文案含配置的雇佣好友名称
  时同时计一次雇佣好友）、都没有但有"分享"按钮=冒险，点 quit 收尾（学习/打工
  顺带尝试鼓励，结算页实测没有鼓励按钮，快速 3 轮不中即放弃）后返回对应类型，
  计数语同等完活动。
- **计数时机**：检测到 `xxx_end` 点完 `quit` 就计数（`save_progress`），再回主页面；
  被雇佣在召回点 quit 时由基类 `count_cross('employed')` 计数，场景 `run()` 不得重复计。
- **主页面点 back 会退出游戏**：`ensure_main_page` 必须保留宽限——连续
  `schedule.main_page_checks` 次（默认 1，即识别不到立即点 back）识别不到
  `main_sign`（金币胶囊）才允许点 back，总尝试上限随检测次数放大
  （`MAIN_PAGE_ATTEMPTS * checks`）。
  **启动例外**（非模拟器模式）：`Runner.run()` 开头先做启动检查
  （`_ensure_pet_page_or_relaunch`）——识别不到 `main_sign` 说明多半根本不在游戏里，
  此时**不走 ensure_main_page 的 back 宽限**（乱按 back 无意义甚至误退别的 App），
  直接走"重启游戏"分支（`reenter_pet(adb, '重启游戏')`：强停 QQ → 启动 QQ → 点
  `Q宠-*` 入口，不受 `recover.method` 配置影响、不重启设备），成功后刷新各场景 `dev`；
  失败发告警退出调度器。
  **返回方式**（`schedule.back_method`，设置页下拉）：所有“回退”统一走基类
  `go_back()`——`系统返回`（默认）用 `dev.d.press('back')`；`返回图标` 定位
  `back` 按钮点击，找不到返回 False 由调用方决定（重试/放弃），避免误按系统返回退过头。
- **冒险类型选择**（2026-09 更新）：冒险准备页改版——开始按钮改名"出发"
  （`adventure_start` 保留"开始"兜底），新增类型卡"附近走走（约45秒）/诗和远方（约2小时）"。
  类型卡 canvas 自绘、控件树不可见、选中态无属性可读：`adventure.py` 的
  `_ensure_adventure_type()`（`do_adventure` 点出发前调用）OCR 卡名定位 +
  `_card_selected()` 蓝框像素检测（选中卡边框行/列蓝色占比 >0.5，实测选中 ~0.80 /
  未选中 ~0.14）判断选中态，未选中才点击（点已选中卡可能反取消）；识别/选中失败
  只记日志不阻塞。类型配置 `adventure.type`（默认 附近走走）。结算页新增"继续冒险"
  按钮，但连跑仍走 quit 回出门页再进的老链路。
- **OCR 置信度**：命中下限 `src/locators.py` 的 `OCR_MIN_SCORE`（默认 0.5）。
- **异常分级重试**：场景抛异常 → 先回主页面重进场景重试一次（页面错乱多半能
  自愈，不必重启）→ 仍失败才走 `Runner.recover()`（`src/recover.py`：adb reboot →
  启动 QQ → 等并点 `Q宠-*` 入口回宠物页）→ 最后再试一次；连续 `RECOVERY_LIMIT`
  次恢复仍失败才放弃恢复（距上次恢复超过 `RECOVERY_RESET_AFTER` 秒计数重置，
  只拦短时间连续恢复的死循环，支线长期失败不永久锁死恢复能力）。
  恢复成功后必须把新 U2Device 刷新到各场景的 `dev`。
  多次重试均失败按任务类型分流：主任务（学习/打工）发告警通知（`src/notify.py`：
  Windows Toast + OnePush，附当前手机截图存 `runs/alert_*.png`）并退出调度器
  （SystemExit）；支线任务（冒险/踩踩/PK/好友护理/好友雇佣）不退出——抛 `ScenarioFailed`，
  由调度循环重新排期延后 `SIDE_TASK_RETRY_DELAY` 秒重试（`retry_after`，
  参考 qq-farm-copilot 的失败间隔队列机制），先执行其他任务。
- **配置改动**：新配置项加到 `config.yaml`（含 `config.example.yaml` 模板）+ `src/config.py` 的
  dataclass + `src/settings.py` 的 `DEFAULTS`/`validate_field` + **前端表单**
  （`web/src/lib/settings-schema.ts` 定义字段，`web/src/components/SettingsPage.tsx` /
  `NotifyPage.tsx` 渲染，`dashboard.py` 的 `editable_snapshot()` / `apply_settings` 的
  `mapping` 负责回填与写盘）。**四处缺一处就会"界面上看不见"或"改了不生效"**。
- **配置热加载**：新增配置项除了上面几处，还必须同步到 `scenarios/runner.py` 的
  `Runner.reload_config()`（两种引擎每轮调度前都会调用，设置页保存后下一轮生效）
  ——否则运行时改了不生效（历史教训：`work.duration` / `care.interval_seconds` /
  `schedule.encourage_times` / `schedule.main_page_checks` 曾漏同步）。规则：
  - 场景 `__init__` 里从 `self.cfg.xxx` 拷成 `self.xxx` 的**副本属性**必须逐字段同步
    （如 `work.duration`、`care.energy_threshold/clean_threshold/method`、
    `school.attribute/times_per_day`、`adventure.times_per_day/skip_bad_weather/batch/adv_type`、
    `visit/pk.times_per_day`）；
  - 运行时直接读 `scen.cfg.xxx` 的字段也要同步对应场景的 cfg（如 `care_due()` 读
    `care.cfg.care.interval_seconds`、`hire_friend._select_job()` 读
    `hire_friend.cfg.work.duration`）；
  - **优先整体替换**，新增字段放进整体替换对象就不用逐个同步。当前已整体替换：
    各场景 `cfg.schedule`（`scen.cfg.schedule = sched`）、`cfg.employed`、
    `cfg.recover.*`、`cfg.emulator`，以及 `friend_care.cfg.friend_care`、
    `hire_friend.cfg.hire_friend`、`care.cfg.care`、`hire_friend.cfg.work`；
  - 非场景 cfg 的共享层配置单独同步（如 `control.method`：reload_config 里
    `scen.dev.control_method = cfg.control.method`，minitouch 会话懒加载，
    切换方案后下次点击自动按新方案走）；
  - 枚举/取值范围字段先校验、非法回退旧值并记日志（如 `school.attribute`、
    `work.duration`、`work.location`——地点下拉选项统一在 `src/settings.py` 的 `WORK_LOCATIONS`，
    仪表盘下拉与 validate_field 共用，新增地图时只改这一处），不要直接赋值导致运行时 KeyError；
  - 例外（无需热加载）：`adb.*`（连接层，改完需重启调度器）、`runner.engine`
    （切换调度引擎需重启调度器）。
    `gui.*`（`theme`/`mirror`）是**已移除的桌面 GUI 的遗留死配置**：全仓无消费者，
    改它没有任何效果（保留仅为不让老 config.yaml 报错）。
  - **统一失败重试间隔**：`tasks.failure_interval`（设置页"任务失败重试间隔"，`DEFAULTS`/`validate_field`）是各任务 `failure_interval` 的**唯一入口**——`load_config` 构造完各 `TaskItemConfig` 后用全局值覆盖，改 config.yaml 里各任务单独的 `failure_interval` 无效（config 文件里已删除这些行）。
- **⚠ 仪表盘由 launchd 托管，别再用 `./dashboard.sh` 长期跑**（2026-10-09 实测死因）：
  `start_new_session=True`（=`setsid`）**只换 POSIX 会话/进程组，不换 macOS 的
  application coalition**。之前仪表盘从 GUI 应用的会话里起，调度器又是它拉起的，
  两者都留在那个 App 的联盟里；从 Dock 强制退出该 App 时系统按联盟清扫：
  `Dock _LSForceQuitApplication: ... coalition pids to consider killing:[, ..., 88553, 95815]`
  → `killPID ... hard=NO succeeded`，两个常驻进程一起没、手机停在冒险页（当日 11:30:25
  事故）。现在仪表盘交给 launchd（源码通过 `tools/launchd/install.sh` 装 `ai.qqpet.dashboard`；
  macOS 完整 App 自动准备 `ai.qqpet.dashboard.app`，已有源码托管继续被采用，
  KeepAlive + RunAtLoad），调度器仍由 `/api/runner/start` 按需拉起、归属该 launchd 作业的
  联盟，不再随任何 App 退出被连带杀。**只托管仪表盘、不托管调度器**——给调度器加 KeepAlive
  会让按钮「停止」被立刻拉回，破坏交互。
  验证手法（不靠推断，直接问内核）：`proc_pidinfo(pid, PROC_PIDCOALITIONINFO=20, ...)`
  比对 coalition_id——仪表盘/调度器应同组且与 App 不同组。
  `./dashboard.sh` 仍可用（临时/调试），但它起的实例依旧脆；已被托管时它会提示端口占用。
- **进程与线程纪律**：调度器是**独立子进程**（`scenarios/runner.py`，由仪表盘的
  `/api/runner/start` 拉起），不要写死某种引擎——入口走 `run_scheduler()` 按
  `runner.engine` 配置选引擎。日志写 stdout + `runs/logs/<日期>.log`，仪表盘读文件上屏。
  runner 自带**单实例守卫**（`src/instance.py`，见 `runs/runner.pid`），多开时后启动者退出 2。
  仪表盘是**多线程 HTTP 服务**：静态资源按请求读盘（改前端无需重启），
  涉及配置写盘/注入的路径要留意 `_SETTINGS_LOCK` 与访问码守卫。
- **任务队列调度**（默认引擎 `task_queue`）：`TaskQueueRunner` 每轮按 `tasks.order`
  顺序扫描，执行第一个"可执行"的任务（`_eligible`：enabled / enabled_time_range /
  trigger 窗口 / 退避间隔 → `_task_due`：任务自身配额与场景时间窗），跑完一个回顶部
  重扫；成功按 `success_interval`（interval 触发再叠加 `interval_seconds` 最小间隔；
  登记了 pending 的延时收尾任务除外——节奏由 `pending.until` 控制，`next_at`
  立即到期，结算完成的同一轮调度即可接力，避免短活动时凭空多等）、
  失败按统一 `tasks.failure_interval` 退避（**全局唯一入口**：设置页"任务失败重试间隔"，`load_config` 里覆盖所有任务的 `failure_interval`，不再单独配各任务）；daily 触发到点打开执行窗口（窗口内可反复执行直到
  任务返回 False，下一个时间点重开窗口并清除当天不可继续标记）；没有任务可执行时
  睡到最近等待点（上限 `QUEUE_POLL_INTERVAL` 轮询热加载配置）；冒险/学习/打工/雇佣好友
  互斥（不能同时做），作为**主任务组**统一调度（`_main_choice`）：组内优先级由
  `tasks.main_order` 配置（代码默认 `school>hire_friend>adventure>work`；本机现配
  `school>hire_friend>work>adventure`，主号策略「打工到 8h 疲劳档 → 之后全冒险」；> 分隔越靠前越优先，
  没列出的按默认顺序兜底；`MAIN_TASK_KEYS` 在 `src/config.py`），先过 `_eligible`
  （退避/时间窗未到点的任务跳过，否则幻影命中会把排它后面的主任务卡住——如雇佣好友
  CD 复测退避 60 秒但 hire_friend_due 的调度间隔只有几秒），再按配置顺序逐个判定——
  冒险（到点且当天次数未满）/ 学习（学习工作时长未达上限且金币 >= 阈值，或打工不可继续时回退）/
  雇佣好友（到点且次数未满）/ 打工（兜底，当天可继续就可执行），
  不受 `tasks.order` 里四者相对位置影响，金币/学习工作时长每轮循环只读一次（ctx 缓存）；
  主任务组**非阻塞等待（延时收尾）**：`defer_wait=True` 时场景出发后识别到进行中状态
  （"正在学习/打工/冒险"）就 OCR 剩余时间（"剩余00:02:50"，复用
  `parse_employed_remaining`）登记场景 `pending`（`defer_busy_end()`；**`until` 必须按 OCR 读取剩余时间的时刻算**——鼓励宠物点击耗时不能算进余量，否则上课/打工（有鼓励）估算偏晚鼓励耗时那么多秒、冒险（无鼓励）准，历史教训见 `_defer_busy` 的 `read_at`，OCR 失败兜底
  `DEFER_FALLBACK_SECONDS`=15 秒，避免一开始预估余量太大导致收尾偏晚），立即回主页面调度其他任务；**收尾优先**：`_run_first_due` 每轮先查主任务 pending——已到点先 `finish_pending()` 收尾，`PENDING_FINISH_HORIZON`=15s 内即将到点则不执行支线任务、睡到收尾点（否则护理/好友护理一轮 30~40s 会把收尾挤后几十秒，冒险短任务实测偏晚 ~30s）；到点后由 `_main_choice` 调 `finish_pending()`
  收尾：回主页面**出门后**才出现结算页（学习"教师评语"/打工"打工总结"（含雇佣
  好友名称），即 `end_name` 的"分享"按钮），见结算页点 quit（落在出门页面）、
  `on_finish()` 计数，还在进行中（计时误差）则 OCR 剩余时间重估 `until` 下轮再来；
  多轮检测既没结算页也没进行中状态则直接丢弃该 pending（不计数，不重估时间——
  识别不到剩余时间会按 60 秒兜底永远卡在重估循环；结算页若稍后真出现，后续主任务
  出门时会被 wait_busy_end 的结算检测兜底计数）；
  **鼓励宠物**：按钮只在学习/打工进行中页面常驻（结算页实测没有），非阻塞调度在
  登记 pending 离开进行中页面前就地按 `schedule.encourage_times` 快速点够
  （`_encourage_burst()`，0 为不鼓励），finish_pending 重估时还在进行中也会就地再点，
  结算页路径仅作兜底；收尾后再选下一个
  主任务，组内有 pending 未到收尾时间本轮不调度主任务；
  计数统一走 pending 的 `on_finish`（`count_cross` / `_count_hire_and_work`），
  场景本地计数在 defer 分支一律跳过，防重复计数；`pending['until']` 也算等待点；
  主任务组当天结束后只等支线任务的失败退避，没有则退出调度器。
  **启动实测**（`_startup_activity_probe`，每次进程启动只做一次，在职业哨兵启动检查之前）：
  重启会丢掉内存里的 pending（“正在上课”的登记），所以起来先出门实测一次——进行中→登记 pending
  （`probe_activity_state`，非阻塞）、撞结算页→点 quit 并按 `count_cross` 计数（学习结算顺带触发职业哨兵）、
  被雇佣→交 `_run_employed_check`/召回流程、什么都没有→日志记“当前没有进行中的活动”后按最新配置调度。
  实测失败只记日志不拦调度（`ensure_main_page` + `leave_home` 的异常都兜住）。
- **踩踩/PK 调度**：执行器主循环在主页面按各自 `start_time` / 当天次数 / 失败延后期调度
  （`visit_due()` / `pk_due()`），跑对应场景 `run()` 完整流程；不做长等待插空
  （好友入口只在主页面，上课/打工/冒险等待页没有）。
- **护理调度**：每次护理检查（`check_and_care`，体力/清洁不足则喂食/洗澡）按
  `care.interval_seconds`（默认 60 秒，距上次检查起算，`care_due()`）节流，
  两种引擎共用（legacy 主循环每轮开头、队列引擎 care 任务）；场景上次检查时间
  记在 `CareScenario.last_care_at`。**"一键护理"分支点完还会调
  `refresh_status_cache()`**（展开状态面板 OCR 体力/清洁/心情后收起，面板本来就
  展开就直接白嫖一屏 OCR）把这三个值写回 `runs/status_cache.json`——仪表盘总览页
  资料卡右侧的三层状态环（外体力/中清洁/内心情，点环 = 卡片**原位向下展开**出
  三行数值，官方同款交互）读的就是它，不补读的话一键护理每轮都把这三个字段清空、
  环永远是灰的；补读失败只记日志。
- **体力/清洁不足提示条**：提示条（`enabled=false` 内联条，非模态弹窗）会**替换
  开始按钮**，因此检测要覆盖两类阶段：点 `*_start`（上课/打工/冒险/PK，含雇佣
  好友复用的 work_start）时识别 `_start` 的同时同帧检测，以及**等待 `*_start`
  出现的阶段**（`click_until_gone_or_see` 的 wait_name 以 `_start` 结尾时同样
  每轮检测——提示条顶掉开始按钮时可能还没点过 _start，如"前往冒险"阶段等
  adventure_start，只查 click_name 会漏判到导航超时）。检测目标：
  `//*[@content-desc="你的宠物体力不足，请回家补充体力"]` /
  `//*[@content-desc="你的宠物清洁值不足，请回家洗澡"]`（`src/locators.py` 的
  `pet_low_energy` / `pet_low_clean`）；命中则点 back 退出面板 -> 回主页面 ->
  护理一次（`care_once`，同调度器护理检查）-> 抛 `StatBlocked`
  （`src/scenario.py`），调度器 `run_one` 立即重试当前任务一次（不算失败/不重启，
  主任务/支线共用），护理后重试仍被拦截则按常规失败分流（主任务告警退出、
  支线 `ScenarioFailed` 退避）。
- **⚠ 「好友护理」已并入护理任务（不再是独立任务）**：`CareScenario` 与
  `FriendCareScenario` 本来就是同一件事的两半（后者内部还复用前者的喂食/洗澡）。
  现在 care 任务每轮 `check_and_care()` 之后调 `_friend_care_round()`：`friend_care_due()`
  到点（间隔/时间段/开关都算过）就顺路去好友家护理一次（`FriendCareScenario.run(max_rounds=1)`）；
  **好友护理失败只记日志 + 把 `last_care_at` 推后**（等效按 interval 退避一次），
  不再走任务级退避与恢复链路去拖垮"自己的护理"。`friend_care` 已从 `TASK_KEYS` 移除
  （任务列表里不再单独出现），但 `friend_care` 段**原样保留**：开关 / 时间段 / 间隔 /
  好友名 / 方式都没变，所以"单独开关"照旧生效（没开或没到点就什么都不做）。
- **福袋调度**：`gift_bag.enabled` 开启时按 `gift_bag.time_range` + `gift_bag.interval_seconds`
  调度（`gift_bag_due()`，距上次扫描完成起算；间隔默认半小时）。
  **机制来自游戏自带的「玩法规则」**（自己家钱袋面板上那个按钮，点开能读到）：
  ① 宠物**学习/打工表现优异**时系统奖励 N 袋金币、可和好友分享——所以**不是定时刷新**；
  ② **24 小时内未领、或产出新福袋，系统会收走旧袋里的剩余金币**——所以要勤看、及时领。
  一轮按顺序做两件事：

  **自己家的**（主页面 `content-desc="钱袋"`，bounds 300x300、中心 (210,1506)@1080x2412）：
  判据是**袋子自己的两态图标**（用户指出）——未领 = 袋口收拢 + 橙绳结（周围有星星闪光）、
  已领 = 袋口外翻；模板 `scenarios/assets/home_bag_{tied,open}.png`，走 `home_bag_*` 那套。
  **限时皮肤**（2026-10 万圣节）：官方把福袋换成「戴巫婆帽的南瓜」——未领 = 南瓜+帽子+光芒、
  已领 = 帽子消失只剩发光南瓜。新增 `home_bag_{tied,open}_pumpkin.png`，与常规款**两套模板
  都保留、逐个匹配取最高分**（活动结束米黄袋子回来照常可用，不要用新模板覆盖旧文件；
  实测南瓜款自匹配 1.00 / 另一态 0.55~0.58，常规款对南瓜款只有 0.35~0.41）。
  一个人一个周期只能领一次（再点只弹面板不给钱），所以每次都调无副作用；已领就跳过、
  **不弹面板**——顺带兼容游戏里「下次直接领取，不自动展开福袋详情」那个勾选项。

  **好友家**：先点右侧「好友」开列表，**按行扫图标**挑出该去哪几家（用户指出的判据，见
  `scan_friend_list()`）——有可领福袋的行在 x=789..832 带一个 43x43 图标（**无 content-desc**，
  只能模板匹配：`friend_icon_bag.png` 黄袋子+爪印 = 有福袋（限时款 `friend_icon_bag_pumpkin.png`
  南瓜+巫婆帽，同样两套取最高分），`friend_icon_other.png` 粉爱心
  魔法棒 = 不是）；同行右侧 `desc='访问'` 按 y 配对，**只访问这几家**（实测 9 家里常常只有
  1~2 家真有），挑不出来（模板缺失/列表打不开）才退回"遍历好友轮播"的老路子（要求好友都
  排在轮播前几位，**遇到非好友**（右上角"加好友"文字区 OCR）**即刻结束本轮**）。
  进家后用同一套两态模板定位袋子——**自己和好友家的袋子是同一份素材**（都是米黄袋子+橙绳结），
  跨房间自匹配 0.85 / 另一态 0.51。挑好友这步**特意不在 `scan_friend_list()` 里关列表**：
  它返回的是「访问」坐标，调用方必须在列表**还开着**的时候点下去（早先在这里 `go_back()`，
  坐标全失效——真机上表现为"点了没反应 / 点到自己家别处"，踩过）。

  **模板与搜索区的两个坑**：① 模板必须用 `_load_rgb_tpl()`（PIL，按 **RGB**）读——
  `screenshot()` 返回 RGB 而 `cv2.imread` 返回 BGR，偏偏 `cv2.matchTemplate` 是逐通道比对的，
  通道错位会让分数虚低；② `BAG_REGION` 要覆盖靠左的摆位（袋子控件 bounds 的 x 从 60 起，
  旧的 x=100 边界会把模板裁掉一截，0.85 掉到 0.46）。
  领取公告弹窗**只点小号关闭按钮**（`_dismiss_popup()` 的 <300px 尺寸过滤）：全屏遮罩节点
  重心落在领取人列表的好友行上，点它会误触跳转到别人家（实测踩过——关个面板人跑到好友家去了）。
  非好友不点不领；单轮上限 `MAX_SWEEP`=45 防死循环；仅接入 task_queue 引擎（legacy 未接）；
  袋子是 canvas 自绘（无控件 desc），检测只能走截图模板匹配。
- **打工结算领补贴**（`src/ad_bonus.py`）：**每次打工（含雇佣好友打工）结束时，在结算页上
  顺手把「看视频 +N」金币补贴领掉**——打工的结算页标题就是「工资明细」，上面带着那个入口
  （带「限时补贴」角标），不点就白丢一笔（实测每次 45~50 金币）。流程：点「看视频 +N」→
  等 13~15 秒广告倒计时走完 → 点右上角 X → 「恭喜获得」弹层点空白关闭。
  挂载点 `DeviceScenario.claim_settlement_ad_bonus()` 在三处收尾路径上调用（全都在点 quit 之前）：
  `_detect_settlement`（出门撞结算页，启动实测/`wait_busy_end` 都走它）、`finish_pending`
  （队列引擎主路径）、`wait_end`（阻塞/legacy 路径）。开关 `schedule.claim_ad_bonus`（默认开；
  schedule 段在 `reload_config` 里**整体替换**到各场景，所以免额外接线）。
  **该方法绝不抛异常**——调用方都在收尾路径上，抛了会中断收尾、丢 pending、还要走恢复链路，
  所以广告加载失败/网络抖动只记日志返回 0。
  定位细节：关闭 X 的 y 取「观看 N 秒后即可获得奖励」那行的 y、x 用 `0.9417W`
  （两个不同机型/分辨率的截图实测完全一致），不用模板匹配；广告约 1/6 概率不给奖励，
  失败重试一次（重试前先复查页面是否已变「已补贴」——变了说明上一次其实领到了）。
  **历史踩坑**：曾经做成过一个独立的「出门记录」定时任务（遍历出门记录列表、逐条点回去补领），
  用户明确说不要任务、只要每次打工结束领一下，已移除；当时趟出的两个坑记在这里备查——
  ① 被踩踩的记录「[被毛线挑战][🪙15][清洁值-1][心情值-1]」与打工条目同构、金币位置完全重合，
  只能靠"同行左侧标签含「雇」字"区分；② 记录列表整屏 canvas 自绘、控件树只有标题和返回，
  且「列表到底」不能用图像差分判（学习记录两屏像素差 <2，会把还在滚动的列表误判成到底）。
- **⚠ 「好友雇佣」已并入打工（不再是独立任务）**：它本来就跟打工是同一件事的两种
  "挑雇员"入口——代码里 `hire_friend` 点完 hire 就直接进打工面板、后续 `select_place`
  / 选时长 / `work_start` / 等结束全部复用 `WorkScenario`，计数时还同时记一次打工。
  现在改为：`WorkScenario.run()` 每轮开头先看配没配 `work.hire_name`，
  走 `_work_via_friend_hire()` —— 去好友家点 hire（复用 `FriendHireScenario` 的导航与
  CD 经验）→ 直接在打工面板开工；**收尾/计数登记在 work 实例上**（pending 必须在 runner
  持有的场景实例里，否则调度器收尾看不到）。条件不满足就落回原来的"自己出门 → 面板挑人"。
  相关配置（`work` 段）：`hire_name`（**唯一入口**——配了就先顺路去他家雇，留空则只在
  面板里挑人）/ `hire_wait`（等他空闲再雇）+ `hire_wait_action`
  （**只有 hire_wait 开启时才可选**，界面上没开时置灰；`FieldDef.enabledWhen` 实现）：
  `不做任何事情` 原地等 / `去冒险` 抛 TaskDeferred 让出调度器（默认）/ `继续打工` 不等、
  直接走面板。两条等待路径共用它：面板里目标忙（`_hire_target_or_wait`）与好友家 CD
  没好（`wait_hire_ready` 的 policy 映射 wait/defer/panel）/ `hire_daily_hours`
  （**游戏机制：每个好友每天最多被雇 4 小时**，满了就不再雇他——账本 `runs/hire_quota.json`，
  由 `src/hire_quota.py` 读写、跨天自动清零；原来"每天 8 次 × 45 分钟 = 6 小时"其实
  超出上限，有 2 次白雇）。**行为固定，没有"顺路雇好友开关 / CD 策略"这类选项**——
  加过一轮三选一（defer/wait/panel）被用户否掉（"别弄个选项迷惑人"）：现在 CD 没好
  一律照常去打工（面板挑人），**不空等也不延后**。`hire_friend` 已从 `TASK_KEYS`/`MAIN_TASK_KEYS` 移除，
  `scenarios/hire_friend.py` 保留为被 work 调用的类（`__main__` 仍可单独跑调试）。
- **被雇佣检查调度**：`employed.enabled` 开启时，被雇佣时间段（`employed.time_range`，
  HH:MM-HH:MM 支持跨零点）内按 `employed.interval_seconds`（默认 60 秒）间隔出门
  检查是否被雇佣中（`employed_due()`，`scenarios/employed.py` 场景只做检测）；
  **非阻塞**：`employed_recall_ready` 单次判定到召回时机（`employed.action` 策略）
  才 `_recall_employed` 召回计数，没到就回主页面等间隔后再查，中间可跑其他任务
  （基类 `wait_employed_back` 仍是阻塞版，供主任务流程 `wait_busy_end` 用）；
  不在 `tasks.order` 里——队列引擎到点优先于队列任务先检查（`_run_employed_check`），
  巡检无论是否检测到都返回 True（同好友护理，False 会被标记当天不可继续）；
  - **学习/工作时长规则（替代旧“每日点数”）**：学习结算按持久化的**课时时长**累计
    （`school_progress.json` 的 `duration`：10分钟/30分钟，学习开始时
    `set_current_school_duration` 写入；旧会话无该字段时回退学园阶段映射
    初级10/中级20/高级30/进修45 分钟）；打工结算按持久化的 `work.duration` 累计
    （`work_progress.json` 的 `duration`：10分钟/45分钟/2小时）。累计时长（秒）存
    `study_secs`/`work_secs`，`load_durations()` 读取（仪表盘“今日”显示
    `已学习/工作/总时长（小时）0.0/0.0/0.0` 1 位小数）；`_duration_over` 判断
    `schedule.daily_hour_limit`（小时，0=不限）达上限后**今天不再学习只打工**；
    `schedule.work_stop_hours`（小时，0=不限）判断 `_work_over`：学习+打工
    合计 >= 该值后**今天连打工也停**——游戏效率档：合计 >8h 收益效率 25%、>12h 10%。
    **仪表盘设置页只暴露一个**「合计满则停止」（`numStopTotal` → `stop_total_hours`，
    不区分学习/打工）：`apply_settings` 校验后把上面三个键一起写成同一个值
    （`stop_total_keys`），快照里由 `stop_total_hours()` 折算（= 两项都停的时刻，
    三项不一致时会提示）；三项分开设（如 8/12/12）仍可手改 config.yaml。
    **`efficiency_tier1_hours`/`efficiency_tier2_hours` 不是设置项**：设置页只在输入框
    下面渲染一张 4 行小表（`stopExplain()` → `.mintbl`，列 = 什么时候 / 会发生什么：
    没到 8h 正常收益、满 8h 降到 25%（只提示）、满 N 小时两项一起停、次日 0 点清零），
    不给输入框也不写长段落；数字随 `editable_snapshot()` 动态生成，config.yaml 仍可
    手改、引擎照读（只影响界面/日志的效率百分比）。
    **本机现配 8**（主号「疲劳后全冒险」：打满 8h 停打工全转冒险；依据：疲劳档后打工
    ~20/~8 金每小时 vs 冒险 ~500 金每小时，且实测 12h+ 下冒险收益不受疲劳档影响）。
    两个上限都**实时求值不设死标记**，
    跨天时长清零自然恢复；效率档写进日志（今日时长行/启动行）。**主任务结束后
    `_sleep_until_next` 的"留守"分支**：只要还有"启用且未判死"的任务就按
    `QUEUE_POLL_INTERVAL` 轮询留守（护理 60s/好友护理 120s/每日支线到点/次日打工
    靠它接力），勿按 `task.next_at` 估等待点——interval 任务的 next_at 只是退避
    下限，真实节奏还叠加任务 interval_seconds 与场景级节流（care_due/friend_care_due），
    误判"没有等待点"会把调度器直接关停（work_stop 打满后该分支每天可达，已实测踩坑）；
    首次运行新版本时老进度只有次数（learned）没有时长字段，按旧版
    `schedule.school_factor`/`work_factor`（每节/每次的分钟数）自动迁移补上（只做一次）；
    旧版 `school_factor`/`work_factor`/`daily_point_limit` 仍留在 config.yaml 仅为
    迁移/兼容老配置，不在设置页显示、不参与调度。
    **进度文件读写统一走 `src/progress_store.py`**（`src/progress.py` 只是兼容层）：
    跨天规整 + 原子写入 + 损坏兜底都在 store。跨天时旧日期当天次数归档进 history、
    清掉 `learned/study_secs/work_secs`；**`school/duration` 作为会话元数据跨天保留**
    （昨晚开始的打工/上课今天收尾要拿它累计今天的时长），由 `get_daily_field` 按
    `date==今天` 门控；`set_current_school`/`set_current_work_duration` 跨天**总是落盘**
    推进日期（即使值没变，否则同校/同时长第一天结算会拿不到元数据）。
    禁止整字典替换成 `{date}`（曾因此把 school_progress.json 的 history/learned 全丢）。
  **被雇佣时间段内主任务（冒险/学习/打工/雇佣好友）不触发**（队列 `_main_choice`
  返回 None，pending 收尾不受影响；legacy 跳过冒险/雇佣好友/学习打工段睡到下次检查）。
- 控制台中文乱码是 Windows GBK 终端显示问题，日志文件（UTF-8）里是正常的，不要当 bug 修。
- **模拟器模式**（`--emulator`）：模拟器里 QQ 搜索卡片的宠物入口默认是空的（点不到
  `Q宠-*`），由 `src/opener.py` 打开宠物主页。**当前方案零注入**（旧版全程常驻 frida
  注入被 QQ 风控"使用外挂插件"，详见 `src/opener.py` 模块 docstring），分级：
  0. **MuMu 机型伪装映射改写**（`ensure_device_spoof`，启动 QQ 前执行）：由配置项
     `emulator.device_spoof` 控制（设置页"MuMu 机型伪装"开关，**默认关闭**——门禁
     MMKV 补丁已翻转过的设备不需要伪装；opener 每次运行重新 `load_config`，保存即生效）：
     MuMu 的 `/system/etc/mumu-configs/app-device-prop-*.config`（按 Android 版本
     命名，多个匹配取实际含 QQ 行的）是"包名正则
     →机型 profile"映射，QQ（`com.tencent.mobileqq`）被故意喂 `yyb.config`（完整
     caas/Intel/AOSP/tablet 属性 dump），注入机制是 QQ 进程里的 MuMu hook lib
     （libnemuinitaidl.so/libjavahelper.so）；把 QQ 行改成目录里已有的真实手机
     profile（含 `ro.build.characteristics=default`，优先 `honor_magic4pro.config`），
     QQ 进程重启即按真机身份运行——门禁原生通过，搜索"QQ宠物"卡片出现"宠物"功能
     入口、双击即进宠物主页，与真机完全一致。/system 硬只读（USER 固件无
     disable-verity），只能 root `mount --bind` 覆盖（暂存
     `/data/local/tmp/qqpet_adp.config`），**重启模拟器后失效需重挂**（函数幂等，
     每次 opener 运行自动检查重挂）；非 MuMu 设备静默跳过；
  1. 无伪装时模拟器被 QQ 判成 TABLET（`ro.build.characteristics` 含 tablet），
     宠物设备门禁 `PetQQMC.e()` 读 UnitedConfig 107805 配置的 `enable_tablet`
     （默认 0）拦截一切官方跳转；opener 用 root 直接改 UnitedConfig 本地 MMKV 缓存
     （`files/mmkv/united_config_mmkv_<uin>`，append-only 追加一条 `enable_tablet:1`
     的 `107805_key_content` 记录 + 重算 CRC 元文件）本地翻转门禁；
  2. 然后官方 scheme `am start -a VIEW -d "mqqapi://qpet/open?version=1&src_type=app&source=1"`
     （JumpActivity，exported，普通 shell 可发）由 QQ 自己打开宠物主页并初始化宠物 SDK。
  调度三级：scheme 直开（伪装/补丁持久，二次运行零写盘）→ MMKV 补丁后 scheme →
  frida 一次性 SDK init + am start fragment 兜底（QQ 大改版 MMKV 格式变化时）。
  注意：QQ 服务端重新下发 107805 配置会覆盖补丁，opener 每次启动自检重补（幂等）；
  **Root 按需**（`_has_root()` 软检查：**`adb shell` 本身就是 root 也算**——MuMu for Mac 的 adbd 直接给 uid=0，而它自带的 `su` 是坏的（`su -c id` 无输出/rc=1），所以所有要 root 的设备命令统一走 `_root_shell()`：shell 已是 root 就直接跑、否则套 `su -c`）：伪装重挂、MMKV 补丁自检/重补、frida 兜底
  都只在有 Root 时执行；无 Root 直接 scheme 直开——伪装（bind-mount 每次开机由
  opener 重挂）与 MMKV 补丁（QQ 数据盘持久）都已在有 Root 时打过一次后，日常运行
  可永久关闭 Root（实测 MuMu/雷电9 关 Root 后 scheme 直开均正常）；scheme 失败且
  无 Root 才报错提示临时开一次 Root 补补丁。
  scheme 打开的宠物页宿主是 `AdelieFragmentActivity`（am start fragment 是
  `QPublicFragmentActivity`），渲染判定两种都认。
  好友访问（踩踩/PK/好友护理/好友雇佣）：门禁翻转后（`opener.GATE_OPEN=True`）
  游戏内 好友->访问 与真机一致直接可用；仅 frida 兜底模式（门禁仍关）才走
  `visit.py` 的 `_goto_first_friend_emulator`（am start 直开 + `runs/friend_entry.json`
  入口缓存）。
  frida-server 由 `tools/fetch_frida_server.py` 拉到 `resources/frida-server/`
  （`runs/resources/frida-server/` 可覆盖）；源码运行缺失时自动联网下载；
  frida 客户端版本必须与它一致（`requirements.txt` 的 `frida` 锁定版本）。
  Frida 17 起 `Java` 桥不再内置在运行时里（脚本里没有全局 `Java`），注入前由
  `_wrap_java_bridge` 把 frida-tools 的 `frida-java-bridge` 暴露为全局 `Java` 再拼 init 脚本；
  frida-tools 版本随 frida 一起锁定（`requirements.txt`）。
  模拟器模式下启动与 `recover()` 都走 opener 打开宠物主页（`src/recover.py` 的 `use_opener` 分支），
  不再依赖 `Q宠-*` 入口；宠物主页已由恢复流程打开时传 `--skip-opener`
  跳过启动时的 opener 打开（避免一次流程开两次宠物主页）。
  打包的模拟器版 exe 内置 `emulator_mode.txt` 标记，启动默认开启模拟器模式
  （`src/config.py` 的 `is_emulator_build()`）；源码运行用 `--emulator` 显式开启。
- 不再修改手机分辨率/密度（wm size/density）：定位分辨率无关，无此需求。
  （**例外**：给仪表盘提取官方素材时可临时放大分辨率截图，用完必须
  `wm size reset; wm density reset` 还原，且要在 QQ 没在跑时改 —— 见 dashboard.py 一行的「房间背景」。）

## 打包

**上游的根目录 `build.py` / Windows CI 已移除；现有打包入口在 `desktop/`**——
源码调度器可用 `./run.sh` 调试；仪表盘长期运行仍用 `tools/launchd/install.sh`，
`./dashboard.sh` 只作临时调试。

- macOS 完整 `.app`：`desktop/macos/scripts/build-macos.sh`，包含 Tauri UI 与随包
  Python 后台/调度器，默认自动本机启动，无 URL 输入。`scripts/verify.sh` 使用临时数据根做
  导入、资源、真实 OCR 与调度器命令离线自检，不碰设备或真实进度。
- Windows 原生 App：`desktop/windows/build_native.py`（必须在 Windows x64 上构建；
  产物 `dist/QQPet-Steward_<version>_windows_x64-setup.exe` 与 SHA-256 校验文件）。
  复用 `desktop/macos/` Tauri crate，通过 `desktop/windows/tauri.windows.conf.json`
  选择每用户 NSIS、开始菜单入口、构建时生成的 ICO 与 WebView2 缺失安装流程。
  `build_portable.py --no-zip` 仅负责内部 Python runtime；暂存目录
  `desktop/macos/target/runtime/QQPetRuntime/` 沿用 base config 的单一 `backend/` 资源映射，
  不要在 Windows override 另加 resources 字典导致深合并留下旧路径。
  `.github/workflows/windows-desktop.yml` 静默安装生成的 NSIS 后，通过 `verify_portable.py`
  验收已安装后台，再用 `verify_native.py` 验已安装 App 的真实窗口/自动登录/关闭重开；
  测试用临时数据根和 WebView2 profile，
  不碰真实设备、配置或进度。

`src/config.py` 保留 `APP_ROOT` / `RESOURCE_ROOT` 双根设计：`QQPET_DATA_DIR` 显式覆盖
可写数据根；未覆盖时 macOS 打包版默认 `~/Library/Application Support/QQPetCopilot`，
Windows 原生 App 默认 `%LOCALAPPDATA%\QQPetCopilot`，开发用便携模式仍为 exe 目录，
源码默认为仓库根。只读 `RESOURCE_ROOT` 在冻结版为
`sys._MEIPASS`、源码为仓库根，不随数据根重定向。冻结版 `resource_path()` 让
`APP_ROOT/runs/` 下的同名文件覆盖包内资源（如 `runs/resources/frida-server/`）。
完整 App 采用已有后台时沿用其数据根；包内不保存用户配置、访问码或进度。
`resources/` 的第三方二进制不入库，按需用 `tools/fetch_*.py` 准备；
完整 App 只打指定控制资源和三个公开 OCR 模型，不打整个真实 `runs/`。

## 自动更新（Tauri updater）

- 壳层用 `tauri-plugin-updater = "=2.13.2"`（`2.12.0` 的 macOS 安装失败会删除已安装 app，禁止使用）；
  `plugins.updater` 在 `desktop/macos/tauri.conf.json`，更新源是
  `https://github.com/mps233/QQPet-Steward/releases/latest/download/latest.json`。
- minisign 私钥 `~/.tauri/qqpet-updater.key`，密码 `~/.tauri/qqpet-updater.key.password`；
  公钥写在 `tauri.conf.json`。私钥/密码丢失后已安装用户无法再验证更新，只能手动重装。
  这不是 Apple 签名；macOS 当前仍是 ad-hoc（无 Developer ID/公证）。
- **点击才下载**：`panel_update_install` 先下载，成功后停调度器（`POST /api/runner/stop`，
  30 秒超时 + 轮询 `scheduler.alive=false`），再 `install()` 并 `request_restart()`。
  安装失败会尝试恢复调度器；成功时写 `runs/update_resume.signal`，新 App 启动后自动恢复托管。
- macOS 的 `.app.tar.gz` 必须由 `desktop/macos/scripts/package-release.sh` 在
  `restore_runtime_symlinks.py` + 最终 codesign 之后手工生成，并
  `tauri signer sign --app-version`；不要在 macOS 基座配置打开
  `bundle.createUpdaterArtifacts`（Tauri 生成的是后处理前的半成品）。
- 更新后新 App 用 `/api/client/meta` 的 `runtime.build_fingerprint` 判断旧后台：
  macOS `launchctl bootout` 后重装 `backends/<fingerprint>` 并 bootstrap；
  Windows 校验 `runs/dashboard.pid` 的进程映像路径后 `taskkill /T`。0.8.4 缺该字段按 mismatch。
- 发布资产：Mac 4 个（dmg/zip/`.app.tar.gz`/`.app.tar.gz.sig`）+ Windows
  setup exe、`.sig`、native zip + `latest.json` + `SHA256SUMS.txt`；
  `.github/workflows/release-upload.yml` 负责生成并上传 `latest.json`。
  CI 需要 `TAURI_SIGNING_PRIVATE_KEY` / `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`。
- `tools/make_update_manifest.py` 可手工生成 `latest.json`（`--platform KEY URL SIG` 可重复）；
  `latest.json` 的 `signature` 是 `.sig` 文件内容，不是路径/哈希。

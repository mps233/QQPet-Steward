<p align="center">
  <img src="docs/cover.png" alt="QQPet-Steward：应用图标与名称，背景为游戏房间" width="100%">
</p>

<h1 align="center">QQPet-Steward</h1>

<p align="center">让 QQ 宠物自动上课、打工、冒险，提供 macOS / Windows 原生 App 与手机可用的浏览器仪表盘。</p>

<p align="center"><a href="README.en.md">English</a></p>

<p align="center">
  <a href="https://www.python.org/"><img alt="Python 3.13" src="https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white"></a>
  <a href="https://github.com/openatx/uiautomator2"><img alt="uiautomator2" src="https://img.shields.io/badge/uiautomator2-3.2+-2C8EBB"></a>
  <a href="https://github.com/RapidAI/RapidOCR"><img alt="RapidOCR" src="https://img.shields.io/badge/OCR-RapidOCR-FF6F00"></a>
  <a href="https://react.dev/"><img alt="React 18" src="https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black"></a>
  <a href="https://tauri.app/"><img alt="Tauri 2" src="https://img.shields.io/badge/Tauri-2-24C8DB?logo=tauri&logoColor=white"></a>
  <a href="https://developer.android.com/tools/adb"><img alt="Android / adb" src="https://img.shields.io/badge/Android-adb-3DDC84?logo=android&logoColor=white"></a>
  <a href="https://github.com/mps233/QQPet-Steward/blob/main/LICENSE"><img alt="许可证：GPL-3.0" src="https://img.shields.io/github/license/mps233/QQPet-Steward"></a>
  <a href="https://github.com/mps233/QQPet-Steward/releases/latest"><img alt="最新版本" src="https://img.shields.io/github/v/release/mps233/QQPet-Steward"></a>
</p>

## 简介

QQPet-Steward 代你玩 QQ 宠物。它通过 adb 连接 Android 手机，用 uiautomator2 操作界面、用 RapidOCR 识别屏幕，完成选课上课、打工、冒险、喂食洗澡和访问好友。你设定目标和上限，调度器决定下一步做什么。

程序跑在手机旁边的电脑上。macOS 与 Windows 版本都是原生 App，随包提供 Python、adb、OCR 模型和本机仪表盘，不需要填写服务器地址，也不用另装运行时。同一套仪表盘可以在同一局域网的手机上打开：扫码配对一次，之后就能启停调度器、修改设置，查看日志、收益和实时画面。各场景、定位注册表和 OCR 层来自 [qq-pet-copilot](https://github.com/490720818/qq-pet-copilot)（GPL-3.0）；本仓库增加了原生 App、React 仪表盘、任务队列调度、通知与日报，并适配了新版 QQ 界面，完整差异见 [MAC-PORT.md](MAC-PORT.md)。

## 核心特性

- **学习与打工** — 自动进学园选课、去八个地点打工，支持短课/长课档位与 10 分钟、45 分钟、2 小时工时。每日学习与工作时长按配额累计，到达各自上限后停止。
- **好友雇佣** — 配置好友后，打工时先去好友家雇佣；每个好友的每日雇佣时长上限在本地记账。
- **冒险** — 按配置的类型连续冒险并记录每次收益；开启 `adventure.skip_bad_weather` 后，遇到「天色不对」会自动召回。
- **护理** — 体力或清洁低于阈值时自动喂食、洗澡；开启好友护理后，作为同一任务的一部分顺路完成。
- **好友互动** — 每日踩踩、按规则 PK，以及到指定好友家护理。
- **被雇佣检查** — 在设定的时间段内出门查看是否被雇佣，并按策略决定召回或继续让利。
- **福袋** — 扫描自己家和好友家的福袋，只领取已经成熟的那些。
- **任务队列调度** — 每个任务有独立开关、顺序、间隔或每日时间窗，以及失败退避。长时间活动采用延时收尾：登记剩余时间后回主页面，先执行其他任务，到点再回来结算。
- **自愈与告警** — 单步失败先回场景重进；仍失败时按 `recover.method` 恢复（重启游戏，或重启设备/模拟器）后再试一次。主任务仍失败时附手机截图发告警并停止调度；支线任务改为退避重试。
- **仪表盘与远程控制** — 七页 Web 仪表盘、Telegram 反向控制、桌面通知、飞书、Telegram 与 OnePush 推送，以及打工学习日报和冒险日报。

## 安装

### 准备

- **桌面 App：** Apple Silicon 上的 macOS 26 及以上，或 Windows x64。
- **手机：** 一台能通过 adb 连接的 Android 设备（或模拟器），已安装并登录 QQ，且宠物功能可用。
- **源码运行：** Python 3.13 与 Android platform-tools。源码方式在 macOS 和 Windows 上受支持；Linux 尚未验证。

### 桌面 App

**macOS：** 从 [Releases](https://github.com/mps233/QQPet-Steward/releases/latest) 下载最新 DMG 或 ZIP。构建面向 Apple Silicon，要求 macOS 26 及以上。

**Windows：** 用 `desktop/windows/build_native.py` 构建 x64 安装程序；当前 Release 尚未附带 Windows 安装包。安装程序按当前用户安装，缺少 Microsoft WebView2 时会一并安装。

macOS 版本使用 ad-hoc 签名，尚未完成 Apple 公证；Windows 版本没有 Authenticode 签名。首次打开时系统可能弹出安全提示。每次发布同时提供 SHA-256 校验值。

### 源码运行

```bash
# 0) 准备 adb 与手机（已有 adb 可跳过）
brew install android-platform-tools
adb devices
adb shell settings put global stay_on_while_plugged_in 7

# 1) Python 环境
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2) 配置
cp config.example.yaml config.yaml

# 3) 启动仪表盘，交给 launchd 托管（推荐长期运行）
./tools/launchd/install.sh
```

单台设备且 adb 在 `PATH` 中时，`adb.path` 与 `adb.device_serial` 可以留空。示例配置里的 `adb.path` 默认是 Windows 随包路径，macOS 上记得改成空字符串。手机设有锁屏密码时，把 `recover.method` 设为重启游戏，不要用重启设备。

Windows 源码方式：用 `py -3.13 -m venv .venv` 建环境、`.venv\Scripts\pip install -r requirements.txt` 装依赖；调度器用 `.venv\Scripts\python scenarios\runner.py` 启动，仪表盘用 `.venv\Scripts\python dashboard.py --port 8787`。Windows 没有 launchd，开机自启请用任务计划程序。

### 构建桌面 App

```bash
desktop/macos/scripts/build-macos.sh
```

```powershell
.venv\Scripts\python.exe desktop\windows\build_native.py
```

macOS 产物位于 `desktop/macos/target/release/bundle/macos/QQPet-Steward.app`；Windows 安装器位于 `dist/QQPet-Steward_<version>_windows_x64-setup.exe`。构建前提与离线校验见 [desktop/macos/README.md](desktop/macos/README.md) 和 [desktop/windows/README.md](desktop/windows/README.md)。

## 使用方式

1. 打开 App 或仪表盘，完成四步新手引导：连接设备、确认主页、选择任务、完成准备。
2. 点右栏的 **启动**。同一台机器只能运行一个调度器，多开时后启动的会自行退出。
3. 想把手机当遥控端，在电脑上打开 **设置 → 手机遥控**，用手机扫描一次性二维码。配对票据五分钟内有效且只能用一次；已配对设备可在同一页撤销。
4. 源码部署的仪表盘监听 8787 端口，首次访问需要输入 `runs/dashboard_access.token` 中的访问码。
5. 只在终端调试调度器时运行 `./run.sh`（Ctrl+C 停止），不要和仪表盘的「启动」同时开。

仪表盘共七页：总览、冒险、职业、日志、通知、设置、实时画面。总览页的界面导览见下图。

![仪表盘总览页导览](docs/overview-guide.png)

*总览页界面导览。界面本身为中文，标注同为中文。*

Telegram：在 **设置 → TG 机器人** 里配置机器人 token 和允许的会话。机器人响应 `/help`、`/status`、`/get`、`/set`、`/attrs`、`/log`、`/run`、`/halt`、`/pause`、`/resume`；白名单之外的会话不会收到回复。

## 配置

推荐在仪表盘设置页修改，也可以直接编辑 `config.yaml`。设置保存后下一轮调度生效；adb 连接设置与调度引擎需要重启调度器。完整字段与默认值见 [config.example.yaml](config.example.yaml)。

| 配置 | 作用 |
| --- | --- |
| `adb.path` / `adb.device_serial` | adb 路径与设备序列号 |
| `control.method` | 控制方案：`injectInputEvent`（真机默认）或 `minitouch`（模拟器更快） |
| `school.attribute` / `school.duration` / `school.times_per_day` | 学科 / 短课或长课 / 每日课程上限（0 为不限） |
| `work.location` / `work.duration` | 打工地点（八个可选）/ 10 分钟、45 分钟或 2 小时 |
| `work.hire_name` / `work.hire_wait` / `work.hire_daily_hours` | 优先雇佣的好友 / 对方忙时是否等待 / 每人每日上限 |
| `schedule.study_quota_hours` / `work_quota_hours` | 每日学习与打工配额 |
| `schedule.daily_hour_limit` / `work_stop_hours` / `coin_threshold` | 学习停止点、合计停止点、金币阈值 |
| `adventure.*` / `employed.*` | 冒险次数、时间窗与类型；被雇佣检查时间段与召回策略 |
| `care.*` / `friend_care.*` | 护理阈值与方式；好友护理对象与间隔 |
| `tasks.failure_interval` / `recover.method` | 任务失败重试间隔 / 异常恢复方式 |
| `notify.*` | 告警渠道与日报 |

打包版把配置与进度放在 macOS 的 `~/Library/Application Support/QQPetCopilot` 或 Windows 的 `%LOCALAPPDATA%\QQPetCopilot`；可用 `QQPET_DATA_DIR` 指定其它目录。

改默认值之前，有两个游戏机制值得先了解：

- **护理勋章必须手动护理。** 一键护理不计入勋章进度。要拿勋章，把 `care.method` 与 `friend_care.method` 设为 `ocr检测`，由程序逐步喂食、洗澡。
- **学习与打工有疲劳档。** 两项合计超过 8 小时后收益降到 25%，超过 12 小时后降到 10%。设置页只提供一个「合计满则停止」，到点后两项一起停并转去冒险；学习、打工各自的停止点可在 `config.yaml` 里分开设。

## 项目结构

```text
scenarios/   调度器、任务队列与各游戏场景
src/         设备控制、OCR、定位、配置、进度、通知
web/         React 仪表盘（构建到 web/dist）
dashboard.py 本机 HTTP 服务与 API
desktop/     macOS 与 Windows App 打包
tools/       校准工具、资源下载脚本、launchd 安装脚本
tests/       单元测试
```

## 发布与更新

构建产物发布在 [Releases](https://github.com/mps233/QQPet-Steward/releases)。macOS App 会自行检查 GitHub Releases：有更新时点击下载，随后停止调度器、安装、重启，并恢复之前的调度状态。Windows 构建尚未接入更新清单，需要手动安装新版本。

## 致谢

- [qq-pet-copilot](https://github.com/490720818/qq-pet-copilot) — 本仓库所依据的上游项目。
- [uiautomator2](https://github.com/openatx/uiautomator2) — 设备控制与输入。
- [RapidOCR](https://github.com/RapidAI/RapidOCR) 与 [PP-OCRv6](https://github.com/PaddlePaddle/PaddleOCR) — 文字识别。
- [minitouch](https://github.com/DeviceFarmer/minitouch) — 触摸注入。
- [qqpet-module-opener](https://github.com/yikehuang/qqpet-module-opener) — 模拟器中打开宠物页的思路。

## 免责声明

本项目仅供学习研究自动化与 OCR 技术使用。自动化操作可能违反游戏服务条款，由此产生的一切后果由使用者自行承担。QQ 宠物及其游戏素材归腾讯所有，本项目与腾讯没有关联。

## 许可证

GNU General Public License v3.0，详见 [LICENSE](LICENSE)。

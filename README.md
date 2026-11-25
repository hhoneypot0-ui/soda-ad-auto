# SodaAdAuto · 汽水音乐自动领免费听时长

🌐 **官网 / Homepage：https://hhoneypot0-ui.github.io/soda-ad-auto/**

> 把"看广告领免费听天数"这件机械事，交给一台电脑和一根 USB 线。
>
> A Windows tool that automates Soda Music's (汽水音乐) official "watch ads to earn
> free-listening days" campaign via real-device UI automation (ADB + OCR). It only
> simulates human taps — no app modification, no ad-report forgery, no DRM bypass.

## 原理 | How it works

手机连 ADB → 脚本循环 `screencap` 截屏 → RapidOCR 识别界面文字 → 按优先级模拟真实点击：

```
误触取消 > 领取奖励(中央绿色大按钮) > 关闭弹窗 > 继续观看 > 跳过(倒计时归零)
广告播完进入无按钮落地页 → 自动 BACK 返回奖励弹窗 → 循环
```

每次成功领取 = 平台官方奖励 +1 天免费听时长（上限 30 天）。

## 特性 | Features

- 🎨 **流体云监控面板**：ColorOS 风格玻璃拟态网页面板，本地 8765 端口，实时天数/进度/日志/手机画面
- 🔇 **全程强制静音**：开跑压音量、运行中巡检、停止后保持静音
- 🛡 **多重防呆**：倒计时未到不点、长句免责声明免疫（只点 ≤12 字按钮体）、诱饵按钮（立即下载/进入直播）永不点、同目标连点 3 次自动休整、未知画面自动截图存档
- 🧹 **退出即清图**：所有含手机画面的截图在停止时自动删除，不留隐私痕迹
- ⏱ **自动收工**：连续 6 分钟领不到（奖励刷完）或达到每日目标/6 小时上限即停，可挂机过夜
- ⚙️ **零代码适配改版**：App 文案变化只需在 `config.json` 增删关键词

## 环境要求 | Requirements

| 项 | 要求 |
|---|---|
| 电脑 | Windows 10/11（Linux/macOS 需自行替换 .bat） |
| Python | 3.10 – 3.14（实测 3.14） |
| Android | platform-tools（adb），手机开 USB 调试并授权 |
| App | 汽水音乐（实测 19.1.0，包名 `com.luna.music`） |

## 快速开始 | Quick start

```bash
git clone <本仓库>
cd soda-ad-auto

# 1. 建环境（首次一次即可）
双击 setup.bat

# 2. 手机连电脑、开 USB 调试、打开汽水音乐到"看视频免费听"页面

# 3. 双击 启动.vbs —— 完全静默后台开跑，欢迎页自动在浏览器弹出
#    停止：双击 停止.vbs
#    全程 0 终端窗口；重复双击「启动」会弹窗提醒已有实例在跑
```

浏览器面板（启动后自动打开）：<http://127.0.0.1:8765>

## 配置 | Configuration

首次运行自动生成 `config.json`，常用项：

| 键 | 说明 |
|---|---|
| `serial` | 设备序列号，`null` 自动选择（仅一台设备时） |
| `adb_path` | adb 路径，默认走 PATH，可写绝对路径 |
| `daily_goal` | 领取次数上限，`0` = 不限量 |
| `max_runtime_min` / `max_stuck_rounds` | 硬时长上限 / 连续无进展自动收工 |
| `reward_targets` 等五个关键词表 | 各类按钮的 OCR 文本，改版时在此维护 |
| `mute_during_run` | 全程强制静音 |
| `cleanup_on_exit` | 退出自动删除 runtime/debug_clicks 下所有截图（日志保留），默认开 |

调试命令：

```bash
.venv\Scripts\python.exe qishui_auto.py --once      # 截一屏打印全部识别文字
.venv\Scripts\python.exe qishui_auto.py --dry-run   # 全流程试跑不点击
```

## 免责声明 | Disclaimer

- 自动化脚本违反汽水音乐用户协议中"禁止使用自动化工具"的条款，**账号存在被限制/封禁的风险**，责任自负。本项目仅供 UI 自动化技术学习与个人小范围体验，请勿用于批量注册、多号矩阵等滥用场景，勿商业分发。
- 出现平台警告（验证码、领取异常变慢）请立即停用。
- 本工具不破解会员、不绕过付费、不修改 App、不伪造广告上报，赚的每一分钟时长都是平台活动规则内真实可得的。

## License

MIT

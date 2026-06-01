# ☀️ solar-brightness

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform: macOS](https://img.shields.io/badge/platform-macOS%20Apple%20Silicon-orange.svg)]()
[![Python: 3.9+](https://img.shields.io/badge/python-3.9%2B-green.svg)]()

**基于太阳高度角 + 实时天气的外接显示器自适应亮度调节**

> 唯一将「太阳高度角 × 天气修正 × 多显示器硬件适配」整合的纯命令行方案。
> 无需光感传感器，通过天文算法 + 免费天气 API 推算环境亮度，DDC/CI 平滑控制外接显示器。

---

## ✨ 特性

- ☀️ **太阳高度角曲线** — 不是简单的日出/日落时间，而是根据太阳实际位置做 `sin(θ)^p` 平滑映射
- 🌤️ **实时天气修正** — 接入 Open-Meteo 免费 API，阴雨天自动降低亮度（可调整强度）
- 🖥️ **多显示器独立参数** — 每台显示器可设不同的 min/max/offset，适配不同硬件
- 🎚️ **渐变过渡** — 每 5 分钟调一次，每次最多 ±3~5%，完全无感
- 📍 **自动定位** — IP 定位 + 离线城市数据库，也支持手动配置经纬度
- 🔌 **Apple Silicon 原生支持** — 基于 m1ddc (DDC/CI)，M4/M5/M6 实测通过
- 💻 **纯 CLI** — 无 GUI 依赖，launchd 后台运行，系统资源占用可忽略
- 🌏 **国内网络友好** — 内置 40+ 中国城市坐标表，直连 ipip.net 绕过代理

---

## 🚀 快速开始

### 前置条件

- macOS (Apple Silicon: M1/M2/M3/M4/M5)
- 外接显示器支持 DDC/CI（大部分现代显示器都支持）
- HDMI / USB-C / DisplayPort 连接均可

### 安装

```bash
# 1. 安装 m1ddc (DDC/CI 控制)
brew install m1ddc

# 2. 验证显示器支持 DDC/CI
m1ddc display list
m1ddc display 1 get luminance   # 应返回 0-100 的亮度值

# 3. 克隆项目
git clone https://github.com/zklovekfc/solar-brightness.git
cd solar-brightness

# 4. 安装依赖
pip3 install pyyaml

# 5. 一键安装 (创建配置 + 注册定时任务)
python3 solar-brightness.py --install
```

### 使用

```bash
# 查看状态
python3 solar-brightness.py --status

# 手动执行一次
python3 solar-brightness.py --once

# 暂停自动调节
launchctl unload ~/Library/LaunchAgents/com.solar-brightness.plist

# 恢复自动调节
launchctl load ~/Library/LaunchAgents/com.solar-brightness.plist

# 查看实时日志
tail -f ~/.config/solar-brightness/solar-brightness.log

# 卸载
python3 solar-brightness.py --uninstall
```

---

## ⚙️ 配置

配置文件位于 `~/.config/solar-brightness/config.yaml`，首次运行 `--install` 会自动生成。

```yaml
global:
  brightness:
    night_min: 35        # 深夜最低亮度
    day_max: 100         # 晴天正午最高亮度
    curve_power: 0.7     # 曲线形状: <1 黄昏更平滑
    weather_effect: 0.45 # 天气影响强度: 0=不管天气, 1=完全跟随

  transition:
    max_step_up: 5       # 每次最多升高 5%
    max_step_down: 3     # 每次最多降低 3%

displays:
  "你的显示器名称":      # 运行 m1ddc display list 查看
    name: "桌面主屏"
    max_nits: 400
    min_pct: 10
    max_pct: 100
    offset: 0            # 微调: +5=偏亮, -5=偏暗
```

---

## 🧠 原理

```
┌──────────────┐    ┌─────────────┐    ┌──────────────┐
│  经纬度+时间  │───▶│ 太阳高度角   │───▶│ 基础亮度     │
│  (自动定位)   │    │ sin(θ)^0.7  │    │ [35-100%]   │
└──────────────┘    └─────────────┘    └──────┬───────┘
                                              │
┌──────────────┐                              │  ×
│ Open-Meteo   │───▶ 云量 + 天气码 ──────────┤
│ (免费 API)   │                              │
└──────────────┘                              ▼
                                       ┌──────────────┐
┌──────────────┐                       │  计算亮度    │
│ 显示器硬件参数│───▶ min/max/offset ──▶│  (每台不同)  │
│ (多台独立)   │                       └──────┬───────┘
└──────────────┘                              │
                                       ┌──────▼───────┐
                                       │  渐变步长限制 │
                                       │  (±3~5%/次)  │
                                       └──────┬───────┘
                                              │
                                       ┌──────▼───────┐
                                       │  m1ddc       │
                                       │  DDC/CI 写入 │
                                       └──────────────┘
```

### 亮度曲线示意

```
100% ┤          ▄▄▄▄████████
     │       ▄▄▄            ████
 70% ┤    ▄▄▄                  ████
     │  ▄▄                        ██
 40% ┤▄▄                            ██
     ├───┬────┬────┬────┬────┬───
     │日出  9   12   15   18  日落
     
     ── 晴天 (云量 0%)     ─ ─ 阴天 (云量 80%)
```

---

## 🔧 依赖

| 工具 | 用途 | 安装 |
|------|------|------|
| [m1ddc](https://github.com/waydabber/m1ddc) | DDC/CI 显示器控制 | `brew install m1ddc` |
| Python 3.9+ | 脚本运行 | macOS 自带 |
| PyYAML | 配置文件解析 | `pip3 install pyyaml` |
| Open-Meteo | 天气数据 (免费，无需注册) | 自动调用 |
| ipip.net / ip-api.com | IP 自动定位 (免费) | 自动调用 |

---

## 🆚 对比

| 功能 | Lunar | MonitorControl | solar-screen-brightness | **solar-brightness** |
|------|:-----:|:----:|:--:|:--:|
| DDC/CI | ✅ | ✅ | ✅ | ✅ |
| Apple Silicon | ✅ | ✅ | ✅ | ✅ (M5 验证) |
| 太阳高度角 | ❌ | ❌ | ✅ | ✅ |
| 天气修正 | ❌ | ❌ | ❌ | ✅ **独有** |
| 多显示器独立参数 | ✅ | ✅ | ❌ | ✅ |
| 纯 CLI / 无 GUI | ❌ | ❌ | ✅ | ✅ |
| 离线可用 | ❌ | ✅ | ✅ | ✅ (有离线城市表) |
| 国内网络友好 | ❌ | ✅ | ✅ | ✅ (ipip.net 直连) |

---

## 📁 项目结构

```
solar-brightness/
├── solar-brightness.py    # 主脚本
├── install.sh             # 一键安装脚本
├── .claude/skills/        # Claude Code 技能定义
├── .github/workflows/     # CI 检查
├── LICENSE                # MIT
├── CHANGELOG.md
└── README.md
```

---

## 🤝 贡献

欢迎 PR！请先看 [CONTRIBUTING.md](CONTRIBUTING.md)。

如你的城市不在内置坐标表中，请提 issue 或 PR 添加。

---

## 📄 License

MIT © 2026 [zklovekfc](https://github.com/zklovekfc)

---

## 🙏 致谢

- [m1ddc](https://github.com/waydabber/m1ddc) — Apple Silicon DDC/CI 支持
- [Open-Meteo](https://open-meteo.com) — 免费天气 API
- Spencer (1971) — 太阳位置算法

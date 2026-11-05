# WaveSense（MVP）

单房间毫米波人体存在检测：DEV202（ICL1122，板内已做 DSP/滤波/CFAR）授权软件导出/回放数据
→ Python 规则判定（能量阈值 + 连续帧去抖 + 静坐判定）→ 本地 MQTT → Home Assistant
（灯控自动化、Logbook、仪表盘）。**全本地、不接云、不训 ML、不写 Web 后端。**

## 目录结构

```
vibe/
├─ config/
│  └─ config.yaml          # 所有可调参数（阈值、去抖帧数、MQTT、延时、模拟参数），占位值待实测
├─ src/
│  ├─ __init__.py
│  ├─ config.py             # 读 config.yaml：load_config()，带默认值和缺项提示
│  ├─ data_source/
│  │  ├─ frame.py          # 统一帧结构 Frame（dataclass）
│  │  ├─ base.py           # 抽象基类 DataSource（iter_frames）
│  │  ├─ simulated.py      # SimulatedSource：合成 empty/walk/sit 场景（A2 已实现）
│  │  ├─ replay.py         # FileReplaySource 占位（B1/B2 拿到导出格式后实现）
│  │  └─ serial_source.py  # SerialSource 占位（B2 按用户手册实现）
│  ├─ detector/
│  │  └─ detector.py      # Detector：运动去抖 + 静坐保持（A3/A3b 已实现）
│  ├─ mqtt_publisher.py      # 发布 presence / people_count（A4 已实现，自动重连）
│  └─ main.py               # 入口：数据源 → 判定 → 发布，A2 接通 simulated
├─ samples/                # 授权软件导出样本（B1 已放 6 个 DAT：empty/sit/walk 各 2）
├─ analyze_samples.py       # B1 离线样本统计：分组统计 + 三类对比图 + 阈值建议报告
├─ tests/
│  └─ test_detector.py     # Detector 单元测试（A3/A3b）
├─ tools/
│  └─ subscribe.py         # 不依赖 mosquitto_sub 的订阅验证小工具（A4）
├─ deploy/                # A5：旧笔记本本地部署（docker-compose + Mosquitto + HA 片段）
├─ docs/
│  ├─ analysis/           # B1 统计报告与对比图（自动生成）
│  ├─ manual/              # 授权软件手册与格式摸底资料
│  └─ DEV202-参数摸底记录表1.md  # B0 参数记录
├─ requirements.txt
└─ README.md
```

## 自检命令（在项目根目录执行）

### A1 骨架自检

```powershell
# 1. 安装依赖（pip 不在 PATH 时用 python -m pip）
python -m pip install -r requirements.txt

# 2. 配置自检：打印加载到的参数，缺项会有提示
python -m src.config

# 3. 入口自检：--help 能列出参数；不带参数运行不报错
python -m src.main --help
```

### A2 模拟数据源验证

```powershell
# 默认走配置里的 sim_scene（默认 walk），打印每帧能量概要和 targets
python -m src.main --source simulated

# 快速验证三个场景（--frames 覆盖帧数，避免等满 60 帧）
python -m src.main --source simulated --scene empty --frames 3
python -m src.main --source simulated --scene walk  --frames 3
python -m src.main --source simulated --scene sit   --frames 3
```

预期：empty 均值 ~0.15、targets=0；walk 峰值 2+ 且目标门移动、targets=1(moving)；
sit 固定门 6、峰值稳定 ~0.7、targets=1(static)。

### A3 判定逻辑验证

```powershell
# 单元测试（9 个用例：运动进/出/短链不翻/人数夹取、changed 仅翻转帧、静坐保持/空房不误判/抖动不触发/10分钟序列）
python -m unittest discover -s tests -v

# 用合成序列看 presence 翻转
python -m src.main --source simulated --scene walk  --frames 20
python -m src.main --source simulated --scene sit   --frames 120
python -m src.main --source simulated --scene empty --frames 10
```

合并规则：最终 presence = 运动有人 OR 静坐有人；静坐期间（窗口方差小 + 某门持续占用）
不得翻成 0。占位参数下，sit 在窗口填满后（约第 90 帧）由静坐路径保持为 1，empty 全程为 0。

### A4 MQTT 发布验证

本机已通过 winget 装好 Eclipse Mosquitto（Windows 服务，监听 127.0.0.1:1883，匿名本地连接）。
另开一个终端订阅，二选一：

```powershell
# 方式一：装了 mosquitto 命令行（默认路径）
& "C:\Program Files\mosquitto\mosquitto_sub.exe" -h 127.0.0.1 -p 1883 -t "wavesense/#" -v

# 方式二：不用命令行，用项目自带 Python 订阅小工具
python tools/subscribe.py
```

再跑发布（presence 只在翻转时发，people_count 变化才发）：

```powershell
python -m src.main --source simulated --scene walk --frames 30 --mqtt
```

订阅端应收到：
`wavesense/room1/people_count {"people_count":1,"timestamp":...}`
`wavesense/room1/presence  {"presence":1,"timestamp":...}`

没有 broker 时加 `--mqtt` 也不会崩溃：只打印警告，本地判定和打印照常。

### B1 离线样本分析（不接串口、不发 MQTT）

```powershell
# 解析 samples/*.dat，按 empty/sit/walk 分组统计每门均值/方差，出对比图和阈值建议报告
python analyze_samples.py
```

产物（自动生成在 `docs/analysis/`）：

- `gate_energy_comparison.png`：三类距离门能量对比图（去底噪 dB + 原始功率对数轴）；
- `B1-样本统计与阈值建议.md`：分组统计表、分位数分布、阈值建议与分离度诊断。

**B1 实测结论（先看报告第 9 节）**：当前样本在“平均距离门能量”口径下，走动与空房尚未拉开差距，
`energy_threshold` 暂不填、继续用占位值；静坐稳定性方差给出了参考上限。等 B2 接通回放链路、
用软件悬停数值复核解码口径后再定稿阈值。

## 配置说明

所有阈值、去抖帧数、延时都在 `config/config.yaml`，**判定相关参数当前仍是占位值，未经上机标定**；
B1 离线统计已给出静坐方差参考值，但运动阈值要等 B2 回放链路复核解码口径后再填，不要把阈值硬编码进代码。
`sim_*` 开头的是 A2 合成演示参数，只用于无硬件开发，不进正式判定。

## 常见问题

**报 `ModuleNotFoundError: No module named 'yaml'`（或 paho）**：机器上有多个 Python，
装包的解释器和跑脚本的不是同一个。规则：**用哪个 Python 跑，就用它装包**。

```powershell
# 先确认当前用的解释器
python -c "import sys; print(sys.executable)"
# 用同一个解释器装依赖（不要直接用 pip，避免装到别处）
python -m pip install -r requirements.txt
```

若用 `py` 启动器或 VS Code 选了解释器，就对那个解释器单独装一遍，例如：
`py -m pip install -r requirements.txt`。

## 任务路线

见同目录 `WaveSense-vibe-coding任务序列.md`：A1→A5 无硬件先行，B0→B4 硬件在场，
每条任务过验证 Gate 再走下一条。

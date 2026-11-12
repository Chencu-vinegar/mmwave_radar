# WaveSense

大学课程「计算机编程」MVP：用矽典微 DEV202（1T2R，ICL1122）24GHz 毫米波雷达
做单房间人体存在检测与灯控联动，全本地运行（不接云、不训 ML、不做原生 App）。

## 数据链路

```
DEV202 雷达 → USB 转 TTL 串口模组 → 电脑
   → 授权软件（1DFFT / 2DFFT，导出 / 回放 .gz）
   → Python 判定（存在 + 动作分级）
   → MQTT（本地 Mosquitto）
   → Home Assistant（灯控 / Logbook / 仪表盘）
```

## 硬件连接

见图 `docs/硬件连接方式.jpg`：DEV202 雷达（黄色小板）用 4 根杜邦线
（红 VCC、黑 GND、白/黄 TXD/RXD）接 USB 转 TTL 串口模组（蓝色小板），
串口模组插电脑 USB。

## 二进制帧格式（已实测钉死）

- 子帧 52 字节：`ICLH` 头 + uint32 长度字段（低 16 位=40，最高字节=通道 0/1）
  + 40 字节（4 字节计数器 + 18 个 int16 小端）+ `ICLT` 尾。
- 逻辑帧 104 字节 = ch0 + ch1 成对（1T2R 两接收通道）。
- 18 个 int16：位置 0–5 距离门（静态反射）；位置 6–15 动作/速度门（微动/多普勒）；
  16 参考；17 常量。控制帧 16 字节，跳过。

## 判定原理（稀疏尖峰 + 长保持）

直接串口时，静坐的人距离门短窗不是连续高值，而是"周期性稀疏尖峰"：
呼吸/身体调整每隔几秒到二十几秒触发一次尖峰（约 3–5e9），
尖峰之间与空房无法区分（人在弱反射/角度盲区，能量可能更低）。

| 场景 | 距离门短窗 | 动作区短窗 |
|---|---|---|
| 空房 | 底噪约 5–9e8（偶发单个尖峰） | ~5e7 |
| 静坐（呼吸） | 周期尖峰 3–5e9，间隔最长约 22s | ~6e7 |
| 小动 | — | ~1.5e9 |
| 大动 | — | ~4.3e9 |

- 进入：距离门短窗 > 1.8e9，连续 10 帧（人进门/坐下有动作，空房偶发尖峰不连续）。
- 保持：静坐的周期呼吸尖峰不断重置离开计数。
- 离开：连续约 25 秒（12000 帧）无活动尖峰才判无人。
- 近门电器过滤：尖峰必须有 ≥8% 的能量传播到远门（门4-5）。近门电器（饮水机/风扇）
  反射只在近门、远门比例 <8%；人呼吸的多径反射传播到所有门、远门比例通常 ≥10%。
- 动作分级：只看动作区短窗——静坐呼吸判"不动"，小动约 4e8–2.6e9，大动 ≥2.6e9。
- 走动保持：窄房间来回走动时，转身会进入角度盲区、当下动作门短暂回落（约 5–6s）。
  用约 7 秒记忆窗口统计"强动作帧数"（动作门 ≥2.0e9），走动的强动作成簇出现（≥20 帧），
  纯静坐恒为 0；因此走动全程判"大动"（98–100%），纯静坐不误判（0 大动）。
- 实测真正空房（人离开后）60–85s 内 0 误报。

### 基线自适应（相对抬升判据）

固定绝对阈值在不同房间/空调档位的底噪水平下容易两头不讨好：底噪高的环境误报、
底噪低的环境漏检。判定器支持先采一段空房底噪、得到距离门/动作区的 p95 基线，
活动判据在绝对阈值之上再叠加一条"相对基线抬升"条件：

```
有效尖峰 = 距离门短窗 > 绝对阈值
         且 距离门短窗 > 基线p95 × 基线倍数（默认 1.8）
         且 远门比例 ≥ 8%
```

- 基线未学习完成时，相对条件自动退化为不约束（保持旧版行为），不影响默认使用。
- 网页「判定依据」卡片可查看当前距离门/动作门/远门比例/长窗抬升/基线状态；
  点「学习空房基线」按钮，保持房间无人约 30 秒，采集满后自动固化并保存到
  `web_data/baseline.json`，重启后自动复用。
- 配置项见 `config.yaml` 的 `detector.baseline`（learn_frames / ratio / long_window）。

- 注：电器压缩机/水泵**启动瞬间**（0.3 秒、距离门和动作门同时冲高）与人的动作
  无法区分，会短暂保持有人、随后正常离开；录制空房基线时最好物理关闭空调/饮水机。

## 目录结构

```
wavesense/
  config/config.yaml        # 阈值 / 窗口 / 去抖 / MQTT（真实标定值）
  src/
    config.py               # 读 config.yaml（缺项回退默认值）
    data_source/
      frame.py              # Frame + range_energy()/motion_energy()
      base.py               # DataSource 抽象
      stream_parser.py      # 流式二进制帧解析（粘包/分包）
      file_replay.py        # FileReplaySource：解析 .gz / .dat
      serial_source.py      # SerialSource：实时串口 + 自动列串口
      simulated.py          # SimulatedSource：合成场景（无硬件）
    detector/detector.py    # Detector：状态机 + 动作分级
    mqtt_publisher.py       # MqttPublisher（分 topic，broker 不在时降级）
    web/
      engine.py             # MonitorEngine：后台采集、运行时切换数据源、整段分析统计
      server.py             # 标准库 HTTP：状态/事件/CSV/串口列表/实时切换/文件上传接口
      static/index.html     # 双模式前端（实时输入 / 数据导入 + 分析报告）
    main.py                 # 入口
  web_data/                 # 运行数据：events.json 事件；baseline.json 空房基线；uploads/ 上传文件
  dataset/                  # 切分后的纯数据集（empty/rest/small/large）+ manifest
  deploy/                   # A5：docker-compose + Mosquitto + HA 配置
  tools/                    # segment 切分 / build_dataset 构建 / 批量回放 / 分析 / smoke / regression 回归
  tests/test_detector.py    # 单元测试
  docs/硬件连接方式.jpg
```

## 快速开始

```bash
cd wavesense
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 实时串口（连接板子即用）
# 1) 按 docs/硬件连接方式.jpg 接好：雷达→USB转串口→电脑
# 2) 运行（不带 --port 会自动列出/选择串口）
PYTHONPATH=. python -m src.main --source serial
# 也可显式指定串口和波特率：
#   --port /dev/tty.usbserial-xxx --baud 921600   （Windows 串口为 COMx）

# 合成剧情（无硬件）：空房→进门→静坐→小动→大动→离开
PYTHONPATH=. python -m src.main --source simulated --scenario demo
# scenario 也可选 empty / sit / small / large

# 回放一个录制文件（.gz）
PYTHONPATH=. python -m src.main --source replay --file /path/to/xxx.gz
# 加 --mqtt 同时发布到 broker（需先起 deploy，见下）

# 一键冒烟（合成场景 + 真实解析）
PYTHONPATH=. python tools/smoke.py

# 批量回放并统计
PYTHONPATH=. python tools/replay_all.py

# 单元测试
PYTHONPATH=. python -m unittest tests.test_detector -v

# 本地网页（演示/记录，无需 Home Assistant，浏览器打开即可）
# 最简单：双击启动网页（macOS 双击 启动网页.command / Windows 双击 启动网页.bat）
#   → 自动检测雷达串口，有就实时接，没有就自动跑内置演示，自动开浏览器
# 页面内自带「实时输入 / 数据导入」双模式切换，无需重启换命令
PYTHONPATH=. python -m src.main --web                                        # 默认 auto：自动选串口/演示
PYTHONPATH=. python -m src.main --web --source simulated --scenario demo   # 无硬件先看效果（合成剧情）
PYTHONPATH=. python -m src.main --web --source replay --file /path/xxx.gz  # 启动即回放录制
PYTHONPATH=. python -m src.main --web --source serial                       # 接真实雷达实时采集
# 默认端口 8080，浏览器打开 http://localhost:8080；可用 --http-port 改端口
# 手机和电脑在同一局域网时，用电脑的局域网 IP（如 http://192.168.x.x:8080）访问
#
# 页面双模式：
#  · 实时输入：选择串口 → 连接并开始，实时接收雷达数据并即时反馈（有人/动作/曲线）
#  · 数据导入：选择本地 .gz（gzip 录制）或 .dat（原始二进制）→ 上传并分析，
#    自动限速回放整段文件，结束后在「数据分析报告」卡片给出：总帧数、时长、
#    有人占比、不动/小动/大动分布、进入/离开次数、最终状态
# 上传的文件保存到 web_data/uploads/；事件保存在 web_data/events.json


# 起本地 Mosquitto + Home Assistant（需 Docker，见 deploy/README.md）
cd deploy && docker compose up -d
```

### 实时串口注意

- **波特率**默认 921600（数据约 50KB/s，需要 ≥460800；若乱码/丢帧，按手册改 `--baud`）。
- **冷启动**：程序启动时若人已经在座位上不动，可能检测不到，做个挥手/起身动作即可识别。
- 屏幕底部每 0.25 秒刷新一行当前状态；存在翻转、动作变化会单独换行打印。

## 已确认的工程约束

- 雷达安装位置/角度是成败关键：人常坐/常躺位置必须落在主波束内；
  正前方静坐检测好，电脑旁侧身/后仰/被遮挡可能处于盲区（算法无法弥补物理"看不到"）。
- 近距离（50cm）动作时距离门、动作区会削顶饱和，可在授权软件调低灵敏度。
- 空房中的饮水机等静止设备不产生干扰；风扇/空调等周期性转动需现场确认。
- 现有固件打包帧（每帧 6 距离门 + 10 动作门）下，能量大小、远门比例、门分布、
  长短窗、自相关/周期峰、过零率、尖峰密度等轻量特征都无法把"空调空房"和"静坐人体"
  分开；真正可分的信息需要原始距离-多普勒谱（RDM），当前打包格式未暴露。空调强风
  误报在现有条件下只能靠基线自适应（相对抬升）+ 现场物理关闭强干扰源缓解。
- 远距离（约 1.5–2m）静坐的呼吸尖峰幅度低、间隔长，进入门槛不易攒够，存在召回
  天花板；这是弱反射 + 打包信息受限的物理限制，放宽进入门槛会同步抬高空房误报。

## 待办

- [x] 真实数据解析、判定器、合成数据源、回放验证、单元测试
- [x] MQTT 分 topic 发布 + HA/Mosquitto 部署配置
- [x] 实时串口接入（SerialSource）
- [x] 走动保持：转身盲区不回落，来回走动全程判大动
- [x] 本地网页（--web）：实时/回放双模式切换、串口实时接入、.gz/.dat 上传回放与整段分析报告、状态、动作分级、能量曲线、事件记录、清空与 CSV 导出
- [x] 基线自适应：空房底噪 p95 学习 + 相对抬升判据，网页可学习/查看基线
- [x] 可解释判定面板：实时展示距离门/动作门/远门比例/长窗抬升/基线状态
- [x] 批量回归脚本：samples 标注段 + dataset 纯段，统计空房误报/静坐召回/大动判出
- [ ] 现场连线验证串口实时判定；`docker compose up` 起服务、接真实灯联调
- [ ] 验收长场景：空房 1h、静坐 30min、双人计数、进门/离开（Gate 2–5）

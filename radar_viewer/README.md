# mmwave_radar — 毫米波雷达数据解析器

> **⚠️ 项目状态：初版（v0.1）**
>
> 这是第一版的数据读取与收集软件，定位为**早期开发 / 验证版本**。目前以数据读取、解析与可视化收集为主，接口与功能仍在持续迭代中，可能存在不完善之处，请勿直接用于生产环境。

针对矽典微 **DEV202H（ICL1122）** 毫米波雷达的离线/实时数据解析与可视化桌面应用。

- 离线解析 SDK 保存的 `.dat` 数据文件，并自动读取配套 `FILE_Radar_Config.ini`
- USB 串口实时读取、解析并持续刷新可视化（默认 921600 波特率）
- 支持 BEV 俯视热图、距离-角度、距离-时间、最新单帧散点、3D 点云
- 支持导出点云 CSV、热图 PNG + 同名 CSV 数值矩阵、录制原始 `.dat`

## 界面预览

两个主要标签页：**文件解析**（选择 `.dat` 一次性解析并回放）与 **串口实时**（连接 USB 串口实时刷新）。

## 快速开始

### 直接运行 exe

下载 `dist/radar_viewer.exe`，双击运行即可（无需安装 Python）。

### 源码运行

```bash
pip install numpy matplotlib pyserial
python radar_app.py
```

## 使用说明

### 文件解析
1. 选择 `.dat` 数据文件（`FILE_Radar_Config.ini` 放同目录会自动识别，或手动指定）
2. 点击「解析」，即可看到 BEV / 距离-角度 / 距离-时间 / 单帧散点
3. 可回放指定帧、缩放距离、镜像、导出 CSV / 热图

### 串口实时
1. 切换到「串口实时」标签页，选择串口（默认 COM5）与波特率（默认 921600）
2. 指定配置文件后点击「连接」
3. 实时刷新四张图，可「录制」原始数据为 `.dat`、导出当前数据

## 物理模型（DEV202H）

| 参数 | 值 |
|------|----|
| 中心频率 fc | 24.125 GHz |
| 带宽 B | 220 MHz |
| 距离分辨率 dR | ≈ 0.682 m |
| 天线间距 d_ant | 6.25 mm |
| 距离 bin 数 | 8 |
| chirp / 帧 | 64 |

角度通过 1T2R 的相位差计算：`θ = arcsin(Δφ / (2π·d_ant/λ))`，其中 `Δφ = angle(RX1·conj(RX0))`。

## 数据格式

`.dat` 与串口实时流均为 `ICLH` 帧封装的连续字节流：

- **数据帧 52B**：`ICLH` | len(40) | 0 | 0 | 通道(0=RX0/1=RX1) | 子头(4B) | 8 个距离 bin 复数(32B, 大端 int16 I/Q) | 附加 | `ICLT`
- **短帧 16B**：`ICLH` | len(4) | ... 分隔帧，跳过

counter 每 chirp +16，每 64 chirp 回绕为 1 个雷达帧；同 counter 的 RX0/RX1 配对成完整 chirp。

## 构建 exe

```bash
pyinstaller --noconfirm --clean --onefile --windowed \
  --name radar_viewer \
  --hidden-import matplotlib.backends.backend_tkagg \
  --hidden-import serial --hidden-import serial.tools.list_ports \
  --hidden-import serial.serialwin32 \
  --hidden-import mpl_toolkits.mplot3d \
  --collect-data matplotlib \
  radar_app.py
```

或直接运行 `build.bat`。

## 目录结构

```
mmwave_radar/
└── radar_viewer/            # 本项目（毫米波雷达解析器）
    ├── radar_app.py         # GUI 主程序（Tkinter + Matplotlib）
    ├── radar_parser.py      # .dat 解析核心（帧解析、相位 AoA、点云构建）
    ├── serial_live.py       # 串口实时读取与增量解析
    ├── build.bat            # PyInstaller 打包脚本
    ├── radar_viewer.spec    # PyInstaller spec
    └── dist/
        └── radar_viewer.exe # 打包成品（免安装）
```

## 依赖

- Python 3.14
- numpy
- matplotlib（TkAgg 后端）
- pyserial
- PyInstaller（仅构建需要）

## 许可

本项目仅用于技术学习与验证。数据格式为对 DEV202H 设备输出逆向所得，不保证与官方协议完全一致。

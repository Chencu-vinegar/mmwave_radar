# -*- coding: utf-8 -*-
"""config.py —— 读取 config/config.yaml，提供带默认值的配置加载器。

设计原则：
- 所有可调参数集中在 config.yaml，代码里不硬编码阈值；
- 配置文件缺项时用 DEFAULT_CONFIG 兜底，并打印缺项提示，方便逐项补全；
- 只做“读配置”这一件事，不碰网络、不碰串口。
"""

from pathlib import Path

import yaml

# 项目根目录：本文件在 src/ 下，往上两级即根目录
ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT_DIR / "config" / "config.yaml"

# 默认占位值（与 config.yaml 保持一致；文件缺项或缺失时兜底用）
DEFAULT_CONFIG = {
    # 运动存在判定
    "energy_threshold": 3.0,
    "noise_floor": 0.2,
    "debounce_frames_enter": 3,
    "debounce_frames_leave": 8,
    # 静坐判定（A3b）
    "still_energy_variance": 0.05,
    "still_persist_frames": 60,
    "still_window_frames": 90,
    "still_gate_min_energy": 0.5,
    # MQTT
    "mqtt_host": "127.0.0.1",
    "mqtt_port": 1883,
    "mqtt_topic_presence": "wavesense/room1/presence",
    "mqtt_topic_people_count": "wavesense/room1/people_count",
    # HA 灯控
    "light_off_delay_sec": 300,
    # 模拟数据源（A2，B0 实测门数 8、fps 10）
    "sim_scene": "walk",
    "sim_frame_rate": 10.0,
    "sim_num_gates": 8,
    "sim_frames": 60,
    # 串口（B2，B0 实测值先记录）
    "serial_port": "COM5",
    "serial_baud": 921600,
}


def load_config(path: str | Path | None = None) -> dict:
    """加载 YAML 配置。

    参数:
        path: 配置文件路径；不传则用默认位置 config/config.yaml。
    返回:
        dict：合并后的配置（YAML 里有的覆盖默认值，缺项补默认值并提示）。
    """
    config = dict(DEFAULT_CONFIG)  # 先拷贝默认值，保证任何缺项都有兜底

    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        print(f"[config] 警告：未找到配置文件 {config_path}，全部使用默认占位值。")
        return config

    with open(config_path, "r", encoding="utf-8") as f:
        user_cfg = yaml.safe_load(f) or {}

    # 逐键合并：YAML 里有的覆盖默认，缺项补默认并打印提示
    for key, default_value in DEFAULT_CONFIG.items():
        if key not in user_cfg:
            print(f"[config] 提示：配置缺项 {key}，使用默认值 {default_value}。")
            user_cfg[key] = default_value

    return user_cfg


# 直接运行 `python -m src.config` 时，打印加载结果用于自检
if __name__ == "__main__":
    cfg = load_config()
    print("[config] 已加载配置：")
    for key, value in cfg.items():
        print(f"  {key} = {value}")

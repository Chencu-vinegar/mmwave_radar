"""配置加载模块。

从 config/config.yaml 读取参数；文件缺失或字段缺失时回退到默认值，
并给出中文提示，避免程序静默使用错误配置。
"""
import sys
from pathlib import Path

import yaml

# 项目根目录 = 本文件上级的上级（wavesense/）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

# 默认值：与 config/config.yaml 保持一致，字段缺失时兜底
DEFAULTS = {
    "detector": {
        "short_window": 30,
        "long_window": 300,
        "enter_threshold": 3.0e8,
        "enter_frames": 5,
        "range_still_threshold": 1.0e9,
        "leave_frames": 100,
        "enter_grace_frames": 300,
        "small_move_threshold": 1.0e9,
        "large_move_threshold": 2.5e9,
    },
    "mqtt": {"host": "localhost", "port": 1883, "topic": "wavesense/room1"},
    "light_off_delay_sec": 300,
}


def load_config(path=None):
    """加载配置字典。

    规则：文件里已有的键用文件值，缺的键用默认值并打印提示；
    整个文件缺失则全部用默认值并警告。绝不静默失败。
    """
    cfg_path = Path(path) if path else CONFIG_PATH
    cfg = {}

    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        for key, default in DEFAULTS.items():
            if isinstance(default, dict):
                # 嵌套结构（mqtt）：文件子键 + 默认子键合并
                cfg[key] = {**default, **(loaded.get(key) or {})}
            else:
                cfg[key] = loaded.get(key, default)
        print(f"[config] 已加载配置: {cfg_path}")
    else:
        cfg = DEFAULTS
        print(f"[config] 警告: {cfg_path} 不存在，使用默认值", file=sys.stderr)

    return cfg

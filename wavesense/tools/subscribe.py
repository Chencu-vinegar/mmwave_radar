# -*- coding: utf-8 -*-
"""subscribe.py —— 不依赖 mosquitto_sub 的本地订阅验证小工具（A4）。

没有装 Mosquitto 命令行时，用它接收消息做验证。
用法（项目根目录，另开一个终端）：
    python tools/subscribe.py
或：
    python -m tools.subscribe

前提：本机 1883 端口已有本地 broker（Mosquitto / Docker 均可）。
"""

import json
import sys
from pathlib import Path

# 把项目根目录加进路径，以便 import src 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import paho.mqtt.client as mqtt

from src.config import load_config


def main() -> None:
    cfg = load_config()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    def on_message(client, userdata, msg):
        """收到消息：尝试按 JSON 解析，失败就原样打印。"""
        try:
            payload = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = msg.payload.decode(errors="replace")
        print(f"{msg.topic} -> {payload}")

    client.on_message = on_message
    client.connect(cfg["mqtt_host"], cfg["mqtt_port"], keepalive=60)
    client.subscribe("wavesense/#")
    print(f"订阅中 {cfg['mqtt_host']}:{cfg['mqtt_port']} 主题 wavesense/#，Ctrl+C 退出")
    client.loop_forever()


if __name__ == "__main__":
    main()

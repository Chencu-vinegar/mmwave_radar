# -*- coding: utf-8 -*-
"""mqtt_publisher.py —— 向本地 Mosquitto 发布 presence / people_count（A4）。

设计要点：
- 只连本地 broker（config 里的 mqtt_host/port），不接任何云；
- paho 后台线程 loop + connect_async，断线自动重连；
- presence 只在 changed 时由调用方触发发布，避免刷屏；
- people_count 内部按“变化才发”发布；
- 连不上只打印警告，不让程序崩溃（判定和打印照常进行）。
"""

import json
import time

import paho.mqtt.client as mqtt


class MqttPublisher:
    """MQTT 发布器。"""

    def __init__(self, config: dict):
        self.host = config["mqtt_host"]
        self.port = int(config["mqtt_port"])
        self.topic_presence = config["mqtt_topic_presence"]
        self.topic_people = config["mqtt_topic_people_count"]

        self.client: mqtt.Client | None = None
        self._connected = False
        self._last_people_count = None  # people_count 变化才发的缓存

    def connect(self) -> None:
        """连接 broker（异步 + 自动重连）；失败只警告，不抛异常。"""
        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            # 断线重连退避：1s 起步，最长 120s
            self.client.reconnect_delay_set(min_delay=1, max_delay=120)
            self.client.connect_async(self.host, self.port, keepalive=60)
            self.client.loop_start()  # 后台线程维持连接/重连
        except Exception as e:
            # 连不上不崩溃：后面发布直接跳过，本地判定照常
            print(f"[mqtt] 警告：初始化连接失败 {self.host}:{self.port}，{e}（继续本地运行）")
            self.client = None

    def publish_presence(self, presence: int) -> None:
        """发布 presence(0/1)。只在 changed 时由 main 调用，不在此内部去重。"""
        if not self._ready():
            return
        payload = {
            "presence": presence,
            "timestamp": round(time.time(), 3),  # 发布时刻（epoch 秒）
        }
        self.client.publish(self.topic_presence, json.dumps(payload), qos=0)

    def publish_people_count(self, people_count: int | None) -> None:
        """发布人数；内部按“变化才发”，避免每帧刷屏。None 表示目标数未知，照常发 null。"""
        # 先确认连接再缓存去重值，避免连接前的取值把去重“喂饱”，上线后永不发布
        if not self._ready():
            return
        if people_count == self._last_people_count:
            return
        self._last_people_count = people_count
        payload = {
            "people_count": people_count,
            "timestamp": round(time.time(), 3),
        }
        self.client.publish(self.topic_people, json.dumps(payload), qos=0)

    def disconnect(self) -> None:
        """优雅断开：停后台线程并断开连接。"""
        if self.client is None:
            return
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception as e:
            print(f"[mqtt] 断开时警告：{e}")

    # ---------- 内部回调 ----------

    def _ready(self) -> bool:
        """连接就绪才发布；未就绪静默跳过（连接失败的警告已在 connect 给出）。"""
        return self.client is not None and self._connected

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            self._connected = True
            print(f"[mqtt] 已连接 broker {self.host}:{self.port}")
        else:
            self._connected = False
            print(f"[mqtt] 连接被拒绝 reason_code={reason_code}，将自动重试")

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties=None):
        self._connected = False
        print("[mqtt] 连接断开，paho 将按退避策略自动重连")

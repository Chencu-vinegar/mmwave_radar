"""MQTT 发布模块（Home Assistant 友好）。

只在 presence 翻转时发布，避免刷屏：
  {topic}/presence      载荷 "1"/"0"，供 HA MQTT binary_sensor（payload_on/off）
  {topic}/people_count  载荷整数；当前等于 presence（单人 MVP）
  {topic}/state         完整 JSON（动作分级 / 各特征值，调试 / 仪表盘用）
broker 不可用时打印中文提示并降级，不影响主流程。

注：DEV202 导出的 18 个 int16 中无可靠目标列表，双人计数本期不做（标 TODO），
people_count 随 presence 取 0/1。
"""
import json
import time

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover
    mqtt = None


class MqttPublisher:
    def __init__(self, mqtt_cfg):
        base = mqtt_cfg.get("topic", "wavesense/room1")
        self.presence_topic = base + "/presence"
        self.count_topic = base + "/people_count"
        self.state_topic = base + "/state"
        self.last_presence = None
        self.client = None
        if mqtt is None:
            print("[mqtt] 未安装 paho-mqtt，跳过发布")
            return
        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            self.client.connect(mqtt_cfg.get("host", "localhost"),
                                int(mqtt_cfg.get("port", 1883)), keepalive=30)
            self.client.loop_start()
            print(f"[mqtt] 已连接 {mqtt_cfg['host']}:{mqtt_cfg['port']}")
        except Exception as exc:  # broker 未启动等
            print(f"[mqtt] 连接失败，跳过发布: {exc}")
            self.client = None

    def publish_state(self, detection):
        """presence 翻转时发布三个 topic；非翻转帧不发。"""
        if self.client is None:
            return
        if detection.presence == self.last_presence:
            return
        self.last_presence = detection.presence

        presence = 1 if detection.presence else 0
        data = detection.as_dict()
        data["timestamp"] = time.time()

        self.client.publish(self.presence_topic, str(presence), qos=1, retain=True)
        self.client.publish(self.count_topic, str(presence), qos=1, retain=True)
        self.client.publish(self.state_topic, json.dumps(data, ensure_ascii=False), qos=1)

    def disconnect(self):
        if self.client is not None:
            self.client.loop_stop()
            self.client.disconnect()

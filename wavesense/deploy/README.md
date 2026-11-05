# WaveSense 本地部署（A5）

一键起本地 Mosquitto + Home Assistant，全程局域网，不暴露公网。

## 目录

```
deploy/
  docker-compose.yml
  mosquitto/config/mosquitto.conf
  homeassistant/config/configuration.yaml
```

## 启动

```bash
cd deploy
docker compose up -d
docker compose ps          # 看到两个服务 running
```

- HA 网页：http://localhost:8123 （首次按提示建账号）
- MQTT broker：本机 1883 端口

## 手动验证 HA 灯控（不接雷达也能测）

```bash
# 模拟"有人"，HA 应判有人并"开灯"
docker exec wavesense-mosquitto mosquitto_pub -t wavesense/room1/presence -m "1"
# 模拟"离开"，HA 判无人并启动 5 分钟关灯计时
docker exec wavesense-mosquitto mosquitto_pub -t wavesense/room1/presence -m "0"
```

订阅查看：

```bash
docker exec -it wavesense-mosquitto mosquitto_sub -t 'wavesense/#' -v
```

## 让 Python 程序连这个 broker

默认 `config/config.yaml` 里 `mqtt.host: localhost`。若 Python 跑在**本机**（broker 也在本机），
直接加 `--mqtt`：

```bash
# 回放真实录制 + 发布
PYTHONPATH=. python -m src.main --source replay --file /path/to/xxx.gz --mqtt
# 或合成剧情 + 发布
PYTHONPATH=. python -m src.main --source simulated --mqtt
```

## 接入真实灯

1. 在 HA（设置 → 设备与服务）接入智能灯 / 智能插座，得到实体名，例如 `light.desk_lamp`。
2. 把 `configuration.yaml` 里三处 `light.placeholder_light` 全部替换成真实实体名。
3. 可删掉 `light:` 下的占位 template 段。重启 HA：`docker compose restart homeassistant`。

## 仪表盘建议（用原生卡片，不做自定义前端）

- 实体卡片（Entities）：`binary_sensor.room_presence`（房间有人）、`sensor.people_count`（人数）、灯实体。
- 历史卡片（History / Logbook）：看最近 24h 的进出记录。
- 计时器实体：`timer.light_off` 显示离开后关灯倒计时。

## 本期不做

- 双人计数（`people_count` 当前等于 presence，0/1）；
- 公网访问、云、原生 App、后端、ML。

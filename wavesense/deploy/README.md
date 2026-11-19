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

## 接入真实灯：飞利浦 WiZ 全彩灯泡

灯型：PHILIPS Connected by WiZ，WiFi（2.4GHz）+ 蓝牙，无网关。两种控制路线二选一，
均在本地局域网完成，配网一次后断外网也能开关/调光/调色。

### 控制路线（二选一）

**默认：pywizlight 本地直连（不经 HA / 云）**

- Python 进程内用 `pywizlight` 通过 UDP（端口 38899）直接控制灯泡：判有人开灯、判无人关灯。
- 启动网页后，在"灯控联动"卡片填灯泡 IP、点保存即可；IP 持久化到 `web_data/wiz.json`，
  下次启动自动载入。依赖已写入 `requirements.txt`（`pywizlight`）。
- 新固件返回 `-32602 Invalid params`：先在 WiZ App「设置 → 安全」允许本地控制；
  仍不行则导出家庭 JSON 取 `udp_signing_key`，填入 `config/config.yaml` 的 `wiz.signing_key`。
- 命令超时/无反应：确认电脑与灯泡同网段、同 2.4G，防火墙放行 UDP 38899，路由器未开 AP 隔离。

**可选：Home Assistant + WiZ 集成（下方 HA 步骤）**

需要 Docker 起 HA + Mosquitto，程序发 MQTT 由 HA 控灯；仅在需要 HA 仪表盘 / 多灯编排时使用。

### 1. 灯泡配网（手机 App，只做一次）

1. 灯泡拧入灯座通电，手机安装 WiZ App（iOS / Android）。
2. App 内按提示把灯泡配到家里 **2.4GHz** WiFi（灯泡不支持 5GHz，确认路由器 2.4G 已开）。
3. 配网后进 App：设置 → 安全设置，打开 **Allow local communication（允许本地通信）**，
   默认应为开启；这是 HA 能本地控灯的前提。

> 包装上的天猫精灵/小爱/小度语音绑定与本项目无关，HA 走本地 WiZ 协议，不需要绑这些平台。
> SpaceSense 是 WiZ 自家的多灯动作感知，和本项目毫米波雷达不是一套；单灯也用不了，可忽略。

### 2. HA 添加 WiZ 集成

1. HA 网页 → 设置 → 设备与服务 → 右下角 添加集成 → 搜索 **WiZ**。
2. 集成通常自动发现同局域网内的灯泡；发现不到就手动填灯泡的局域网 IP
   （IP 在路由器后台或 WiZ App 设备详情里查）。
3. 添加后在实体列表确认灯泡实体名，形如 `light.wiz_bulb_xxxx`。

### 3. 回填配置并重启

1. 打开 `homeassistant/config/configuration.yaml`，把自动化里两处
   `light.placeholder_light` 引用（开灯、关灯）替换成上面的真实实体名。
2. 确认灯控行为：有人开灯并取消关灯计时、无人启动 5 分钟延时关灯、到点关灯。
3. 不需要占位演示后，可删掉 `light:` 下的占位 template 段。
4. 重启 HA 生效：

```bash
cd deploy
docker compose restart homeassistant
```

### 4. 不接雷达先验证灯控链路

```bash
# 模拟有人 → 灯应亮
docker exec wavesense-mosquitto mosquitto_pub -t wavesense/room1/presence -m "1"
# 模拟离开 → 启动 5 分钟关灯计时，到点灯灭
docker exec wavesense-mosquitto mosquitto_pub -t wavesense/room1/presence -m "0"
```

### 常见问题

- **HA 发现不到灯泡**：确认 HA 容器与灯泡在同一局域网、2.4G 信号正常；compose 端口映射
  模式下 mDNS 发现可能受限，改用手动填 IP。
- **命令延迟或丢包**：WiFi 灯泡是独立网络客户端，距路由器过远/隔墙会有延迟，属网络问题，
  不是判定逻辑问题。
- **想当夜灯低亮度暖光**：在"有人_开灯"自动化的 `light.turn_on` 下取消注释
  `brightness_pct` / `color_temp` 那段即可。

## 仪表盘建议（用原生卡片，不做自定义前端）

- 实体卡片（Entities）：`binary_sensor.room_presence`（房间有人）、`sensor.people_count`（人数）、灯实体。
- 历史卡片（History / Logbook）：看最近 24h 的进出记录。
- 计时器实体：`timer.light_off` 显示离开后关灯倒计时。

## 本期不做

- 双人计数（`people_count` 当前等于 presence，0/1）；
- 公网访问、云、原生 App、后端、ML。

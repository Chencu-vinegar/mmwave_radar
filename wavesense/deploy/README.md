# A5 部署说明：Home Assistant + Mosquitto（全局域网，不暴露公网）

本目录是给**旧笔记本**目标机用的本地部署配置；开发机上已有的 Windows Mosquitto 不影响这里。

## 目录与粘贴位置

```
deploy/
├─ docker-compose.yml              # ① 编排文件：mosquitto + homeassistant
├─ mosquitto/
│  └─ mosquitto.conf               # ② broker 配置（匿名本地）
└─ ha/
   └─ configuration.yaml.snippet  # ③ HA 配置片段（合并进 HA 的 configuration.yaml）
```

| 文件 | 粘到哪里 |
|---|---|
| `docker-compose.yml` | 旧笔记本任意目录，如 `~/wavesense/docker-compose.yml` |
| `mosquitto/mosquitto.conf` | 同目录 `mosquitto/mosquitto.conf`（compose 已挂载） |
| `configuration.yaml.snippet` | 合并进 HA 容器 `/config/configuration.yaml`，**不要整体覆盖**原有内容 |

## 启动步骤（旧笔记本，装好 Docker Desktop / Docker Engine）

```bash
cd ~/wavesense
docker compose up -d
# HA 网页：http://笔记本局域网IP:8123 （首次进入引导创建管理员账号）
```

## 配置回填顺序（关键）

1. 先只粘贴 `mqtt:` 接入段和两个实体，重启 HA；
2. 到「设置 → 设备与服务 → 实体」查到 `binary_sensor` 的真实 entity_id；
3. 把自动化、Logbook 里所有 `binary_sensor.renti_cunzai`、`light.placeholder_light` 占位换成真实值；
4. 重启 HA 使自动化生效。

## 手动联调（不跑 Python 也能验证 HA 响应）

另开终端往 broker 发一条，看 HA 实体/灯是否按规则动：

```bash
mosquitto_pub -h 笔记本IP -t wavesense/room1/presence \
  -m '{"presence":1,"timestamp":0}'
```

## 仪表盘卡片建议（全用原生卡片，不写自定义前端）

| 需求 | 原生卡片 | 配置要点 |
|---|---|---|
| 人数 | 实体卡片（Entities） | 添加 `sensor.人数`，显示数值 |
| 存在状态 | 实体卡片（Entities） | 同卡加 `binary_sensor.人体存在`，显示 on/off |
| 灯状态/开关 | 灯卡片（Light） | 选真实灯实体，可开关、调亮度 |
| 最近 24h 进出 | 历史卡片（History graph） | 选 `binary_sensor.人体存在`，时长选 24h |
| 进出事件记录 | 日志卡片（Logbook） | 选存在传感器 + 灯，逐条显示 on/off 时间 |

布局建议：第一行「实体卡（存在+人数）+ 灯卡片」，第二行「历史卡片」，第三行「日志卡片」。

## 安全边界

- 只在局域网用：路由器上不要把 1883、8123 端口转发到公网；
- 本地匿名先图方便，以后加设备多了再给 Mosquitto 加账号密码；
- `light_off_delay_sec` 在 Python 侧 `config/config.yaml`，HA 自动化 B 的 300 秒是镜像占位，两边保持一致即可。

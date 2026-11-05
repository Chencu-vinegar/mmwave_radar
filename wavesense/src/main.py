"""WaveSense 入口。

实时串口（连接板子即用）：
    python -m src.main --source serial --port /dev/tty.usbserial-xxx
    不带 --port 会自动列出可用串口。--baud 默认 921600。

回放（真实录制）：
    python -m src.main --source replay --file path/to.gz
合成（无硬件）：
    python -m src.main --source simulated --scenario demo
加 --mqtt 可发布到 Home Assistant（需先起 broker）。
"""
import argparse
import os
import sys
import threading
import time
import webbrowser

from src.config import load_config
from src.data_source import (
    FileReplaySource,
    SerialSource,
    SimulatedSource,
    list_serial_ports,
)
from src.detector import Detector


def make_publisher(cfg, use_mqtt):
    if not use_mqtt:
        return None
    from src.mqtt_publisher import MqttPublisher
    return MqttPublisher(cfg["mqtt"])


def pick_real_serial_port():
    """过滤掉系统调试/蓝牙口，返回真正可用的串口设备名；没有则返回 None。"""
    try:
        ports = list_serial_ports()
    except Exception:
        return None
    for dev, _desc in ports:
        low = dev.lower()
        if any(k in low for k in ("debug-console", "bluetooth", "incoming-port")):
            continue
        return dev
    return None


def run_web(args, cfg):
    """本地网页：后台引擎采集 + 浏览器演示/记录。"""
    from src.web import MonitorEngine, WebServer

    if args.source == "replay":
        if not args.file:
            print("错误：replay 模式需要 --file", file=sys.stderr)
            sys.exit(1)
        source = FileReplaySource(args.file)
        pace = True
        mode = "replay"
        source_name = os.path.basename(args.file)
    elif args.source == "simulated":
        source = SimulatedSource(args.scenario, args.frames)
        pace = True
        mode = "replay"
        source_name = "合成·" + args.scenario
    else:
        port = args.port
        if not port:
            ports = list_serial_ports()
            if not ports:
                print("没找到串口：请确认 USB 转串口模组已插好、驱动已安装。")
                sys.exit(1)
            for dev, desc in ports:
                print(f"  可用串口：{dev}（{desc}）")
            port = ports[0][0]
            print(f"  自动使用第一个：{port}")
        print(f"连接 {port}，波特率 {args.baud} ...")
        source = SerialSource(port, args.baud)
        pace = False
        mode = "live"
        source_name = port

    base_dir = os.path.dirname(os.path.abspath(__file__))
    static_dir = os.path.join(base_dir, "web", "static")
    data_dir = os.path.abspath(os.path.join(base_dir, "..", "web_data"))
    history_path = os.path.join(data_dir, "events.json")
    upload_dir = os.path.join(data_dir, "uploads")

    engine = MonitorEngine(source, cfg, history_path, pace=pace,
                           mode=mode, source_name=source_name)
    engine.start()
    server = WebServer(engine, args.host, args.http_port, static_dir, upload_dir)
    url = f"http://{args.host if args.host != '0.0.0.0' else 'localhost'}:{server.port}"
    print(f"\n网页已启动：{url} （关闭此窗口或按 Ctrl+C 退出）")
    print("手机和电脑在同一局域网时，可用本机 IP 访问。\n")
    if not args.no_open:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    try:
        server.start()
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
        server.stop()
        print("已停止。")


def run_batch(source, cfg, use_mqtt):
    """回放 / 合成：逐帧处理，只在 presence 翻转时打印。"""
    det = Detector(cfg)
    publisher = make_publisher(cfg, use_mqtt)
    prev_presence = None
    n = 0
    for frame in source:
        d = det.update(frame)
        n += 1
        if d.presence != prev_presence:
            print(f"[帧 {frame.index}] {'有人' if d.presence else '无人'}（{d.motion_label}，"
                  f"短窗距离门 {d.short_range_energy:.2e}，短窗动作区 {d.short_motion_energy:.2e}）")
            if publisher:
                publisher.publish_state(d)
        prev_presence = d.presence
    print(f"完成：共 {n} 帧，最终 {'有人' if det.presence else '无人'}")
    if publisher:
        publisher.disconnect()


def run_live(source, cfg, use_mqtt, refresh=0.25):
    """实时串口：事件换行打印 + 定时原地刷新状态。"""
    det = Detector(cfg)
    publisher = make_publisher(cfg, use_mqtt)
    prev_presence = None
    prev_level = None
    last_refresh = 0.0
    n = 0

    print("实时监测中（Ctrl+C 退出）。开机后若人已静坐，做个挥手动作即可识别。")
    try:
        for frame in source:
            d = det.update(frame)
            n += 1

            if d.presence != prev_presence:
                print(f"\n[帧 {frame.index}] {'有人' if d.presence else '无人'}")
                if publisher:
                    publisher.publish_state(d)
            elif d.motion_level != prev_level:
                print(f"\n    动作变化：{d.motion_label}")
                if publisher:
                    publisher.publish_state(d)

            prev_presence = d.presence
            prev_level = d.motion_level

            now = time.time()
            if now - last_refresh >= refresh:
                last_refresh = now
                sys.stdout.write(
                    f"\r  状态：{'有人' if d.presence else '无人'}　{d.motion_label}　"
                    f"短窗动作区 {d.short_motion_energy:.2e}　帧 {frame.index}   "
                )
                sys.stdout.flush()
    except KeyboardInterrupt:
        print("\n已停止。")
    if publisher:
        publisher.disconnect()


def main():
    parser = argparse.ArgumentParser(description="WaveSense 存在检测")
    parser.add_argument("--source", choices=["auto", "replay", "serial", "simulated"],
                        default="auto", help="数据来源：auto=自动接串口，无雷达则演示模式")
    parser.add_argument("--file", help="replay 模式的 .gz 文件路径")
    parser.add_argument("--scenario", default="demo",
                        choices=["demo", "empty", "sit", "small", "large"],
                        help="simulated 场景")
    parser.add_argument("--frames", type=int, default=2000, help="simulated 单场景帧数")
    parser.add_argument("--port", help="serial 串口设备，如 /dev/tty.usbserial-xxx")
    parser.add_argument("--baud", type=int, default=921600, help="serial 波特率")
    parser.add_argument("--mqtt", action="store_true", help="发布到 MQTT")
    parser.add_argument("--web", action="store_true", help="启动本地网页（演示/记录）")
    parser.add_argument("--no-open", action="store_true", help="启动后不自动打开浏览器")
    parser.add_argument("--host", default="0.0.0.0", help="网页监听地址，默认所有网卡")
    parser.add_argument("--http-port", type=int, default=8080, help="网页端口，默认 8080")
    args = parser.parse_args()

    cfg = load_config()

    # auto：有雷达串口就实时接，没有就自动降级为内置演示
    if args.source == "auto":
        real = pick_real_serial_port()
        if real:
            args.source = "serial"
            if not args.port:
                args.port = real
            print(f"检测到雷达串口 {real}，使用实时模式。")
        else:
            args.source = "simulated"
            print("未检测到雷达串口，自动切换到内置演示数据。")

    if args.web:
        run_web(args, cfg)
        return

    if args.source == "replay":
        if not args.file:
            print("错误：replay 模式需要 --file", file=sys.stderr)
            sys.exit(1)
        run_batch(FileReplaySource(args.file), cfg, args.mqtt)
    elif args.source == "simulated":
        run_batch(SimulatedSource(args.scenario, args.frames), cfg, args.mqtt)
    else:
        port = args.port
        if not port:
            ports = list_serial_ports()
            if not ports:
                print("没找到串口：请确认 USB 转串口模组已插好、驱动已安装。")
                sys.exit(1)
            for dev, desc in ports:
                print(f"  可用串口：{dev}（{desc}）")
            port = ports[0][0]
            print(f"  自动使用第一个：{port}（也可用 --port 指定）")
        print(f"连接 {port}，波特率 {args.baud} ...")
        run_live(SerialSource(port, args.baud), cfg, args.mqtt)


if __name__ == "__main__":
    main()

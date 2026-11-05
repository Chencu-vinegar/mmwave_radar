# -*- coding: utf-8 -*-
"""main.py —— 程序入口。

规划链路：数据源(Frame) → Detector 判定(A3) → MqttPublisher 发布(A4) → HA。
A2 阶段：仅接通 simulated 合成数据源，逐帧打印能量概要和 targets；
replay / serial / mqtt 尚未实现，选了会给出提示而不是崩溃。

运行方式（在项目根目录）：
    python -m src.main --source simulated
    python -m src.main --source simulated --scene sit --frames 5
"""

import argparse
import glob
from pathlib import Path

from src.config import load_config
from src.data_source import SCENES, SimulatedSource, FileReplaySource, compute_bg_db
from src.detector import Detector
from src.mqtt_publisher import MqttPublisher


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WaveSense 毫米波人体存在检测（MVP）")
    # 数据源选择：simulated 已实现；replay/serial 后续任务启用
    parser.add_argument(
        "--source",
        choices=["simulated", "replay", "serial"],
        default="simulated",
        help="数据来源（A2 仅 simulated 可用）",
    )
    parser.add_argument("--file", help="replay 模式的导出样本文件路径（.dat，B2 用）")
    parser.add_argument("--raw", action="store_true",
                        help="replay/serial 模式：不扣空房底噪，直接输出原始 dB（对排查用）")
    parser.add_argument("--learn-bg", type=float, default=0,
                        help="serial 模式：空房状态下学习 N 秒，实测底噪写回 config.yaml（人要离开房间）")
    parser.add_argument("--port", help="serial 模式串口名，如 COM5（默认读 config serial_port）")
    parser.add_argument("--baud", type=int, help="serial 波特率（默认读 config serial_baud=921600）")
    parser.add_argument("--mqtt", action="store_true", help="连接 MQTT 发布（A4 启用）")
    # 仅 simulated 用：快速切换场景 / 帧数，方便验证
    parser.add_argument("--scene", choices=SCENES, help="覆盖配置里的合成场景 empty/walk/sit")
    parser.add_argument("--frames", type=int, help="覆盖配置里的合成帧数，验证时用小数值")
    return parser.parse_args()


def _print_frame(index: int, frame) -> None:
    """打印一帧的 range_energy 概要（门数/均值/峰值门）和 targets。"""
    energy = frame.range_energy
    mean_e = sum(energy) / len(energy)
    peak_gate = max(range(len(energy)), key=lambda g: energy[g])
    peak_e = energy[peak_gate]

    # targets 概要：每个目标打印 门号/状态/速度；None 表示无目标信息
    target_desc = [
        f"门{t.get('gate')} {t.get('state')} v={t.get('speed_mps')}"
        for t in (frame.targets or [])
    ]
    print(
        f"[帧{index:03d}] t={frame.timestamp:.2f}s "
        f"门数={len(energy)} 均值={mean_e:.2f} 峰值={peak_e:.2f}@门{peak_gate} "
        f"targets={len(frame.targets or [])} {target_desc if target_desc else ''}"
    )


def run_simulated(cfg: dict, args: argparse.Namespace) -> None:
    source = SimulatedSource(
        config=cfg,
        scene=args.scene,
        num_frames=args.frames,
        real_time=True,  # 演示时按帧率节奏打印；写测试时可改 False
    )
    detector = Detector(cfg)  # 阈值从 config 读

    # --mqtt：连接本地 broker；连不上只警告，不影响本地判定
    publisher = MqttPublisher(cfg) if args.mqtt else None
    if publisher:
        publisher.connect()

    print(f"[main] simulated 场景={source.scene} 帧率={source.fps} 门数={source.num_gates} "
          f"帧数={source.num_frames} mqtt={'开' if publisher else '关'}")
    try:
        for index, frame in enumerate(source.iter_frames(), start=1):
            result = detector.update(frame)
            _print_frame(index, frame)
            mark = " ← 状态翻转" if result["changed"] else ""
            print(
                f"    → presence={result['presence']} "
                f"people_count={result['people_count']} "
                f"changed={result['changed']}{mark}"
            )
            if publisher:
                # presence 只在 changed 时发布；people_count 内部变化才发
                if result["changed"]:
                    publisher.publish_presence(result["presence"])
                publisher.publish_people_count(result["people_count"])
    finally:
        if publisher:
            publisher.disconnect()
    print("[main] 合成序列播放完毕。")


def run_replay(cfg: dict, args: argparse.Namespace) -> None:
    """回放授权软件导出的 DAT：FileReplaySource → Detector → 打印 presence。"""
    if not args.file:
        print("[main] replay 模式需要 --file samples/xxx.dat")
        return
    num_gates = int(cfg["sim_num_gates"])
    fps = float(cfg["sim_frame_rate"])
    exclude_dc = bool(cfg.get("exclude_dc_bin0", True))

    # 默认用 samples/ 下所有 empty_*.dat 估计空房底噪；--raw 时不扣
    bg_db = None
    if not args.raw:
        empty_files = sorted(glob.glob("samples/empty_*.dat"))
        if not empty_files:
            print("[main] 未找到 samples/empty_*.dat，无法估计底噪；请加 --raw 或先放空房样本。")
            return
        bg_db = compute_bg_db(empty_files, num_gates)
        print(f"[main] 空房底噪来自 {[Path(f).name for f in empty_files]}")

    source = FileReplaySource(
        file_path=args.file, num_gates=num_gates, fps=fps,
        bg_db=bg_db, exclude_dc_bin0=exclude_dc,
    )
    detector = Detector(cfg)

    publisher = MqttPublisher(cfg) if args.mqtt else None
    if publisher:
        publisher.connect()

    print(f"[main] replay 文件={Path(args.file).name} 门数={num_gates} 帧率={fps} "
          f"底噪={'原始' if args.raw else '已扣'} mqtt={'开' if publisher else '关'}")
    flip_count = 0
    try:
        for index, frame in enumerate(source.iter_frames(), start=1):
            result = detector.update(frame)
            # 回放是离线文件，不逐帧刷屏；只在状态翻转帧和开头/结尾打印概要
            if result["changed"] or index == 1:
                _print_frame(index, frame)
                print(f"    → presence={result['presence']} "
                      f"people_count={result['people_count']} changed={result['changed']}")
                flip_count += 1
            if publisher:
                if result["changed"]:
                    publisher.publish_presence(result["presence"])
                publisher.publish_people_count(result["people_count"])
    finally:
        if publisher:
            publisher.disconnect()
    total = index if 'index' in dir() else 0
    print(f"[main] 回放完毕：共 {total} 帧，presence 翻转 {flip_count} 次，"
          f"最终 presence={detector.presence}")


def _learn_bg(cfg: dict, args: argparse.Namespace, num_gates: int) -> None:
    """空房底噪学习：N 秒空房原始 dB 统计，写回 config.yaml 的 live_bg_db。"""
    import statistics
    import time
    from src.data_source import SerialSource

    seconds = float(args.learn_bg)
    port = args.port or cfg.get("serial_port", "COM5")
    baud = args.baud or int(cfg.get("serial_baud", 921600))
    print(f"[学习] 请确认房间已空（人离开！），{seconds:.0f} 秒后开始采底噪...")
    time.sleep(2.0)

    source = SerialSource(
        port=port, baud=baud, num_gates=num_gates,
        fps=float(cfg["sim_frame_rate"]),
        exclude_dc_bin0=bool(cfg.get("exclude_dc_bin0", True)),
        bg_db=None,  # 学习时不扣底噪，采的是原始 dB
    )
    # frame_sums 是扣完 live_bg 后的残差能量和（bg_db=None 时 range_energy 即原始 dB，
    # 这里手动减去刚采的每门均值，和后续正式运行同一口径）
    mean_bg = [gate_sum[g] / n for g in range(num_gates)]
    frame_sums = []
    n = 0
    t0 = time.time()
    print("[学习] 采样中...（保持空房别动）")
    try:
        for frame in source.iter_frames():
            n += 1
            for g in range(num_gates):
                gate_sum[g] += frame.range_energy[g]
            # 残差能量和：各门原始 dB - 已采均值的累计估计（首帧近似为0，用最终均值更准，下面重算）
            frame_sums.append(sum(frame.range_energy[1:]))
            if time.time() - t0 >= seconds:
                break
    except Exception as e:
        print(f"[学习] 串口失败：{e}")
        return

    # 残差口径：每门原始 dB 减去均值后的帧能量和
    frame_sums_res = []
    # 重新遍历不可行（流已耗尽），改用近似：sum(raw) - sum(mean_bg[1:])
    import statistics
    sum_bg_rest = sum(mean_bg[1:])
    frame_sums_res = [s - sum_bg_rest for s in frame_sums]
    frame_sums_res.sort()
    p50 = frame_sums_res[len(frame_sums_res) // 2]
    p99 = frame_sums_res[min(len(frame_sums_res) - 1, int(len(frame_sums_res) * 0.99))]
    suggest_thr = round(max(1.0, p99 * 1.5), 1)

    print(f"\n[学习] {n} 帧采完。各门平均 dB 底噪：")
    for g in range(num_gates):
        print(f"  门{g}: {mean_bg[g]:.2f}")
    print(f"扣底噪后残差帧能量和：均值={statistics.mean(frame_sums_res):.2f} "
          f"P50={p50:.2f} P99={p99:.2f}")
    print(f"建议 energy_threshold = {suggest_thr}（空房残差 P99 的 1.5 倍留余量）")

    # 写回 config.yaml：live_bg_db 行已存在就替换，否则追加
    bg_line = "live_bg_db: [" + ", ".join(f"{x:.2f}" for x in mean_bg) + "]"
    cfg_path = Path("config/config.yaml")
    text = cfg_path.read_text(encoding="utf-8")
    if "live_bg_db:" in text:
        import re
        text = re.sub(r"^live_bg_db:.*$", bg_line, text, flags=re.M)
    else:
        text += "\n# 实机空房学习的每门 dB 底噪（--learn-bg 写入；空数组=用 samples 旧样本）\n"
        text += bg_line + "\n"
    cfg_path.write_text(text, encoding="utf-8")
    print(f"[学习] 已写回 {cfg_path}。如需把阈值改成建议值，手动把 energy_threshold 改成 {suggest_thr}。")


def run_serial(cfg: dict, args: argparse.Namespace) -> None:
    """实时串口：SerialSource → Detector → 打印/发布 presence（B2 第 3 步）。"""
    import glob
    from pathlib import Path

    port = args.port or cfg.get("serial_port", "COM5")
    baud = args.baud or int(cfg.get("serial_baud", 921600))
    num_gates = int(cfg["sim_num_gates"])
    fps = float(cfg["sim_frame_rate"])
    max_frames = args.frames

    try:
        from src.data_source import SerialSource, compute_bg_db
    except ImportError as e:
        print(f"[main] 需要 pyserial：python -m pip install pyserial（{e}）")
        return

    num_gates = int(cfg["sim_num_gates"])

    # ---- 学习模式：空房时实测底噪，写回 config.yaml ----
    if args.learn_bg and args.learn_bg > 0:
        return _learn_bg(cfg, args, num_gates)

    # 默认用 samples/empty_*.dat 算空房底噪；config 里若有 live_bg_db 优先用实机测的
    bg_db = None
    live_bg = cfg.get("live_bg_db") or []
    if not args.raw and len(live_bg) == num_gates:
        bg_db = [float(x) for x in live_bg]
        print(f"[main] 使用实机学习的底噪 live_bg_db（{num_gates} 门）")
    elif not args.raw:
        empty_files = sorted(glob.glob("samples/empty_*.dat"))
        if not empty_files:
            print("[main] 未找到 samples/empty_*.dat，无法扣底噪；加 --raw 看原始 dB。")
            return
        bg_db = compute_bg_db(empty_files, num_gates)
        print(f"[main] 空房底噪来自 {[Path(f).name for f in empty_files]}")

    source = SerialSource(
        port=port, baud=baud, num_gates=num_gates, fps=fps,
        max_frames=max_frames,
        exclude_dc_bin0=bool(cfg.get("exclude_dc_bin0", True)),
        bg_db=bg_db,
    )
    detector = Detector(cfg)
    publisher = MqttPublisher(cfg) if args.mqtt else None
    if publisher:
        publisher.connect()

    print(f"[main] serial 打开 {port} @ {baud}，8N1，门数={num_gates}，"
          f"max_frames={max_frames or '无限'}，底噪={'原始' if args.raw else '已扣'}。Ctrl+C 停止。")
    index = 0
    import time
    last_beat = time.time()
    try:
        for index, frame in enumerate(source.iter_frames(), start=1):
            result = detector.update(frame)
            # 每 10 帧或状态翻转时打印；空房时避免刷屏
            if result["changed"] or index == 1 or index % 10 == 0:
                _print_frame(index, frame)
                print(f"    → presence={result['presence']} "
                      f"people_count={result['people_count']} changed={result['changed']}")
            if publisher:
                if result["changed"]:
                    publisher.publish_presence(result["presence"])
                publisher.publish_people_count(result["people_count"])
            # 心跳：没出翻转时每秒报一次帧数，确认活着
            now = time.time()
            if now - last_beat >= 1.0:
                last_beat = now
                if index % 10 != 0 and not result["changed"]:
                    print(f"[等待] 已收 {source.bytes_received} 字节，{index} 帧，"
                          f"presence={detector.presence}，resync={source.resync_count}")
    except KeyboardInterrupt:
        print("\n[main] 用户中断。")
    except Exception as e:
        print(f"[main] 串口读取失败：{e}")
    finally:
        if publisher:
            publisher.disconnect()
        print(f"[main] 结束：共 {index} 帧，收到 {source.bytes_received} 字节，"
              f"resync={source.resync_count}，最终 presence={detector.presence}")


def main() -> None:
    args = parse_args()
    cfg = load_config()

    if args.source == "simulated":
        run_simulated(cfg, args)
    elif args.source == "replay":
        run_replay(cfg, args)
    elif args.source == "serial":
        run_serial(cfg, args)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""calibrate_thresholds.py —— 新环境标定统计工具（离线，不接串口、不发网络）。

用途：
  把新房间录到的三类样本（空房 empty / 静坐 rest / 走动 walk）喂进来，
  按 Detector 同一口径统计短窗能量分布，给出 config.yaml 里各阈值的建议值。

口径与 src/detector/detector.py 保持一致：
  - 距离门：位置 0-5，两通道 int16 平方和；动作区：位置 6-15；
  - 短窗 = 最近 short_window 帧的均值；远门比例 = 门4+门5 能量 / 全部门能量；
  - 帧速率 fps 仅用于把“帧间隔”换算成秒（Detector 注释口径约 480 fps）。

运行（在 wavesense 根目录）：
    $env:PYTHONPATH="."
    python tools/calibrate_thresholds.py --empty a.gz --rest b.gz --walk c.gz
    # 每类可以传多个文件：--empty e1.gz e2.gz

输出：
  - 终端打印各类样本的统计表；
  - tools/calibration_report.txt 保存同样内容；
  - 末尾给出建议的 detector 配置块（人工复核后再填进 config/config.yaml，脚本不自动改写配置）。
"""
import argparse
import statistics
from pathlib import Path

import numpy as np

from src.data_source import FileReplaySource

RANGE_POS = list(range(0, 6))
MOTION_POS = list(range(6, 16))


def _percentile(arr: np.ndarray, q: float) -> float:
    """q ∈ [0,1]，返回分位数；空数组返回 0。"""
    if len(arr) == 0:
        return 0.0
    return float(np.percentile(arr, q * 100))


def load_arrays(path: str) -> tuple[np.ndarray, np.ndarray]:
    """读取一个 gz/dat，返回 (range_energy[n], motion_energy[n])，均为标量序列。"""
    ranges, motions = [], []
    for frame in FileReplaySource(path).frames():
        c0 = frame.ch0.astype(np.float64)
        c1 = frame.ch1.astype(np.float64)
        ranges.append(float(np.sum(c0[RANGE_POS] ** 2 + c1[RANGE_POS] ** 2)))
        motions.append(float(np.sum(c0[MOTION_POS] ** 2 + c1[MOTION_POS] ** 2)))
    return np.asarray(ranges), np.asarray(motions)


def rolling_mean(x: np.ndarray, window: int) -> np.ndarray:
    """短窗均值；前 window-1 帧用可用帧的均值（与 Detector 的 deque 行为一致）。"""
    if len(x) == 0:
        return x
    if window <= 1:
        return x
    out = np.empty_like(x)
    for i in range(len(x)):
        lo = max(0, i - window + 1)
        out[i] = x[lo:i + 1].mean()
    return out


def far_ratio_series(path: str, window: int) -> np.ndarray:
    """远门（门4-5）能量占比的短窗序列，口径同 Detector。"""
    gate_sums = []
    for frame in FileReplaySource(path).frames():
        c0 = frame.ch0.astype(np.float64)
        c1 = frame.ch1.astype(np.float64)
        gates = c0[RANGE_POS] ** 2 + c1[RANGE_POS] ** 2
        gate_sums.append(gates)
    if not gate_sums:
        return np.asarray([])
    gate_sums = np.asarray(gate_sums)
    out = np.empty(len(gate_sums))
    for i in range(len(gate_sums)):
        lo = max(0, i - window + 1)
        win = gate_sums[lo:i + 1].sum(axis=0)
        total = float(win.sum())
        out[i] = float((win[4] + win[5]) / total) if total > 0 else 0.0
    return out


def summarize(name: str, files: list[str], window: int, fps: float) -> dict:
    """合并同类样本，返回统计字典。"""
    all_range, all_motion, all_far = [], [], []
    for f in files:
        r, m = load_arrays(f)
        if len(r) < window:
            print(f"[跳过] {Path(f).name} 仅 {len(r)} 帧，少于短窗 {window}")
            continue
        all_range.append(rolling_mean(r, window))
        all_motion.append(rolling_mean(m, window))
        all_far.append(far_ratio_series(f, window))
        print(f"[读取] {Path(f).name}: {len(r)} 帧（约 {len(r) / fps:.1f}s）")

    if not all_range:
        raise SystemExit(f"[错误] {name} 类没有可用样本。")

    range_s = np.concatenate(all_range)
    motion_s = np.concatenate(all_motion)
    far_s = np.concatenate(all_far)

    stats = {
        "name": name,
        "frames": int(len(range_s)),
        "seconds": len(range_s) / fps,
        "range_p50": _percentile(range_s, 0.50),
        "range_p95": _percentile(range_s, 0.95),
        "range_p99": _percentile(range_s, 0.99),
        "range_max": float(range_s.max()),
        "motion_p50": _percentile(motion_s, 0.50),
        "motion_p95": _percentile(motion_s, 0.95),
        "motion_p99": _percentile(motion_s, 0.99),
        "far_p05": _percentile(far_s, 0.05),
        "far_p50": _percentile(far_s, 0.50),
        "far_p95": _percentile(far_s, 0.95),
    }
    return stats


def longest_rest_gap(rest_files: list[str], window: int, active_level: float) -> int:
    """静坐样本里，相邻活动尖峰（短窗距离门 > active_level）之间的最长帧数间隔。"""
    max_gap = 0
    for f in rest_files:
        r, _ = load_arrays(f)
        short = rolling_mean(r, window)
        active_idx = np.where(short > active_level)[0]
        if len(active_idx) < 2:
            continue
        gaps = np.diff(active_idx)
        max_gap = max(max_gap, int(gaps.max()))
    return max_gap


def fmt(x: float) -> str:
    return f"{x:.3e}" if x >= 1e6 else f"{x:.3f}"


def main() -> None:
    ap = argparse.ArgumentParser(description="WaveSense 新环境阈值标定统计")
    ap.add_argument("--empty", nargs="+", required=True, help="空房样本 gz/dat（可多个）")
    ap.add_argument("--rest", nargs="+", required=True, help="静坐样本 gz/dat（可多个）")
    ap.add_argument("--walk", nargs="+", required=True, help="走动样本 gz/dat（可多个）")
    ap.add_argument("--window", type=int, default=30, help="短窗帧数（与 config short_window 一致，默认 30）")
    ap.add_argument("--fps", type=float, default=480.0, help="逻辑帧速率（帧/秒，仅用于秒换算，默认 480）")
    args = ap.parse_args()

    empty = summarize("空房", args.empty, args.window, args.fps)
    rest = summarize("静坐", args.rest, args.window, args.fps)
    walk = summarize("走动", args.walk, args.window, args.fps)

    # ---- 建议阈值（人工复核后填 config；脚本不自动改写配置） ----
    # 活动阈值：空房 P99 留 1.5 倍余量；同时必须低于静坐尖峰下沿，否则静坐进不来。
    active_thr = max(1.0, empty["range_p99"] * 1.5)
    # 远门比例：取空房 P95 与静坐 P10 之间；静坐 P10 这里用 far_p05 近似下沿。
    far_ratio = max(0.05, max(empty["far_p95"], rest["far_p05"]) * 1.1)
    # 小动阈值：静坐动作区 P95 留 1.2 倍余量；大动阈值：走动动作区 P10 与小动阈值取中。
    small_thr = max(1.0, rest["motion_p95"] * 1.2)
    walk_p10 = _percentile(np.concatenate([load_arrays(f)[1] for f in args.walk]), 0.10)
    large_thr = max(small_thr * 1.5, (small_thr + walk_p10) / 2)
    # 离开保持：静坐最长尖峰间隔 ×1.2 留余量。
    rest_gap = longest_rest_gap(args.rest, args.window, active_thr)
    leave_gap = int(max(12000, rest_gap * 1.2)) if rest_gap > 0 else 12000

    lines = []
    lines.append("=" * 96)
    lines.append(f"{'样本':<6}{'帧数':>8}{'时长(s)':>10}{'距离门P50':>14}{'距离门P95':>14}"
                 f"{'距离门P99':>14}{'动作区P50':>14}{'动作区P95':>14}{'远门比P05':>12}{'远门比P95':>12}")
    lines.append("-" * 96)
    for s in (empty, rest, walk):
        lines.append(
            f"{s['name']:<6}{s['frames']:>8}{s['seconds']:>10.1f}{fmt(s['range_p50']):>14}"
            f"{fmt(s['range_p95']):>14}{fmt(s['range_p99']):>14}{fmt(s['motion_p50']):>14}"
            f"{fmt(s['motion_p95']):>14}{fmt(s['far_p05']):>12}{fmt(s['far_p95']):>12}"
        )
    lines.append("-" * 96)
    lines.append(f"静坐最长活动尖峰间隔：{rest_gap} 帧（约 {rest_gap / args.fps:.1f}s）")
    lines.append("")
    lines.append("建议填入 config/config.yaml 的 detector 段（请对照上方分布复核）：")
    lines.append("  detector:")
    lines.append(f"    active_threshold: {active_thr:.3e}   # 空房 P99={fmt(empty['range_p99'])}；"
                 f"静坐尖峰下沿 P10≈{fmt(rest['range_p50'])}，阈值须低于它")
    lines.append(f"    far_gate_ratio: {far_ratio:.2f}          # 空房 P95={fmt(empty['far_p95'])}，"
                 f"静坐 P05={fmt(rest['far_p05'])}")
    lines.append(f"    small_move_threshold: {small_thr:.3e}")
    lines.append(f"    large_move_threshold: {large_thr:.3e}")
    lines.append(f"    leave_gap_frames: {leave_gap}        # 静坐最长间隔 {rest_gap} 帧 ×1.2")
    lines.append("")
    lines.append("复核要点：")
    lines.append("  1) active_threshold 必须 < 静坐距离门尖峰下沿（rest P10 附近），否则静坐不会被判有人；")
    lines.append("  2) 若空房误报，先检查近门电器，再抬高 active_threshold / far_gate_ratio；")
    lines.append("  3) 若静坐被误判离开，增大 leave_gap_frames；若离开关灯太慢，核对实测最长尖峰间隔后下调。")

    report = "\n".join(lines)
    print(report)
    out = Path(__file__).resolve().parent / "calibration_report.txt"
    out.write_text(report, encoding="utf-8")
    print(f"\n[报告已保存] {out}")


if __name__ == "__main__":
    main()

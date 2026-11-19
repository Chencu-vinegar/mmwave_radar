"""单段/多段录制数据分析：能量特征 + 判定器回放结果。

用法:
  PYTHONPATH=. python tools/analyze_sample.py samples/xxx.dat [yyy.gz ...]

每段输出：
  帧数、时长、距离门/动作区短窗（30 帧）均值/95%/最大、远门比例 95%、
  判定器有人占比、进入/离开次数、动作分级分布。
口径与 Detector 保持一致，能量单位为两通道 int16 平方和。
"""
import os
import sys
from collections import deque

import numpy as np

from src.config import load_config
from src.data_source import FileReplaySource
from src.detector import Detector

SHORT_WIN = 30


def analyze(path, cfg):
    det = Detector(cfg)
    rng_q = deque(maxlen=SHORT_WIN)
    mot_q = deque(maxlen=SHORT_WIN)
    gate_q = deque(maxlen=SHORT_WIN)

    n = 0
    present = 0
    levels = [0, 0, 0]
    n_enter = n_leave = 0
    prev = False
    short_rng, short_mot, far = [], [], []

    for frame in FileReplaySource(path):
        rng = frame.range_energy()
        mot = frame.motion_energy()
        c0 = frame.ch0.astype(np.float64)
        c1 = frame.ch1.astype(np.float64)
        gates = np.array([c0[p] ** 2 + c1[p] ** 2 for p in range(6)])

        rng_q.append(rng)
        mot_q.append(mot)
        gate_q.append(gates)

        sr = float(np.mean(rng_q))
        sm = float(np.mean(mot_q))
        gs = np.sum(gate_q, axis=0)
        gt = float(gs.sum())
        fr = float((gs[4] + gs[5]) / gt) if gt > 0 else 0.0
        short_rng.append(sr)
        short_mot.append(sm)
        far.append(fr)

        d = det.update(frame)
        n += 1
        if d.presence:
            present += 1
            levels[d.motion_level] += 1
        if d.presence and not prev:
            n_enter += 1
        if prev and not d.presence:
            n_leave += 1
        prev = d.presence

    def stat(a):
        a = np.asarray(a, dtype=np.float64)
        return float(a.mean()), float(np.percentile(a, 95)), float(a.max())

    rm, rp, rx = stat(short_rng)
    mm, mp, mx = stat(short_mot)
    far_p95 = float(np.percentile(far, 95))
    tot = sum(levels) or 1
    return {
        "n": n,
        "dur": n / 480,
        "present": present / n * 100,
        "enter": n_enter,
        "leave": n_leave,
        "rng": (rm, rp, rx),
        "mot": (mm, mp, mx),
        "far": far_p95,
        "lv": tuple(v / tot * 100 for v in levels),
    }


def fmt(v):
    return f"{v:.2e}"


def main():
    cfg = load_config()
    paths = sys.argv[1:]
    if not paths:
        print("用法: python tools/analyze_sample.py <录制文件> [更多文件...]")
        return

    for path in paths:
        if not os.path.exists(path):
            print(f"文件缺失: {path}")
            continue
        r = analyze(path, cfg)
        name = os.path.basename(path)
        print("=" * 96)
        print(f"文件: {name}")
        print(f"  帧数 {r['n']}  时长 {r['dur']:.1f}s")
        print(f"  距离门短窗  均值 {fmt(r['rng'][0])}  95% {fmt(r['rng'][1])}  最大 {fmt(r['rng'][2])}")
        print(f"  动作区短窗  均值 {fmt(r['mot'][0])}  95% {fmt(r['mot'][1])}  最大 {fmt(r['mot'][2])}")
        print(f"  远门比例 95% {r['far']:.3f}")
        print(f"  判定器: 有人 {r['present']:.1f}%  进入 {r['enter']} 次  离开 {r['leave']} 次")
        print(f"  动作分级: 不动 {r['lv'][0]:.0f}%  小动 {r['lv'][1]:.0f}%  大动 {r['lv'][2]:.0f}%")


if __name__ == "__main__":
    main()

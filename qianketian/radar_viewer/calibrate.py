# -*- coding: utf-8 -*-
"""calibrate.py — 分析录制的雷达数据，标定 presence_detect 能量阈值。

用法:
  python calibrate.py empty.dat sit.dat walk.dat [--warmup-frames N]

每组数据（空房 / 静坐 / 走动）：
  1. 解析数据（自动识别 ICLH 封装或 55 AA 裸帧），配对 RX0/RX1，按 64 chirp 组帧；
  2. 每帧做软件 2DFFT，提取 range_energy（距离门）与 motion_energy（动作门）；
  3. 统计分布（min / med / p95 / max），并给出建议阈值。

说明：
  - ICLH 封装（SDK 工具保存的 .dat）与 55 AA 裸帧（串口录制）同为 1dfft 8 距离门
    复数，内容一致，本工具按文件头自动区分。
  - 雷达上电后约 40 秒内处于预热期（距离/动作门能量从 ~1e10 指数衰减到 ~1e6），
    若录制时未等待预热完成，可用 --warmup-frames N 跳过开头的 N 帧再统计。
"""
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import presence_detect as pd
import radar_parser as rp
import serial_live as sl

FRAME_HDR = b"\x55\xAA"
FRAME_LEN = 40


def parse_55aa(raw, n_bin=8):
    """解析 55 AA 裸帧字节流，返回 [(gch, r0, r1), ...] 完整 chirp 列表。"""
    assembler = sl.LiveChirpAssembler()
    chirps = []
    i = 0
    n = len(raw)
    while True:
        idx = raw.find(FRAME_HDR, i)
        if idx < 0:
            break
        if idx + 5 > n:
            break
        ch = raw[idx + 2]
        if (ch & 0x70) != 0x30 or raw[idx + 4] != 0x09:
            i = idx + 2
            continue
        if idx + FRAME_LEN > n:
            break
        frame = raw[idx:idx + FRAME_LEN]
        rx = (ch >> 7) & 1
        chirp = (ch & 0x0F) * 16 + (frame[3] >> 4)
        cnt = chirp * 16
        vals = [struct.unpack(">h", frame[5 + 2 * k:7 + 2 * k])[0] for k in range(16)]
        cplx = np.array([complex(vals[2 * k], vals[2 * k + 1]) for k in range(n_bin)],
                        dtype=np.complex128)
        res = assembler.add(cnt, rx, cplx)
        if res is not None:
            chirps.append(res)
        i = idx + FRAME_LEN
    return chirps


def analyze(path, cpf=64, n_bin=8, motion_lo_bin=3, warmup_frames=0):
    with open(path, "rb") as f:
        head = f.read(4)
    if head == b"ICLH":
        # ICLH 封装（SDK 保存的 .dat）：与 55 AA 裸帧同为 1dfft 8 距离门复数
        chirps, _ = rp.parse_dat(path, config=rp.RadarConfig())
    else:
        # 串口录制的 55 AA 裸帧
        with open(path, "rb") as f:
            raw = f.read()
        chirps = parse_55aa(raw, n_bin)
    if not chirps:
        print(f"  {os.path.basename(path)}: no valid chirps")
        return None, None
    # 按完整帧组帧
    gch = np.array([c[0] for c in chirps], dtype=np.int64)
    fr = gch // cpf
    ranges, motions = [], []
    n_frames = 0
    # 逐帧提取（按帧号分组，忽略不完整首尾帧）
    for f, idxs in _group_frames(fr, cpf):
        r0 = np.stack([chirps[j][1] for j in idxs])
        r1 = np.stack([chirps[j][2] for j in idxs])
        re_, me_, _, _ = pd.extract_features(r0, r1, n_bin, cpf,
                                             motion_lo_bin=motion_lo_bin)
        ranges.append(re_)
        motions.append(me_)
        n_frames += 1
    ranges = np.array(ranges)
    motions = np.array(motions)
    if warmup_frames > 0 and len(ranges) > warmup_frames:
        ranges = ranges[warmup_frames:]
        motions = motions[warmup_frames:]
        print(f"  [warmup] 跳过前 {warmup_frames} 帧（预热期）")
    return ranges, motions


def _group_frames(fr, cpf):
    """按帧号分组 chirp 下标，仅返回满 64 chirp 的帧。"""
    from collections import defaultdict
    d = defaultdict(list)
    for i, f in enumerate(fr):
        d[f].append(i)
    out = []
    for f in sorted(d):
        if len(d[f]) == cpf:
            out.append((f, d[f]))
    return out


def _fmt(a):
    return (f"min={a.min():.3e} med={np.median(a):.3e} "
            f"p95={np.percentile(a, 95):.3e} max={a.max():.3e}")


def main():
    args = sys.argv[1:]
    warmup_frames = 0
    paths = []
    i = 0
    while i < len(args):
        if args[i] == "--warmup-frames" and i + 1 < len(args):
            warmup_frames = int(args[i + 1])
            i += 2
        else:
            paths.append(args[i])
            i += 1
    if len(paths) < 3:
        print(__doc__)
        return
    paths = paths[:3]
    labels = ["empty", "sit", "walk"]
    stats = {}
    for lab, p in zip(labels, paths):
        print("=" * 62)
        print(f"[{lab}] {p}")
        rng, mot = analyze(p, warmup_frames=warmup_frames)
        if rng is None:
            continue
        stats[lab] = (rng, mot)
        print(f"  frames      = {len(rng)}")
        print(f"  range_energy  {_fmt(rng)}")
        print(f"  motion_energy {_fmt(mot)}")

    # 建议阈值
    print("=" * 62)
    print("建议阈值（供 presence_detect.DetectorConfig 覆盖）:")
    if "empty" in stats and "sit" in stats:
        e_p95 = np.percentile(stats["empty"][0], 95)
        s_med = np.median(stats["sit"][0])
        active = (e_p95 + s_med) / 2
        print(f"  active_threshold      ≈ {active:.3e}  (空房 p95 与静坐中位之间)")
    if "sit" in stats and "walk" in stats:
        s_p95 = np.percentile(stats["sit"][1], 95)
        w_med = np.median(stats["walk"][1])
        large = (s_p95 + w_med) / 2
        small = large / 4
        print(f"  large_move_threshold  ≈ {large:.3e}  (静坐动作 p95 与走动中位之间)")
        print(f"  small_move_threshold  ≈ {small:.3e}  (large 的 1/4，起始值)")
        print(f"  walk_strong_threshold ≈ {large:.3e}")
    if "empty" in stats and "sit" in stats:
        e_max = np.max(stats["empty"][0])
        print(f"  [参考] 空房 range 最大值 {e_max:.3e}，静坐距离门应明显高于此才可判有人")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()

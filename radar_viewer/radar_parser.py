# -*- coding: utf-8 -*-
"""
radar_parser.py — 矽典微 DEV202H (ICL1122) 毫米波雷达 .dat 数据解析核心。

数据格式（逆向得出）：
  数据帧 52B : "ICLH" | len(1B)=40 | 0 | 0 | 通道(1B, 0=RX0/1=RX1)
               | 子头(4B: 0xAA [flag+cntHi2] [cntLo8] 0x09)
               | 8 个距离 bin 复数(32B, 大端 int16 I/Q)
               | 附加(2B) | "PU"(0x50 0x55) | "ICLT"
  短帧  16B : "ICLH" | len(1B)=4 | ... 跳过（分隔/元数据）

  计数器 counter = ((子头[1] & 0x03) << 8) | 子头[2]，每 chirp +16，
  每 64 chirp 回绕 = 1 个雷达帧。同 counter 的 RX0/RX1 配对。

物理模型：
  距离 R_k = k * dR,  dR = c/(2B)
  角度 θ = arcsin( Δφ / (2π·d_ant/λ) ),  Δφ = angle(RX1·conj(RX0))
"""
import struct
import re
import os
import numpy as np

C = 3.0e8
DEFAULT_F_IDLE = 24.015e9
DEFAULT_F_TOP = 24.235e9
DEFAULT_D_ANT = 6.25e-3
N_BIN = 8
CHIRPS_PER_FRAME = 64


class RadarConfig:
    """从 FILE_Radar_Config.ini（或默认值）得到的物理参数。"""

    def __init__(self, f_idle=DEFAULT_F_IDLE, f_top=DEFAULT_F_TOP, d_ant=DEFAULT_D_ANT):
        self.f_idle = float(f_idle)
        self.f_top = float(f_top)
        self.d_ant = float(d_ant)
        self.n_bin = N_BIN
        self.chirps_per_frame = CHIRPS_PER_FRAME

    @property
    def bandwidth(self):
        return self.f_top - self.f_idle

    @property
    def fc(self):
        return (self.f_idle + self.f_top) / 2.0

    @property
    def wavelength(self):
        return C / self.fc

    @property
    def range_resolution(self):
        return C / (2.0 * self.bandwidth)

    @property
    def angle_scale(self):
        return 2.0 * np.pi * self.d_ant / self.wavelength

    def summary(self):
        return (
            f"fc={self.fc/1e9:.3f}GHz  B={self.bandwidth/1e6:.0f}MHz  "
            f"lambda={self.wavelength*1e3:.3f}mm  dR={self.range_resolution:.3f}m  "
            f"d_ant={self.d_ant*1e3:.2f}mm"
        )


def parse_config(path):
    """解析 FILE_Radar_Config.ini，提取 IdleFreq/TopFreq/HorzAngleInfo。"""
    cfg = RadarConfig()
    if not path or not os.path.isfile(path):
        return cfg
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError:
        return cfg

    def _num(key, default=None):
        m = re.search(rf"^\s*{key}\s*=\s*([-+0-9.eE]+)", text, re.M)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        return default

    idle = _num("IdleFreq")
    top = _num("TopFreq")
    if idle and top:
        # 兼容两种单位写法：>= 1e6 视为 Hz，否则视为 GHz
        cfg.f_idle = idle if idle >= 1e6 else idle * 1e9
        cfg.f_top = top if top >= 1e6 else top * 1e9

    m = re.search(r"^\s*HorzAngleInfo\s*=\s*([-+0-9.eE,; ]+)", text, re.M)
    if m:
        parts = re.split(r"[;,]", m.group(1))
        if parts:
            try:
                val = float(parts[0].strip())
                if val > 0:
                    cfg.d_ant = val * 1e-3  # mm -> m
            except ValueError:
                pass

    nbin = _num("SamplesPerChirp")
    if nbin and nbin == int(nbin) and nbin > 0:
        cfg.n_bin = int(nbin)

    return cfg


def _pair_chirps(items):
    """按 counter 将 RX0/RX1 配对，返回 [(gch, r0, r1), ...]，r 为 8 复数 ndarray。"""
    chirps = []
    wrap = 0
    prev = None
    j = 0
    n = len(items)
    while j < n:
        cnt = items[j][0]
        if prev is not None and cnt < prev:
            wrap += 1
        prev = cnt
        r0 = r1 = None
        while j < n and items[j][0] == cnt:
            if items[j][1] == 0:
                r0 = items[j][2]
            else:
                r1 = items[j][2]
            j += 1
        if r0 is not None and r1 is not None:
            chirps.append((wrap * CHIRPS_PER_FRAME + cnt // 16, r0, r1))
    return chirps


def parse_dat(path, config=None):
    """解析 .dat 文件，返回 (chirps, 统计信息)。"""
    config = config or RadarConfig()
    with open(path, "rb") as f:
        b = f.read()

    items = []
    i = 0
    n_bytes = len(b)
    while True:
        i = b.find(b"ICLH", i)
        if i < 0:
            break
        if i + 16 > n_bytes:
            break
        length = b[i + 4]
        if length == 40 and i + 52 <= n_bytes:
            ch = b[i + 7]
            sub = b[i + 8:i + 12]
            cnt = ((sub[1] & 0x03) << 8) | sub[2]
            vals = [struct.unpack(">h", b[i + 12 + 2 * k:i + 14 + 2 * k])[0] for k in range(16)]
            cplx = np.array([complex(vals[2 * k], vals[2 * k + 1]) for k in range(config.n_bin)],
                            dtype=np.complex128)
            items.append((cnt, ch, cplx))
            i += 52
        elif length == 4:
            i += 16
        else:
            i += 1

    chirps = _pair_chirps(items)
    stats = {
        "n_frame_items": len(items),
        "n_chirps": len(chirps),
        "n_frames": (max((c[0] for c in chirps), default=0) // CHIRPS_PER_FRAME + 1) if chirps else 0,
        "file_bytes": n_bytes,
    }
    return chirps, stats


def chirp_points(chirp, config, exclude_bin0=True):
    """单个 chirp -> [(range_m, angle_deg, amplitude, bin_idx), ...]"""
    gch, r0, r1 = chirp
    dphi = np.angle(r1 * np.conj(r0))
    th = np.degrees(np.arcsin(np.clip(dphi / config.angle_scale, -1, 1)))
    amp = (np.abs(r0) ** 2 + np.abs(r1) ** 2) / 2.0
    start = 1 if exclude_bin0 else 0
    pts = []
    for k in range(start, config.n_bin):
        rng = k * config.range_resolution
        pts.append((rng, float(th[k]), float(amp[k]), k))
    return pts


def build_point_arrays(chirps, config, exclude_bin0=True):
    """全部 chirp -> 展平的 numpy 数组 (frame, range, angle, amp, bin)。向量化实现。"""
    if not chirps:
        return (np.zeros(0, dtype=np.int64), np.zeros(0), np.zeros(0),
                np.zeros(0), np.zeros(0, dtype=np.int64))
    gch = np.fromiter((c[0] for c in chirps), dtype=np.int64, count=len(chirps))
    R0 = np.stack([c[1] for c in chirps])  # (n_chirp, n_bin) complex128
    R1 = np.stack([c[2] for c in chirps])
    dphi = np.angle(R1 * np.conj(R0))
    th = np.degrees(np.arcsin(np.clip(dphi / config.angle_scale, -1, 1)))
    amp = (np.abs(R0) ** 2 + np.abs(R1) ** 2) / 2.0
    fr = gch // CHIRPS_PER_FRAME
    start = 1 if exclude_bin0 else 0
    idx = np.arange(start, config.n_bin)
    n_keep = config.n_bin - start
    frames = np.repeat(fr, n_keep)
    bins = np.tile(idx, len(chirps))
    ranges = bins.astype(np.float64) * config.range_resolution
    angles = th[:, start:].ravel()
    amps = amp[:, start:].ravel()
    return frames, ranges, angles, amps, bins


def range_time_matrix(chirps, config, n_frames=None):
    """帧 x bin 的幅度累积矩阵。向量化实现。"""
    if not chirps:
        if n_frames is None:
            n_frames = 0
        return np.zeros((n_frames, config.n_bin))
    if n_frames is None:
        n_frames = chirps[-1][0] // CHIRPS_PER_FRAME + 1
    rt = np.zeros((n_frames, config.n_bin))
    gch = np.fromiter((c[0] for c in chirps), dtype=np.int64, count=len(chirps))
    fr = gch // CHIRPS_PER_FRAME
    R0 = np.stack([c[1] for c in chirps])
    R1 = np.stack([c[2] for c in chirps])
    amp = (np.abs(R0) ** 2 + np.abs(R1) ** 2) / 2.0
    valid = (fr >= 0) & (fr < n_frames)
    np.add.at(rt, fr[valid], amp[valid])
    return rt

"""WaveSense 真实雷达数据解析与可视化（B1 分析工具）。

数据来源：矽典微授权软件录制/导出的 gzip 二进制流（DEV202, 1T2R）。

二进制帧格式（经实测核对）：
- 数据帧 52 字节：
    b'ICLH' (4)
    uint32 长度字段：低 16 位 = 数据长度 40，最高字节 = 通道标志（0=ch0, 1=ch1）
    40 字节数据：前 4 字节为逐帧递增计数器，后 36 字节 = 18 个 int16(小端) 有效载荷
    b'ICLT' (4)
  ch0 帧与 ch1 帧相邻，组成 104 字节的"逻辑帧"。
- 控制帧 16 字节：b'ICLH' + 0x00800004 + 4 字节载荷 + b'ICLT'。

当前数据：三组（50cm 初版、约 1m、50cm 强化版），每组三类动作。

用法：
    .venv/bin/python tools/analyze_radar_gz.py
输出：
    tools/radar_heatmap_<组>.png   每组三类动作 × 两通道的 |int16| 热图
    tools/radar_diff_energy.png    三组帧间差分能量曲线（3 子图）
"""
import gzip
import os
import struct

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# macOS 中文字体
plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "PingFang SC", "STHeiti"]
plt.rcParams["axes.unicode_minus"] = False

DESK = "/Users/a123/Desktop"
BSED = os.path.join(DESK, "project bsed")

# 每组：(组名, 目录, [(文件名, 动作标签), ...])
GROUPS = [
    ("50cm初版", BSED, [
        ("RadarData_2026_10_20_10_16_59.gz", "不动 <0.5cm"),
        ("RadarData_2026_10_20_10_17_19.gz", "小动 3-4cm"),
        ("RadarData_2026_10_20_10_17_34.gz", "大动 >20cm"),
    ]),
    ("约1m", BSED, [
        ("RadarData_2026_10_20_10_30_12.gz", "不动 <0.5cm"),
        ("RadarData_2026_10_20_10_30_23.gz", "小动 3-4cm"),
        ("RadarData_2026_10_20_10_30_37.gz", "大动 <50cm"),
    ]),
    ("50cm强化", DESK, [
        ("RadarData_2026_10_20_10_50_18.gz", "不动 <0.5cm"),
        ("RadarData_2026_10_20_10_50_28.gz", "小动 3-4cm"),
        ("RadarData_2026_10_20_10_50_41.gz", "大动 >20cm"),
    ]),
    ("1m强化", DESK, [
        ("RadarData_2026_10_20_10_51_18.gz", "不动 <0.5cm"),
        ("RadarData_2026_10_20_10_51_25.gz", "小动 3-4cm"),
        ("RadarData_2026_10_20_10_51_38.gz", "大动 >20cm"),
    ]),
]
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

# 有效载荷布局（18 个 int16）
RANGE_POS = list(range(0, 6))    # 0-5：6 个距离门
MOTION_POS = list(range(6, 16))  # 6-15：10 个动作/速度门
CONST_POS = 17                   # 固定常量


def parse(path):
    """返回 (ch0, ch1)，各为形状 (n_frames, 18) 的 int16 数组。"""
    raw = gzip.open(path, "rb").read()
    channels = {0: [], 1: []}
    i = 0
    while i < len(raw) - 4:
        if raw[i:i + 4] == b"ICLH":
            field = struct.unpack("<I", raw[i + 4:i + 8])[0]
            dlen = field & 0xFFFF
            flag = (field >> 24) & 0xFF
            if dlen == 40 and flag in (0, 1):
                payload = struct.unpack("<18h", raw[i + 12:i + 48])
                channels[flag].append(payload)
                i += 52
            else:  # 控制帧（长度 4）
                i += 16
        else:
            i += 1
    return np.array(channels[0]), np.array(channels[1])


def smooth(x, w=30):
    return np.convolve(x, np.ones(w) / w, mode="valid")


def motion_energy(c0, c1):
    """位置 6-15 动作区能量，两通道合并，返回 (n,)。"""
    n = min(len(c0), len(c1))
    f = np.hstack([
        c0[:n][:, MOTION_POS].astype(float),
        c1[:n][:, MOTION_POS].astype(float),
    ])
    return np.sum(f ** 2, axis=1), n


def range_energy(c0, c1):
    """位置 0-5 距离门能量，两通道合并，返回 (n,)。"""
    n = min(len(c0), len(c1))
    f = np.hstack([
        c0[:n][:, RANGE_POS].astype(float),
        c1[:n][:, RANGE_POS].astype(float),
    ])
    return np.sum(f ** 2, axis=1), n


def main():
    # 收集：data = {组名: [(标签, c0, c1), ...]}
    data = {}
    lines = []
    lines.append("=" * 92)
    lines.append(f"{'组':<10}{'动作':<14}{'逻辑帧':>7}{'距离门能量(均值)':>18}{'动作区能量(均值)':>18}{'帧间差分(均值)':>18}")
    lines.append("-" * 92)
    for gname, gdir, files in GROUPS:
        data[gname] = []
        for fn, label in files:
            c0, c1 = parse(os.path.join(gdir, fn))
            data[gname].append((label, c0, c1))
            n = min(len(c0), len(c1))
            re_, _ = range_energy(c0, c1)
            me_, _ = motion_energy(c0, c1)
            arr = np.hstack([c0[:n], c1[:n]]).astype(float)
            diff = np.sum(np.diff(arr, axis=0) ** 2, axis=1)
            lines.append(f"{gname:<10}{label:<14}{n:>7}{re_.mean():>18.3e}{me_.mean():>18.3e}{diff.mean():>18.3e}")
        lines.append("-" * 92)
    report = "\n".join(lines)
    print(report)
    with open(os.path.join(OUT_DIR, "separation_report.txt"), "w", encoding="utf-8") as f:
        f.write(report)

    # ---- 每组独立热图 ----
    for gname, gdir, files in GROUPS:
        vmax = max(np.abs(c).max() for _, c0, c1 in data[gname] for c in (c0, c1))
        fig, axes = plt.subplots(3, 2, figsize=(14, 9))
        for r, (label, c0, c1) in enumerate(data[gname]):
            for c, arr in enumerate((c0, c1)):
                ax = axes[r, c]
                im = ax.imshow(np.abs(arr.T), aspect="auto", origin="lower",
                               cmap="viridis", vmin=0, vmax=vmax)
                ax.set_title(f"{gname} | {label} | 通道{c}", fontsize=11)
                ax.set_xlabel("逻辑帧序号")
                ax.set_ylabel("int16 位置 (0-17)")
                if c == 1:
                    fig.colorbar(im, ax=ax, fraction=0.046)
        fig.suptitle(f"WaveSense 数据 | {gname} 三类动作有效载荷热图（|int16|）", fontsize=14)
        fig.tight_layout(rect=[0, 0, 1, 0.97])
        p = os.path.join(OUT_DIR, f"radar_heatmap_{gname}.png")
        fig.savefig(p, dpi=120)
        plt.close(fig)
        print("热图:", p)

    # ---- 三组帧间差分能量（3 子图） ----
    fig2, axes2 = plt.subplots(4, 1, figsize=(13, 17))
    for ax, (gname, gdir, files) in zip(axes2, GROUPS):
        for label, c0, c1 in data[gname]:
            n = min(len(c0), len(c1))
            arr = np.hstack([c0[:n], c1[:n]]).astype(float)
            d = np.sum(np.diff(arr, axis=0) ** 2, axis=1)
            ax.plot(d, label=f"{label}（均值 {d.mean():.2e}）", alpha=0.72, lw=0.8)
        ax.set_title(f"{gname}：帧间差分能量", fontsize=12)
        ax.set_xlabel("逻辑帧序号")
        ax.set_ylabel("帧间差分能量")
        ax.legend(loc="upper right")
        ax.grid(alpha=0.3)
    fig2.tight_layout()
    p2 = os.path.join(OUT_DIR, "radar_diff_energy.png")
    fig2.savefig(p2, dpi=110)
    plt.close(fig2)
    print("差分曲线:", p2)


if __name__ == "__main__":
    main()

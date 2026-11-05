"""三段空房热图对比：定位干扰段的时空特征。"""
import os

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "PingFang SC", "STHeiti"]
plt.rcParams["axes.unicode_minus"] = False

from src.data_source import FileReplaySource

D = "/Users/a123/Desktop/project bsed/"
EMPTIES = [
    ("空房1", "RadarData_2026_10_20_10_56_14.gz"),
    ("空房2", "RadarData_2026_10_20_11_06_19.gz"),
    ("空房3", "RadarData_2026_10_20_11_07_21.gz"),
]
OUT = os.path.dirname(os.path.abspath(__file__))

fig, axes = plt.subplots(3, 2, figsize=(15, 11))
for r, (name, fn) in enumerate(EMPTIES):
    frames = list(FileReplaySource(D + fn).frames())
    c0 = np.array([f.ch0 for f in frames])
    c1 = np.array([f.ch1 for f in frames])
    for c, arr in enumerate((c0, c1)):
        ax = axes[r, c]
        im = ax.imshow(np.abs(arr.T), aspect="auto", origin="lower",
                       cmap="viridis", vmin=0, vmax=32767)
        ax.set_title(f"{name}（{len(frames)} 帧）| 通道{c}", fontsize=12)
        ax.set_xlabel("逻辑帧序号")
        ax.set_ylabel("int16 位置")
        if c == 1:
            fig.colorbar(im, ax=ax, fraction=0.046)
fig.suptitle("三段空房数据热图对比（|int16|）", fontsize=15)
fig.tight_layout(rect=[0, 0, 1, 0.98])
p = os.path.join(OUT, "radar_heatmap_空房对比.png")
fig.savefig(p, dpi=120)
print("输出:", p)

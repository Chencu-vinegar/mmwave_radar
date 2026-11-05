"""诊断空房2 的短窗能量 / 长窗中位 / presence 随时间。"""
import os

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "PingFang SC", "STHeiti"]
plt.rcParams["axes.unicode_minus"] = False

from src.config import load_config
from src.data_source import FileReplaySource
from src.detector import Detector

D = "/Users/a123/Desktop/project bsed/"
OUT = os.path.dirname(os.path.abspath(__file__))

fig, axes = plt.subplots(3, 1, figsize=(15, 10), sharex=True)
files = [("空房1", "RadarData_2026_10_20_10_56_14.gz"),
         ("空房2", "RadarData_2026_10_20_11_06_19.gz"),
         ("空房3", "RadarData_2026_10_20_11_07_21.gz")]
cfg = load_config()
for ax, (name, fn) in zip(axes, files):
    det = Detector(cfg)
    xs, short, lng, pres = [], [], [], []
    for fr in FileReplaySource(D + fn):
        d = det.update(fr)
        xs.append(fr.index)
        short.append(d.short_motion_energy)
        lng.append(d.long_motion_median)
        pres.append(1 if d.presence else 0)
    ax.plot(xs, short, lw=0.7, color="#888", label="短窗动作区能量")
    ax.plot(xs, lng, lw=1.2, color="#1f77b4", label="长窗中位")
    ax.axhline(3.0e8, color="orange", ls="--", lw=0.9, label="进入阈值 3e8")
    ax.axhline(1.3e8, color="red", ls="--", lw=0.9, label="保持阈值 1.3e8")
    ax.fill_between(xs, 0, max(short), where=[p == 1 for p in pres],
                    color="red", alpha=0.12, label="判有人")
    ax.set_yscale("log")
    ax.set_ylabel("能量")
    ax.set_title(f"{name}（{len(xs)} 帧）")
    ax.legend(loc="upper right", ncol=5, fontsize=8)
axes[-1].set_xlabel("逻辑帧")
fig.suptitle("三段空房判定诊断", fontsize=15)
fig.tight_layout(rect=[0, 0, 1, 0.98])
p = os.path.join(OUT, "radar_empty_diagnosis.png")
fig.savefig(p, dpi=120)
print("输出:", p)

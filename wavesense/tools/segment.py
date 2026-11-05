"""把录制文件按动作门短窗能量自动切成纯段（静止 / 小动 / 大动）。

流程：
  1. 计算每帧动作门短窗能量；
  2. 按阈值做三类划分；
  3. 用滑动窗口多数投票平滑，消除短暂跳变（不依赖 scipy）；
  4. 按平滑标签分段；每段标签由段内能量中位重新判定。

用法：
  python tools/segment.py --file <path> [--label <标称标签>]
"""
import argparse
import numpy as np

from src.data_source import FileReplaySource

REST_TH = 3.5e8      # 静止 / 动作
LARGE_TH = 2.2e9     # 小动 / 大动
WIN = 30             # 短窗
SMOOTH = 201         # 平滑窗口（帧）
MIN_SEG = 1000       # 段最短帧数（约2s，过滤动作间的碎片停顿）


def _classify(ms):
    return np.where(ms >= LARGE_TH, 2, np.where(ms >= REST_TH, 1, 0))


def _majority_vote(cls, win, labels=(0, 1, 2)):
    """滑动窗口多数投票（用 cumsum 统计窗口内各类数量）。"""
    n = len(cls)
    half = win // 2
    idx = np.arange(n)
    lo = np.clip(idx - half, 0, n)
    hi = np.clip(idx - half + win, 0, n)
    counts = []
    for c in labels:
        cum = np.concatenate([[0], np.cumsum(cls == c)])
        counts.append(cum[hi] - cum[lo])
    return np.array(labels)[np.argmax(np.stack(counts), axis=0)]


def segment(path):
    fs = list(FileReplaySource(path))
    mot = np.array([f.motion_energy() for f in fs])
    ms = np.array([mot[max(0, i - WIN + 1):i + 1].mean() for i in range(len(mot))])

    smooth = _majority_vote(_classify(ms), SMOOTH)

    segs = []
    i = 0
    while i < len(smooth):
        j = i + 1
        while j < len(smooth) and smooth[j] == smooth[i]:
            j += 1
        if j - i >= MIN_SEG:
            med = float(np.median(ms[i:j]))
            label = int(_classify(np.array([med]))[0])
            segs.append((i, j, label, med))
        i = j
    return segs


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--label", default="")
    args = ap.parse_args()
    names = {0: "静止", 1: "小动", 2: "大动"}
    print(f"文件 {args.file.split('/')[-1]}（标称 {args.label}）")
    for a, b, c, e in segment(args.file):
        print(f"  帧 {a:6d}-{b:6d}（{b-a:5d}，{(b-a)/480:.1f}s）{names[c]} 动作门中位 {e:.2e}")

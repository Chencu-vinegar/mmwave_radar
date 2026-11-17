"""批量回归：回放全部标注样本，输出存在判定与动作分级的混淆矩阵。

用法:
  PYTHONPATH=. python tools/regression.py                # 跑 samples + dataset 全部
  PYTHONPATH=. python tools/regression.py rest walk      # 只看指定场景

指标口径:
  empty 负样本: 误报率 = 有人帧占比；最长连续有人段（帧数）
  rest 正样本:  召回率 = 有人帧占比；最长连续无人段（漏检）
  small/large/walk 动作样本: 有人召回 + 大动判出率（large/walk 应判大动）

标注清单见 tools/regression_labels.json；dataset 纯段标注来自 dataset/manifest.json。
"""
import json
import os
import sys

from src.config import load_config
from src.data_source import FileReplaySource
from src.detector import Detector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES_DIR = os.path.join(ROOT, "samples")
DATASET_DIR = os.path.join(ROOT, "dataset")
LABELS_PATH = os.path.join(ROOT, "tools", "regression_labels.json")

FPS = 480


def longest(seq, val):
    best = cur = 0
    for x in seq:
        if x == val:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def replay(path, cfg):
    det = Detector(cfg)
    n = 0
    present = 0
    levels = [0, 0, 0]
    n_enter = n_leave = 0
    prev = False
    seq = []
    for frame in FileReplaySource(path):
        d = det.update(frame)
        n += 1
        seq.append(d.presence)
        if d.presence:
            present += 1
            levels[d.motion_level] += 1
        if d.presence and not prev:
            n_enter += 1
        if prev and not d.presence:
            n_leave += 1
        prev = d.presence
    tot = sum(levels) or 1
    return {
        "n": n,
        "present_ratio": present / n * 100 if n else 0,
        "enter": n_enter,
        "leave": n_leave,
        "levels": tuple(v / tot * 100 for v in levels),
        "longest_present": longest(seq, True),
        "longest_absent": longest(seq, False),
    }


def row(name, scene, r):
    lv = r["levels"]
    lv_s = f"不动{lv[0]:.0f}/小{lv[1]:.0f}/大{lv[2]:.0f}"
    return (f"{name:<34}{scene:<6}{r['n']:>7}帧 有人{r['present_ratio']:>5.1f}%  "
            f"进{r['enter']} 离{r['leave']}  {lv_s}")


def main():
    cfg = load_config()
    only = set(sys.argv[1:])

    labels = json.load(open(LABELS_PATH, encoding="utf-8"))["samples"]

    # 汇总容器
    groups = {"empty": [], "rest": [], "small": [], "large": [], "walk": []}

    print("=" * 110)
    print("【samples 原始段】")
    for fn, meta in sorted(labels.items()):
        scene = meta["scene"]
        if only and scene not in only:
            continue
        path = os.path.join(SAMPLES_DIR, fn)
        if not os.path.exists(path):
            print(f"  缺失: {fn}")
            continue
        r = replay(path, cfg)
        groups[scene].append((fn, r))
        print(row(fn, scene, r))

    print("=" * 110)
    print("【dataset 纯段】")
    manifest_path = os.path.join(DATASET_DIR, "manifest.json")
    if os.path.exists(manifest_path):
        manifest = json.load(open(manifest_path, encoding="utf-8"))
        by_scene = {}
        for item in manifest:
            by_scene.setdefault(item["scene"], []).append(item)
        for scene, items in by_scene.items():
            if only and scene not in only:
                continue
            # 纯段按场景聚合统计，避免逐段刷屏
            tot_n = tot_present = 0
            lv = [0, 0, 0]
            worst_absent = 0
            for it in items:
                path = os.path.join(DATASET_DIR, it["file"])
                if not os.path.exists(path):
                    continue
                r = replay(path, cfg)
                tot_n += r["n"]
                tot_present += r["n"] * r["present_ratio"] / 100
                for i in range(3):
                    lv[i] += r["levels"][i] / 100 * r["n"]
                worst_absent = max(worst_absent, r["longest_absent"])
            if tot_n:
                print(f"  {scene:<6} {len(items):>3}段 合计{tot_n:>7}帧 有人{tot_present/tot_n*100:5.1f}%  "
                      f"不动{lv[0]/tot_n*100:.0f}/小{lv[1]/tot_n*100:.0f}/大{lv[2]/tot_n*100:.0f}  "
                      f"最长漏检{worst_absent}帧")

    # 汇总指标
    print("=" * 110)
    print("【汇总】")

    def avg(xs, key):
        return sum(x[1][key] for x in xs) / len(xs) if xs else 0

    def worst(xs, key):
        return max((x[1][key] for x in xs), default=0)

    empty = groups["empty"]
    if empty:
        print(f"空房负样本 {len(empty)} 段: 平均误报率 {avg(empty, 'present_ratio'):.1f}%  "
              f"最高误报率 {worst(empty, 'present_ratio'):.1f}%  最长连续有人段 {worst(empty, 'longest_present')}帧")
    rest = groups["rest"]
    if rest:
        print(f"静坐正样本 {len(rest)} 段: 平均召回率 {avg(rest, 'present_ratio'):.1f}%  "
              f"最低召回率 {min(x[1]['present_ratio'] for x in rest):.1f}%  最长漏检段 {worst(rest, 'longest_absent')}帧")
    act = groups["large"] + groups["walk"]
    if act:
        big = sum(1 for _, r in act if r["levels"][2] > 50)
        print(f"大动/走动样本 {len(act)} 段: 大动判出 {big}/{len(act)}  "
              f"平均有人召回 {avg(act, 'present_ratio'):.1f}%")
    small = groups["small"]
    if small:
        print(f"小动样本 {len(small)} 段: 平均有人召回 {avg(small, 'present_ratio'):.1f}%")


if __name__ == "__main__":
    main()

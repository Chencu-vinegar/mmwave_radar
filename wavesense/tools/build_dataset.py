"""从原始录制文件切出纯段并保存为带标签的数据集。

数据集目录：
  dataset/empty/  空房（人离开，Detector 判无人）
  dataset/rest/   静坐 / 静立（有人，只有呼吸）
  dataset/small/  小动（有人，手指 / 轻微转头）
  dataset/large/  大动（有人，挥手 / 起身 / 走动）
  dataset/manifest.json  每段的来源、帧范围、时长、能量

切分规则：
  1. 先用 Detector 得到每帧 presence（稀疏尖峰逻辑，已独立验证）作为"有人/无人"掩码；
  2. empty 只在无人区域切，rest/small/large 只在有人区域切；
  3. 区域内再按动作门短窗能量分级，只保留与标称场景一致的段。
这样可避免把"静坐（动作门低）"误标成"空房"，也能剔除离开/进入的延迟段。
"""
import gzip
import json
import struct
from pathlib import Path

import numpy as np

from src.config import load_config
from src.data_source.frame import N_PAYLOAD

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "dataset"
DIAG = ROOT.parent / ".diag"
HEAD = b"ICLH"
TAIL = b"ICLT"
DATA_LEN = 40

# 标称场景：empty / rest / small / large
FILES = {
    # 授权软件
    "RadarData_2026_10_20_10_16_59.gz": ("rest", "samples"),
    "RadarData_2026_10_20_10_17_19.gz": ("small", "samples"),
    "RadarData_2026_10_20_10_17_34.gz": ("large", "samples"),
    "RadarData_2026_10_20_10_30_12.gz": ("rest", "samples"),
    "RadarData_2026_10_20_10_30_23.gz": ("small", "samples"),
    "RadarData_2026_10_20_10_30_37.gz": ("large", "samples"),
    "RadarData_2026_10_20_10_50_18.gz": ("rest", "samples"),
    "RadarData_2026_10_20_10_50_28.gz": ("small", "samples"),
    "RadarData_2026_10_20_10_50_41.gz": ("large", "samples"),
    "RadarData_2026_10_20_10_51_18.gz": ("rest", "samples"),
    "RadarData_2026_10_20_10_51_25.gz": ("small", "samples"),
    "RadarData_2026_10_20_10_51_38.gz": ("large", "samples"),
    "RadarData_2026_10_20_10_56_14.gz": ("rest", "samples"),
    "RadarData_2026_10_20_11_06_19.gz": ("rest", "samples"),
    "RadarData_2026_10_20_11_07_21.gz": ("rest", "samples"),
    "RadarData_2026_10_20_11_21_31.gz": ("empty", "samples"),
    "RadarData_2026_10_20_11_22_41.gz": ("empty", "samples"),
    # 直接串口
    "direct_serial_empty.gz": ("empty", "samples"),
    "direct_serial_sit_1711.gz": ("rest", "samples"),
    "direct_serial_sit_1732.gz": ("rest", "samples"),
    "direct_serial_sit_phone.gz": ("rest", "samples"),
    # 10月29日 窄房间（宽1.5-2m，深4m）
    "RadarData_2026_10_29_17_19_21.gz": ("empty", "samples"),
    "RadarData_2026_10_29_17_20_40.gz": ("empty", "samples"),
    "RadarData_2026_10_29_17_51_42.gz": ("rest", "samples"),
    "RadarData_2026_10_29_18_17_16.gz": ("rest", "samples"),
    "RadarData_2026_10_29_18_29_28.gz": ("large", "samples"),
    "RadarData_2026_10_29_18_30_59.gz": ("large", "samples"),
    "RadarData_2026_10_29_18_32_05.gz": ("large", "samples"),
    "RadarData_2026_10_29_18_34_01.gz": ("large", "samples"),
    # .dat（dat_empty 被饮水机污染，仍尝试切分但不会落入 empty）
    "dat_empty.dat": ("empty", "diag"),
    "dat_sit.dat": ("rest", "diag"),
    "dat_walk.dat": ("large", "diag"),
}


def read_bytes(path):
    if path.suffix == ".gz":
        return gzip.open(path, "rb").read()
    return path.read_bytes()


def parse_with_bytes(raw):
    """返回 [(motion_energy, frame_bytes)]，frame_bytes 为该逻辑帧的原始字节。"""
    out = []
    pending = {}
    i = 0
    while True:
        h = raw.find(HEAD, i)
        if h < 0:
            break
        if h + 8 > len(raw):
            break
        field = struct.unpack("<I", raw[h + 4:h + 8])[0]
        dlen = field & 0xFFFF
        flag = (field >> 24) & 0xFF
        total = 8 + dlen + 4
        if h + total > len(raw):
            break
        if dlen == DATA_LEN and flag in (0, 1):
            payload = raw[h + 8:h + 8 + dlen]
            tail = raw[h + 8 + dlen:h + total]
            i = h + total
            if tail != TAIL:
                continue
            arr = np.array(struct.unpack(f"<{N_PAYLOAD}h", payload[4:4 + 2 * N_PAYLOAD]),
                           dtype=np.int16)
            sub = raw[h:h + total]
            pending[flag] = (arr, sub)
            if 0 in pending and 1 in pending:
                a0, b0 = pending[0]
                a1, b1 = pending[1]
                mot = float(np.sum(a0[6:16].astype(np.float64) ** 2)
                            + np.sum(a1[6:16].astype(np.float64) ** 2))
                out.append((mot, b0 + b1))
                pending = {}
        elif dlen == 4:
            i = h + total
        else:
            i = h + 4
    return out


def smooth_labels(ms, rest_th, large_th, win):
    cls = np.where(ms >= large_th, 2, np.where(ms >= rest_th, 1, 0))
    half = win // 2
    n = len(cls)
    idx = np.arange(n)
    lo = np.clip(idx - half, 0, n)
    hi = np.clip(idx - half + win, 0, n)
    counts = []
    for c in (0, 1, 2):
        cum = np.concatenate([[0], np.cumsum(cls == c)])
        counts.append(cum[hi] - cum[lo])
    return np.argmax(np.stack(counts), axis=0)


def main():
    from src.data_source import FileReplaySource
    from src.detector import Detector
    from tools.segment import REST_TH, LARGE_TH, SMOOTH, MIN_SEG, WIN

    for d in ("empty", "rest", "small", "large"):
        (DATASET / d).mkdir(parents=True, exist_ok=True)

    manifest = []
    counters = {"empty": 0, "rest": 0, "small": 0, "large": 0}
    want_level = {"empty": 0, "rest": 0, "small": 1, "large": 2}

    for fname, (scene, loc) in FILES.items():
        base = DIAG if loc == "diag" else ROOT / "samples"
        path = base / fname
        if not path.exists():
            print(f"跳过（不存在）：{fname}")
            continue

        # Detector 跑 presence（帧级）
        det = Detector(load_config())
        pres = np.array([det.update(fr).presence for fr in FileReplaySource(str(path))])

        parsed = parse_with_bytes(read_bytes(path))
        if len(parsed) < MIN_SEG or len(parsed) != len(pres):
            print(f"跳过（帧太少或不一致）：{fname}")
            continue

        mot = np.array([p[0] for p in parsed])
        ms = np.array([mot[max(0, k - WIN + 1):k + 1].mean() for k in range(len(mot))])
        smooth = smooth_labels(ms, REST_TH, LARGE_TH, SMOOTH)

        # 区域掩码：empty 取无人，其余取有人；区域外用 -1
        region = (~pres) if scene == "empty" else pres
        labels = np.where(region, smooth, -1)
        target = want_level[scene]

        i = 0
        while i < len(labels):
            if labels[i] != target:
                i += 1
                continue
            j = i + 1
            while j < len(labels) and labels[j] == target:
                j += 1
            if j - i >= MIN_SEG:
                med = float(np.median(ms[i:j]))
                idx = counters[scene]
                counters[scene] += 1
                out_name = f"{scene}_{idx:03d}.gz"
                blob = b"".join(parsed[k][1] for k in range(i, j))
                with gzip.open(DATASET / scene / out_name, "wb") as f:
                    f.write(blob)
                manifest.append({
                    "file": f"{scene}/{out_name}",
                    "scene": scene,
                    "source": fname,
                    "frame_start": int(i),
                    "frame_end": int(j),
                    "frames": int(j - i),
                    "duration_sec": round((j - i) / 480, 2),
                    "motion_median": med,
                })
            i = j
        print(f"{fname:38s} -> {scene}，累计 {counters[scene]} 段")

    with open(DATASET / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print("\n各场景段数：", counters)
    print("总段数：", len(manifest))


if __name__ == "__main__":
    main()

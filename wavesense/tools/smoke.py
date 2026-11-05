"""离线全链路冒烟：一键验证所有模块、合成场景与真实回放。

运行：PYTHONPATH=. python tools/smoke.py
"""
import gzip
import struct

import numpy as np

from src.config import load_config
from src.data_source import DataSource, FileReplaySource, Frame, SimulatedSource
from src.detector import MOTION_LABELS, Detector
from src.mqtt_publisher import MqttPublisher  # noqa: F401

cfg = load_config()
ok = True


def check(label, cond, detail=""):
    global ok
    mark = "PASS" if cond else "FAIL"
    if not cond:
        ok = False
    print(f"[{mark}] {label} {detail}")


# 1. 合成：空房应始终无人
det = Detector(cfg)
final = [det.update(f) for f in SimulatedSource("empty", 600)][-1]
check("空房场景保持无人", not final.presence)

# 2. 合成：小动进入并分级
det = Detector(cfg)
final = [det.update(f) for f in SimulatedSource("small", 500)][-1]
check("小动场景进入", final.presence and final.motion_level == 1,
      f"（分级 {final.motion_label}）")

# 3. 合成：大动进入并分级
det = Detector(cfg)
final = [det.update(f) for f in SimulatedSource("large", 500)][-1]
check("大动场景进入", final.presence and final.motion_level == 2,
      f"（分级 {final.motion_label}）")

# 4. 合成剧情：应经历 无人→有人→无人，且静坐段保持
det = Detector(cfg)
pres = [det.update(f).presence for f in SimulatedSource("demo")]
check("demo 进入后能离开", pres[0] is False and True in pres and pres[-1] is False)
# 静坐段（帧 800~2300）不应整段掉人
sit_seg = pres[800:2300]
check("demo 静坐段保持有人", sum(sit_seg) / len(sit_seg) > 0.95,
      f"（{sum(sit_seg)/len(sit_seg)*100:.1f}% 有人）")

# 5. 真实文件回放：扫描工程 samples/ 下的 .gz（跨平台，不依赖固定路径）
from pathlib import Path
samples_dir = Path(__file__).resolve().parent.parent / "samples"
paths = sorted(samples_dir.glob("*.gz"))
if paths:
    src = FileReplaySource(str(paths[0]))
    frames = []
    for i, f in enumerate(src):
        frames.append(f)
        if i >= 400:
            break
    check("真实 .gz 解析出 Frame",
          len(frames) > 400 and frames[0].ch0.shape == (18,),
          f"（{paths[0].name}，{len(frames)} 帧）")
else:
    check("真实 .gz 解析出 Frame", False, "（samples/ 下没有 .gz 文件）")

print("\n冒烟结果：" + ("全部通过 ✅" if ok else "存在失败 ❌"))

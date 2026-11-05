# -*- coding: utf-8 -*-
"""用真实串口流录制验证新 SerialSource 的拼帧逻辑（离线，不需要板子）。"""
import struct
import sys

sys.path.insert(0, ".")
from src.data_source.dat_parser import CHIP_HEAD_MARK, RadarFrameGrouper, decode_chip_frame

path = sys.argv[1]
raw = open(path, "rb").read()

grouper = RadarFrameGrouper(8)
buf = b""
frames = []
resync = 0
synced = False
CHUNK = 256
for off in range(0, len(raw), CHUNK):
    buf += raw[off:off + CHUNK]
    while True:
        if not synced:
            i = 0
            while i <= len(buf) - 4 and not (buf[i] == CHIP_HEAD_MARK and buf[i + 3] == 0x09):
                i += 1
                resync += 1
            if i > 0:
                buf = buf[i:]
            if len(buf) < 40:
                break
            synced = True
        if len(buf) < 40:
            break
        if buf[0] != CHIP_HEAD_MARK or buf[3] != 0x09:
            synced = False
            continue
        d = decode_chip_frame(buf[:40], 8)
        if d:
            p = grouper.add(*d)
            if p:
                frames.append(p)
        buf = buf[40:]

print(f"流式拼帧得到 {len(frames)} 个雷达帧, resync={resync}")
if frames:
    f = frames[0]
    print(f"首帧 8 门平均功率: {[round(x,1) for x in f]}")
    f = frames[-1]
    print(f"末帧 8 门平均功率: {[round(x,1) for x in f]}")

# -*- coding: utf-8 -*-
"""dat_parser.py —— 授权软件导出 DAT 的二进制解析（B2 回放共用）。

帧格式（依据手册 §4.2 MCU 封装 + §4.1 芯片数据，2026-10 解析实测确认）：
  MCU 帧：ICLH(4B) | 长度 LE(2B) | 类型(1B) | 扩展/通道(1B) | 数据段 | ICLT(4B)
  数据段内：芯片头(4B, 大端, 0xAA 开头) | 8 个复距离门(I, Q 各 int16, 交织) | 校验帧尾(4B)
  一个雷达帧 = 64 个 chirp × 2 接收通道；每门功率 = 各 chirp/通道 I²+Q² 的平均值。

本模块只负责“字节 → 每门平均功率”，不做判定、不做底噪扣除；
底噪扣除和直流门剔除在数据源（replay）里做。
"""

import struct
from pathlib import Path

# ---------- 帧格式常量（来自手册/实测，不随意改） ----------
MCU_HEAD = b"ICLH"          # MCU 打包帧头（0x49 0x43 0x4C 0x48）
MCU_TAIL = b"ICLT"          # MCU 打包帧尾（0x49 0x43 0x4C 0x54）
CHIP_HEAD_MARK = 0xAA       # 芯片头最高字节固定 0xAA
TYPE_CHIP = 0x00            # MCU 类型：芯片数据（其他类型跳过）
CHIRPS_PER_FRAME = 64       # 每个雷达帧的 chirp 数（实测头字段 chirp 0~63 循环）


def decode_chip_frame(data: bytes, num_gates: int):
    """解一个芯片数据段。

    返回 (rx, chirp, [(I0,Q0), …, (I7,Q7)])；格式不符返回 None。
    芯片头大端 32 位：0xAA | RX | 类型 | chirp 计数 | dw_len。
    """
    need = 4 + 2 * num_gates * 2  # 芯片头 4B + 每门 I/Q 各 2B
    if len(data) < need:
        return None
    head = struct.unpack_from(">I", data, 0)[0]
    if (head >> 24) & 0xFF != CHIP_HEAD_MARK:
        return None
    rx = (head >> 23) & 1
    chirp = ((head >> 12) & 0xFF) | (((head >> 11) & 1) << 8)
    iq = struct.unpack_from("<" + "h" * (2 * num_gates), data, 4)
    gates = [(iq[2 * g], iq[2 * g + 1]) for g in range(num_gates)]
    return rx, chirp, gates


class RadarFrameGrouper:
    """把逐芯片数据段（chirp × 通道）聚成一个雷达帧。

    文件回放和实时串口共用：串口流也是同样的 MCU 帧格式，只是数据来源不同。
    """

    def __init__(self, num_gates: int):
        self.num_gates = num_gates
        self._pending: dict[int, dict[int, list]] = {}
        self.chirp_seen = 0
        self.channel_seen = 0

    def add(self, rx: int, chirp: int, gates: list):
        """喂入一个芯片数据段，攒满一个雷达帧时返回每门平均功率，否则 None。"""
        if chirp == 0 and self._pending and any(k != 0 for k in self._pending):
            power = self._flush()
            self._pending = {}
            return power
        self._pending.setdefault(chirp, {})[rx] = gates
        return None

    def flush(self):
        """文件/流末尾吐出最后一帧（可能不完整）。"""
        power = self._flush()
        self._pending = {}
        return power

    def _flush(self):
        if not self._pending:
            return None
        gate_sum = [0.0] * self.num_gates
        gate_cnt = [0] * self.num_gates
        for chans in self._pending.values():
            for g in chans.values():
                self.channel_seen += 1
                for gi in range(self.num_gates):
                    i, q = g[gi]
                    gate_sum[gi] += i * i + q * q
                    gate_cnt[gi] += 1
        self.chirp_seen = len(self._pending)
        return [gate_sum[gi] / gate_cnt[gi] for gi in range(self.num_gates)]


def iter_radar_frames(path: str | Path, num_gates: int):
    """把 64 个 chirp × 2 通道聚成一个雷达帧，逐帧产出每门平均功率。

    产出: (power[门数], chirp 覆盖数, 通道样本数)
    """
    raw = open(path, "rb").read()
    grouper = RadarFrameGrouper(num_gates)
    i = 0
    while i < len(raw) - 12:
        if raw[i:i + 4] != MCU_HEAD:
            i += 1
            continue
        length = struct.unpack_from("<H", raw, i + 4)[0]
        frame_type = raw[i + 6]
        data = raw[i + 8:i + 8 + length]
        if raw[i + 8 + length:i + 12 + length] != MCU_TAIL:
            i += 1
            continue
        if frame_type == TYPE_CHIP:
            decoded = decode_chip_frame(data, num_gates)
            if decoded is not None:
                power = grouper.add(*decoded)
                if power is not None:
                    yield power, grouper.chirp_seen, grouper.channel_seen
        i += 12 + length

    power = grouper.flush()  # 文件末尾最后一帧
    if power is not None:
        yield power, grouper.chirp_seen, grouper.channel_seen

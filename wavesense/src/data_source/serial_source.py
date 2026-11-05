# -*- coding: utf-8 -*-
"""serial_source.py —— 实时串口数据源（B2 v1）。

实机对拍结论（2026-11-03 serial_capture.bin）：
板载裸串口发的不是 DAT 那种 ICLH/ICLT 包装帧，而是一串定长芯片帧：
  [AA rx_type_chirp_dwlen 4B 大端] + [8 门 I/Q int16 小端 32B] + [4B 尾] = 40B 定长
每 128 个芯片帧（64 chirp × RX0/RX1）= 一个雷达帧；chirp==0 表示新帧开始。
ICLH/ICLT 是授权软件导出 DAT 时加的外壳，实时流里不存在。

本模块：读串口 → 字节缓冲 → 找到第一个 AA 芯片头后按 40 字节定长切 → 聚成雷达帧 → Frame。
"""

import math
import struct
from collections.abc import Iterator

from src.data_source.base import DataSource
from src.data_source.dat_parser import (
    CHIP_HEAD_MARK, RadarFrameGrouper, decode_chip_frame,
)
from src.data_source.frame import Frame

_CHIP_RECORD = 40  # 每个芯片帧定长 40 字节（实测 gaps 恒为 40）


class SerialSource(DataSource):
    """实时串口数据源。

    参数:
        port:        串口号，如 COM5
        baud:        波特率（实测 921600）
        num_gates:   距离门数（config sim_num_gates=8）
        fps:         给 Frame 打时间戳用（B0 实测 10）
        max_frames:  最多解析多少雷达帧后自动停止（None=一直读到 Ctrl+C）
        exclude_dc_bin0: 门 0 直流自杂波置 0
    """

    def __init__(self, port: str, baud: int, num_gates: int, fps: float = 10.0,
                 max_frames: int | None = None, exclude_dc_bin0: bool = True,
                 bg_db: list[float] | None = None):
        self.port = port
        self.baud = baud
        self.num_gates = num_gates
        self.fps = fps
        self.max_frames = max_frames
        self.exclude_dc_bin0 = exclude_dc_bin0
        self.bg_db = bg_db      # 空房每门 dB 底噪；None=不扣除，直接输出原始 dB
        self.resync_count = 0    # 错位重同步次数（理想=0）
        self.bytes_received = 0  # 累计收到字节数（诊断用）

    def iter_frames(self) -> Iterator[Frame]:
        import serial  # pyserial，延迟 import，离线流程不依赖

        ser = serial.Serial(
            self.port, self.baud,
            bytesize=8, parity="N", stopbits=1, timeout=0.1,
        )
        ser.reset_input_buffer()
        buf = b""
        grouper = RadarFrameGrouper(self.num_gates)
        frame_no = 0
        synced = False
        try:
            while True:
                chunk = ser.read(4096)
                if chunk:
                    self.bytes_received += len(chunk)
                    buf += chunk
                while True:
                    if not synced:
                        # 缓冲里找第一个合法芯片头（AA 开头、第 4 字节=0x09）
                        i = 0
                        while i <= len(buf) - 4 and not (
                                buf[i] == CHIP_HEAD_MARK and buf[i + 3] == 0x09):
                            i += 1
                            self.resync_count += 1
                        if i > 0:
                            buf = buf[i:]
                        if len(buf) < _CHIP_RECORD:
                            break  # 不够一整帧，等下一块
                        synced = True
                    # 按定长 40 字节切
                    if len(buf) < _CHIP_RECORD:
                        break
                    if buf[0] != CHIP_HEAD_MARK or buf[3] != 0x09:
                        synced = False  # 失步，回去重新找头
                        continue
                    decoded = decode_chip_frame(buf[:_CHIP_RECORD], self.num_gates)
                    if decoded is not None:
                        power = grouper.add(*decoded)
                        if power is not None:
                            frame_no += 1
                            yield self._to_frame(frame_no, power)
                            if self.max_frames and frame_no >= self.max_frames:
                                return
                    buf = buf[_CHIP_RECORD:]
        finally:
            ser.close()

    def _to_frame(self, frame_no: int, power: list[float]) -> Frame:
        """每门平均功率 → dB；扣空房底噪（与 replay 同口径），可选剔除门 0。"""
        db = [10 * math.log10(max(p, 1e-9)) for p in power]
        if self.bg_db is not None:
            db = [max(0.0, db[g] - self.bg_db[g]) for g in range(self.num_gates)]
        if self.exclude_dc_bin0:
            db[0] = 0.0
        return Frame(
            timestamp=frame_no / self.fps,
            range_energy=db,
            targets=None,
            raw={"power": power},
        )

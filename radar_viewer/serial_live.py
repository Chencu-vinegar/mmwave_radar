# -*- coding: utf-8 -*-
"""
serial_live.py — DEV202H (ICL1122) 毫米波雷达 USB 串口实时读取与解析。

串口输出为连续字节流，真实帧格式为「55 AA」同步头的 40 字节裸帧
（实测：与 .dat 文件的 ICLH 封装不同，串口直读没有 ICLH/ICLT/PU 外壳）：

  帧(40B): 55 AA | ch(1B) | cnt_lo(1B) | cnt_hi(1B) | 数据(32B) | 尾(3B)
    ch      bit7=RX通道(0=RX0/1=RX1)，bit0-3=chirp序号高2位，bit4-5=0x30 固定
    cnt_lo  chirp序号低4位 ×16（0x00~0xF0，步进 0x10）
    cnt_hi  0x09（固定）
    数据    8 距离 bin 复数（大端 int16 I/Q 交替），与 ICLH 数据帧一致
    尾      3 字节（校验/固定），忽略

  完整 chirp 序号 = (ch & 0x0F) * 16 + (cnt_lo >> 4)，范围 0~63（=64 chirp/帧）。
  同序号的 RX0/RX1 配对成一个完整 chirp。

后台线程持续读取串口字节，做帧同步、增量配对（RX0/RX1），
并维护一个滚动 chirp 缓冲供 GUI 定时取用。
"""
import itertools
import struct
import time
import threading
from collections import deque

import numpy as np
import serial
import serial.tools.list_ports

import radar_parser as rp

DEFAULT_BAUDRATE = 921600
FRAME_HDR = b"\x55\xAA"
FRAME_LEN = 40
# 帧头后字节校验：ch 的 bit4-5 固定为 0x30、bit6=0；counter 高字节固定 0x09
_CH_MASK = 0x70
_CH_FIX = 0x30
_CNT_HI = 0x09


def list_serial_ports():
    """枚举可用串口，返回 [(device, description), ...]"""
    try:
        return [(p.device, p.description) for p in serial.tools.list_ports.comports()]
    except Exception:
        return []


class LiveChirpAssembler:
    """增量式 chirp 配对：同 counter 的 RX0/RX1 配对成一个完整 chirp。"""

    def __init__(self, chirps_per_frame=64):
        self.cpf = chirps_per_frame
        self.pending = {}
        self.wrap = 0
        self.prev_cnt = None

    def add(self, cnt, ch, cplx):
        if self.prev_cnt is not None and cnt < self.prev_cnt:
            self.wrap += 1
        self.prev_cnt = cnt
        ci = cnt // 16
        key = (self.wrap, ci)
        d = self.pending.get(key)
        if d is None:
            d = {"r0": None, "r1": None}
            self.pending[key] = d
        if ch == 0:
            d["r0"] = cplx
        else:
            d["r1"] = cplx
        if d["r0"] is not None and d["r1"] is not None:
            gch = self.wrap * self.cpf + ci
            del self.pending[key]
            return gch, d["r0"], d["r1"]
        return None


class SerialRadarReader:
    """后台线程读取串口字节流，帧同步、解析并维护滚动 chirp 缓冲。"""

    def __init__(self, port, baudrate=DEFAULT_BAUDRATE, config=None, max_chirps=64 * 300):
        self.port = port
        self.baudrate = baudrate
        self.config = config or rp.RadarConfig()
        self.max_chirps = max_chirps
        self.buf = bytearray()
        self.assembler = LiveChirpAssembler()
        self.lock = threading.Lock()
        self.recent = deque(maxlen=max_chirps)
        self.stats = {"chirps": 0, "frames": 0, "frame_items": 0, "bytes": 0, "errors": 0}
        self._last_frame = -1
        self.running = False
        self.ser = None
        self.thread = None
        self._rec_file = None
        self.recording = False

    def start(self):
        self.ser = serial.Serial(self.port, self.baudrate, timeout=0.05)
        self.running = True
        self.thread = threading.Thread(target=self._read_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread is not None:
            self.thread.join(timeout=1.0)
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
        self.stop_recording()

    def start_recording(self, path):
        with self.lock:
            if self._rec_file is not None:
                try:
                    self._rec_file.close()
                except Exception:
                    pass
            self._rec_file = open(path, "wb")
            self.recording = True

    def stop_recording(self):
        with self.lock:
            if self._rec_file is not None:
                try:
                    self._rec_file.close()
                except Exception:
                    pass
                self._rec_file = None
            self.recording = False

    def reset(self):
        with self.lock:
            self.buf.clear()
            self.recent.clear()
            self.assembler = LiveChirpAssembler()
            self.stats = {"chirps": 0, "frames": 0, "frame_items": 0, "bytes": 0, "errors": 0}
            self._last_frame = -1

    def _read_loop(self):
        while self.running:
            try:
                n = self.ser.in_waiting
                if n > 0:
                    self.feed(self.ser.read(n))
                else:
                    time.sleep(0.001)
            except serial.SerialException:
                self.running = False
                break
            except Exception:
                time.sleep(0.01)

    def feed(self, data):
        with self.lock:
            if self._rec_file is not None:
                self._rec_file.write(data)
            self.buf.extend(data)
            self.stats["bytes"] += len(data)
            self._drain()

    def _drain(self):
        while True:
            idx = self.buf.find(FRAME_HDR)
            if idx < 0:
                # 找不到帧头：仅保留末尾 1 字节（可能是半个 55 AA 头）
                if len(self.buf) > 1:
                    del self.buf[: len(self.buf) - 1]
                return
            if idx > 0:
                del self.buf[:idx]
            # 帧头后至少要有 5 字节（ch + cnt_lo + cnt_hi + 校验位）再做校验
            if len(self.buf) < 5:
                return
            # 严格校验，避免把数据区里偶然出现的 55 AA 误当帧头
            ch = self.buf[2]
            if (ch & _CH_MASK) != _CH_FIX or self.buf[4] != _CNT_HI:
                del self.buf[:2]
                continue
            if len(self.buf) < FRAME_LEN:
                return
            frame = bytes(self.buf[:FRAME_LEN])
            del self.buf[:FRAME_LEN]
            self._parse_frame(frame)

    def _parse_frame(self, frame):
        self.stats["frame_items"] += 1
        try:
            chb = frame[2]
            rx = (chb >> 7) & 1  # 0=RX0, 1=RX1
            chirp = (chb & 0x0F) * 16 + (frame[3] >> 4)  # 0~63
            cnt = chirp * 16  # 与 ICLH counter 对齐（每 chirp +16）
            vals = [struct.unpack(">h", frame[5 + 2 * k:7 + 2 * k])[0] for k in range(16)]
            cplx = np.array([complex(vals[2 * k], vals[2 * k + 1]) for k in range(self.config.n_bin)],
                            dtype=np.complex128)
        except Exception:
            self.stats["errors"] += 1
            return
        res = self.assembler.add(cnt, rx, cplx)
        if res is not None:
            gch, r0, r1 = res
            self.recent.append((gch, r0, r1))
            self.stats["chirps"] += 1
            fr = gch // self.assembler.cpf
            if fr != self._last_frame:
                self._last_frame = fr
                self.stats["frames"] += 1

    def snapshot(self, n=None):
        """返回滚动缓冲快照；n 不为 None 时只取最近 n 个 chirp，降低复制与后续处理开销。"""
        with self.lock:
            if n is None or n >= len(self.recent):
                items = list(self.recent)
            else:
                items = list(itertools.islice(self.recent, len(self.recent) - n, None))
            return items, dict(self.stats)

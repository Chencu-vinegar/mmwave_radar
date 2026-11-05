"""流式二进制帧解析器（回放与实时串口共用）。

用法：不断把字节块喂给 feed()，每次返回本次能解出的 Frame 列表。
内部维护缓冲区，自动处理粘包 / 分包：
  数据帧 52 字节：ICLH + field(低16=40, 最高字节=通道0/1) + 40 + ICLT；
  控制帧 16 字节：跳过。
ch0/ch1 凑成一对产出一个逻辑帧（104 字节）。
"""
import struct

import numpy as np

from .frame import Frame, N_PAYLOAD

HEAD = b"ICLH"
TAIL = b"ICLT"
DATA_LEN = 40


class StreamParser:
    def __init__(self):
        self.buf = b""
        self.pending = {}
        self.index = 0

    def feed(self, data):
        self.buf += data
        frames = []

        while True:
            i = self.buf.find(HEAD)
            if i < 0:
                # 保留最后 3 字节，防止帧头跨块
                self.buf = self.buf[-3:]
                break
            self.buf = self.buf[i:]

            if len(self.buf) < 8:
                break
            field = struct.unpack("<I", self.buf[4:8])[0]
            dlen = field & 0xFFFF
            flag = (field >> 24) & 0xFF
            total = 8 + dlen + 4

            if dlen == DATA_LEN and flag in (0, 1):
                if len(self.buf) < total:
                    break
                payload = self.buf[8:8 + dlen]
                tail = self.buf[8 + dlen:8 + dlen + 4]
                self.buf = self.buf[total:]
                if tail != TAIL:
                    continue
                counter = struct.unpack("<I", payload[0:4])[0]
                arr = np.array(struct.unpack(f"<{N_PAYLOAD}h", payload[4:4 + 2 * N_PAYLOAD]),
                               dtype=np.int16)
                self.pending[flag] = (counter, arr)
                if 0 in self.pending and 1 in self.pending:
                    c0, p0 = self.pending[0]
                    c1, p1 = self.pending[1]
                    frames.append(Frame(index=self.index, counter=c0, ch0=p0, ch1=p1))
                    self.index += 1
                    self.pending = {}
            elif dlen == 4:
                # 控制帧 16 字节
                if len(self.buf) < total:
                    break
                self.buf = self.buf[total:]
            else:
                # 异常长度：丢弃这个帧头，重新对齐，避免死循环
                self.buf = self.buf[4:]

        return frames

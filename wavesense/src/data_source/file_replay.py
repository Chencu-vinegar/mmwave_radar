"""文件回放数据源：解析录制的二进制流（.gz 压缩或 .dat 原始字节流）。

与实时串口共用 StreamParser，保证两条链路解析结果完全一致。
  .gz  gzip 压缩后的录制文件（授权软件导出）
  .dat 未压缩的原始串口字节流（直接录制）
"""
import gzip

from .base import DataSource
from .stream_parser import StreamParser


class FileReplaySource(DataSource):
    """从单个 .gz / .dat 文件回放 Frame。

    path  文件路径
    speed 回放速度（>0 按真实帧率睡眠；默认 0 尽快，用于离线分析）。
    """

    def __init__(self, path, speed=0.0):
        self.path = path
        self.speed = speed

    def frames(self):
        if self.path.lower().endswith(".gz"):
            with gzip.open(self.path, "rb") as f:
                raw = f.read()
        else:
            with open(self.path, "rb") as f:
                raw = f.read()
        parser = StreamParser()
        for frame in parser.feed(raw):
            yield frame

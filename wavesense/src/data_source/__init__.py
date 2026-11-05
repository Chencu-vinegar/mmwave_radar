"""数据源包：文件回放 / 实时串口 / 模拟。

统一产出 Frame，判定器只依赖 DataSource 接口。
"""
from .base import DataSource
from .file_replay import FileReplaySource
from .frame import Frame
from .serial_source import SerialSource, list_serial_ports
from .simulated import SimulatedSource
from .stream_parser import StreamParser

__all__ = [
    "DataSource",
    "FileReplaySource",
    "Frame",
    "SerialSource",
    "list_serial_ports",
    "SimulatedSource",
    "StreamParser",
]

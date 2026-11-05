# -*- coding: utf-8 -*-
"""data_source 包：统一帧结构 + 数据源接口。

对外只从这里导出，其他模块用 `from src.data_source import Frame, SimulatedSource`。
"""

from src.data_source.base import DataSource
from src.data_source.frame import Frame
from src.data_source.simulated import SCENES, SimulatedSource
from src.data_source.replay import FileReplaySource, compute_bg_db
from src.data_source.serial_source import SerialSource

__all__ = [
    "Frame", "DataSource", "SimulatedSource", "SCENES",
    "FileReplaySource", "compute_bg_db", "SerialSource",
]

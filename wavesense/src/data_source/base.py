# -*- coding: utf-8 -*-
"""base.py —— 数据源抽象基类。

所有数据源（模拟 / 文件回放 / 实时串口）都实现同一个接口 iter_frames()，
这样后面的 Detector 和 main 不用关心帧从哪里来。
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator

from src.data_source.frame import Frame


class DataSource(ABC):
    """数据源抽象基类：逐帧产出 Frame。"""

    @abstractmethod
    def iter_frames(self) -> Iterator[Frame]:
        """生成器：逐帧 yield Frame。具体数据源自己实现。"""
        raise NotImplementedError

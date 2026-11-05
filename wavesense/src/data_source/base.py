"""数据源抽象基类。

所有数据源（文件回放 / 实时串口 / 模拟）都产出统一的 Frame，
判定器只依赖这个接口，不关心数据来自哪里。
"""
from abc import ABC, abstractmethod


class DataSource(ABC):
    @abstractmethod
    def frames(self):
        """生成 Frame 的可迭代对象。"""
        raise NotImplementedError

    def __iter__(self):
        yield from self.frames()

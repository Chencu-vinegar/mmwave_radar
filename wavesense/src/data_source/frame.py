"""统一帧结构（基于 DEV202 真实二进制数据）。

一个逻辑帧 = 同一时刻两通道（ch0/ch1）的有效载荷，各 18 个 int16。
有效载荷布局（经实测核对）：
    位置 0-5   ：6 个距离门（静态目标也有能量）
    位置 6-15  ：10 个动作/速度门（不动时贴近底噪，动作时抬升）
    位置 16    ：参考字段
    位置 17    ：固定常量（非数据）
"""
from dataclasses import dataclass

import numpy as np

RANGE_POS = list(range(0, 6))
MOTION_POS = list(range(6, 16))
N_PAYLOAD = 18


@dataclass
class Frame:
    """单逻辑帧。

    index   逻辑帧序号（从 0 起）
    counter 板上计数器（ch0 原始计数字段，调试用）
    ch0/ch1 各为形状 (18,) 的 int16 数组
    """
    index: int
    counter: int
    ch0: np.ndarray
    ch1: np.ndarray

    @staticmethod
    def _zone_energy(ch, positions):
        return float(np.sum(ch[positions].astype(np.float64) ** 2))

    def range_energy(self) -> float:
        """距离门能量（位置 0-5，两通道合计）：用于存在判定。"""
        return self._zone_energy(self.ch0, RANGE_POS) + self._zone_energy(self.ch1, RANGE_POS)

    def motion_energy(self) -> float:
        """动作区能量（位置 6-15，两通道合计）：用于动作分级。"""
        return self._zone_energy(self.ch0, MOTION_POS) + self._zone_energy(self.ch1, MOTION_POS)

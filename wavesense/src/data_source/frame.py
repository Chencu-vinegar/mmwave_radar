# -*- coding: utf-8 -*-
"""frame.py —— 统一帧结构 Frame。

判定算法（detector）只认这个结构，不直接依赖授权软件导出格式或串口字节；
将来拿到真实格式，只需要在数据源里把原始数据“翻译”成 Frame。
"""

from dataclasses import dataclass, field


@dataclass
class Frame:
    """一帧雷达数据的统一中间结构。

    属性:
        timestamp:    帧时间戳（秒，float）。
        range_energy: 1DFFT 各距离门能量，长度 = 距离门数；索引即距离门序号。
        targets:      授权软件给的目标信息列表（距离/角度/速度/状态），没有就空列表。
        raw:          预留字典，存原始导出字段，方便排查，判定逻辑不读它。
    """

    timestamp: float
    range_energy: list[float]
    targets: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

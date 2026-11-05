"""合成数据源：无硬件时验证判定链路。

四种基础场景（距离门 level / 动作区 level，已按真实标定值反推 int16 幅度）：
  empty 空房  sit 静坐（带呼吸周期）  small 小动  large 大动
另提供 demo：一段完整剧情（空房→进门大动→静坐→小动→大动→离开空房），
能看到 presence 的完整翻转，并验证静坐呼吸间隙靠距离门保持。

能量换算：距离门能量 ≈ 12·level²，动作区能量 ≈ 20·level²（两通道合计）。
"""
import numpy as np

from .base import DataSource
from .frame import Frame

# 各场景（距离门基础 level，动作区基础 level）
# 距离门能量 ≈ 12·level²，动作区能量 ≈ 20·level²
# 新判定：active 距离门阈值 1.2e9；小动动作区 4e8~1.2e9；大动动作区 >1.2e9
SCENARIO_LEVELS = {
    "empty": (5500, 1600),    # 距离门约 3.6e8（不 active），动作区约 5e7
    "sit":   (13000, 2600),   # 距离门约 2.0e9（active），动作区约 1.4e8（呼吸周期）
    "small": (13000, 6300),   # 距离门约 2.0e9（active），动作区约 7.9e8（小动）
    "large": (13000, 13600),  # 距离门约 2.0e9（active），动作区约 3.7e9（大动）
}

# demo 剧情：(场景, 帧数)；结尾空房段需 > leave_gap(12000) 才能触发离开
DEMO_SCRIPT = [
    ("empty", 600),
    ("large", 200),    # 进门
    ("sit", 1500),     # 静坐（帧 800~2300）
    ("small", 300),
    ("large", 300),
    ("sit", 800),
    ("empty", 12500),  # 离开，足够长触发离开
]


class SimulatedSource(DataSource):
    def __init__(self, scenario="demo", n_frames=2000, seed=42):
        self.scenario = scenario
        self.n_frames = n_frames
        self.rng = np.random.default_rng(seed)

    def _make_frame(self, idx, range_level, motion_level, jitter=0.12):
        def channel():
            ch = np.zeros(18, dtype=np.int32)
            ch[0:6] = range_level * (1 + jitter * self.rng.standard_normal(6))
            ch[6:16] = motion_level * (1 + jitter * self.rng.standard_normal(10))
            ch[16] = 800 * self.rng.standard_normal()
            ch[17] = 2000
            return np.clip(ch, -32767, 32767).astype(np.int16)
        return Frame(index=idx, counter=idx, ch0=channel(), ch1=channel())

    def frames(self):
        idx = 0
        if self.scenario == "demo":
            for name, n in DEMO_SCRIPT:
                base_r, base_m = SCENARIO_LEVELS[name]
                for _ in range(n):
                    r, m = base_r, base_m
                    if name == "sit":
                        # 呼吸：动作区周期约 60 帧，最弱掉到 0.25 倍（呼吸间隙）
                        m = base_m * (0.62 + 0.38 * np.sin(2 * np.pi * idx / 60.0))
                    yield self._make_frame(idx, r, m)
                    idx += 1
        else:
            base_r, base_m = SCENARIO_LEVELS[self.scenario]
            for _ in range(self.n_frames):
                r, m = base_r, base_m
                if self.scenario == "sit":
                    m = base_m * (0.62 + 0.38 * np.sin(2 * np.pi * idx / 60.0))
                yield self._make_frame(idx, r, m)
                idx += 1

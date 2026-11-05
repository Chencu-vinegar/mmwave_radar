# -*- coding: utf-8 -*-
"""simulated.py —— 合成数据源 SimulatedSource（A2）。

无硬件时自己造“空房 / 走动 / 静坐”三种场景的帧序列，用来先把判定逻辑跑通。
注意：这里的能量曲线只是“看起来像”的占位合成，真实分布以 B1 实测样本为准。
"""

import math
import random
import time

from src.data_source.base import DataSource
from src.data_source.frame import Frame

# 支持的合成场景
SCENES = ("empty", "walk", "sit")

# 各场景的合成参数（这里是“造曲线”的造型参数，不是判定阈值，故留在代码里）
_FLOOR = 0.15          # 空房底噪水平（合成）
_NOISE_STD = 0.03      # 逐门随机波动幅度（合成）


class SimulatedSource(DataSource):
    """按配置生成合成帧序列。

    参数:
        config:      load_config() 读出的全局配置字典。
        scene:       覆盖配置里的 sim_scene（empty/walk/sit），不传则用配置。
        num_frames:  覆盖配置里的 sim_frames，不传则用配置。
        real_time:   True 时按 sim_frame_rate 节奏 sleep，模拟真实帧率；测试时传 False。
        seed:        随机种子，传固定值可复现同一序列。
    """

    def __init__(
        self,
        config: dict,
        scene: str | None = None,
        num_frames: int | None = None,
        real_time: bool = False,
        seed: int | None = None,
    ):
        self.scene = scene or config.get("sim_scene", "walk")
        if self.scene not in SCENES:
            raise ValueError(f"未知合成场景 {self.scene!r}，可选：{SCENES}")

        self.fps = float(config.get("sim_frame_rate", 10.0))
        self.num_gates = int(config.get("sim_num_gates", 16))
        # sim_frames=0 表示无限循环；CLI 覆盖优先
        self.num_frames = int(config.get("sim_frames", 60)) if num_frames is None else int(num_frames)
        self.real_time = real_time
        self._rng = random.Random(seed)

    def iter_frames(self):
        """逐帧生成 Frame；num_frames<=0 时无限循环。"""
        t = 0.0
        i = 0
        while self.num_frames <= 0 or i < self.num_frames:
            yield self._make_frame(i, t)
            i += 1
            t += 1.0 / self.fps
            if self.real_time:
                time.sleep(1.0 / self.fps)

    # ---------- 内部：造一帧 ----------

    def _make_frame(self, index: int, t: float) -> Frame:
        # 1) 先造全门的底噪 + 随机波动
        energy = [
            _FLOOR + self._rng.gauss(0, _NOISE_STD) for _ in range(self.num_gates)
        ]
        targets: list[dict] = []

        if self.scene == "walk":
            # 走动：一个目标门从近到远缓慢移动，能量峰值明显
            progress = index / max(self.num_frames - 1, 1)  # 0→1 的进度
            center = 2 + progress * (self.num_gates - 5)     # 目标门位置（连续值）
            self._add_bump(energy, center, amp=2.5, sigma=1.2)
            targets.append(
                {
                    "gate": round(center),
                    "distance_m": round(center * 0.15, 2),  # 占位换算，非真实测距
                    "speed_mps": 0.8,
                    "angle_deg": 0,
                    "state": "moving",
                }
            )
        elif self.scene == "sit":
            # 静坐：固定一个门，能量略高于底噪且非常稳定，只有呼吸式微小起伏
            center = 6
            breath = 1.0 + 0.1 * math.sin(2 * math.pi * index / (self.fps * 4))
            self._add_bump(energy, center, amp=0.55 * breath, sigma=0.8)
            targets.append(
                {
                    "gate": center,
                    "distance_m": round(center * 0.15, 2),
                    "speed_mps": 0.0,
                    "angle_deg": 0,
                    "state": "static",
                }
            )
        # empty：不加任何峰，只留底噪，targets 为空

        return Frame(
            timestamp=round(t, 3),
            range_energy=[round(e, 3) for e in energy],
            targets=targets,
            raw={"scene": self.scene, "synthetic": True},
        )

    def _add_bump(self, energy: list[float], center: float, amp: float, sigma: float) -> None:
        """在 center 附近叠一个高斯形状的能量峰，模拟人体反射。"""
        for g in range(len(energy)):
            energy[g] += amp * math.exp(-((g - center) ** 2) / (2 * sigma * sigma))

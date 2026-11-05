# -*- coding: utf-8 -*-
"""test_detector.py —— Detector 单元测试（A3 运动去抖 + A3b 静坐保持）。

运行（项目根目录）：
    python -m unittest discover -s tests -v
只用标准库 unittest，不引第三方测试框架。
"""

import random
import unittest

from src.data_source import Frame
from src.detector import Detector

# 测试用配置（独立于 config.yaml 的占位值，避免调占位值影响测试）
TEST_CFG = {
    # 运动
    "energy_threshold": 3.0,
    "noise_floor": 0.2,
    "debounce_frames_enter": 3,
    "debounce_frames_leave": 5,
    # 静坐（小参数便于快速触发）
    "still_energy_variance": 0.05,
    "still_persist_frames": 20,
    "still_window_frames": 30,
    "still_gate_min_energy": 0.5,
}

GATES = 16


def make_frame(energies: list[float], targets=None) -> Frame:
    """造一帧测试帧：targets 不传默认空列表；显式传 None 模拟“目标信息拿不到”。"""
    return Frame(timestamp=0.0, range_energy=energies, targets=targets)


def hot_frame() -> Frame:
    """高能量帧：超底噪能量之和远超阈值 → 运动有人候选。"""
    return make_frame([3.0] * GATES)


def cold_frame() -> Frame:
    """低能量帧：全部在底噪以下 → 无人候选。"""
    return make_frame([0.15] * GATES, targets=[])


def stable_sit_frame(seed_offset: int, gate: int = 6) -> Frame:
    """稳定静坐帧：固定门 6 维持 0.70，只有极小呼吸式波动；运动路径判不出。"""
    rng = random.Random(1000 + seed_offset)
    energies = [0.15 + rng.gauss(0, 0.005) for _ in range(GATES)]
    energies[gate] = 0.70 + rng.gauss(0, 0.005)
    return make_frame(energies, targets=[{"gate": gate, "state": "static"}])


def unstable_sit_frame(seed_offset: int, gate: int = 6) -> Frame:
    """抖动帧：门 6 能量大幅乱跳 → 方差大，静坐不应触发。"""
    rng = random.Random(2000 + seed_offset)
    energies = [0.15 + rng.gauss(0, 0.01) for _ in range(GATES)]
    energies[gate] = 0.70 + rng.gauss(0, 0.5)
    return make_frame(energies, targets=[])


class DetectorTest(unittest.TestCase):
    def setUp(self):
        self.det = Detector(TEST_CFG)

    # ---------- A3：运动检测 + 去抖 ----------

    def test_walk_flips_to_presence(self):
        """走动：连续 3 帧候选（=进入去抖帧数）后 presence 翻 1，且只在翻转帧 changed。"""
        results = [self.det.update(hot_frame()) for _ in range(3)]
        self.assertEqual(results[0]["presence"], 0)
        self.assertFalse(results[0]["changed"])
        self.assertEqual(results[1]["presence"], 0)
        self.assertEqual(results[2]["presence"], 1)
        self.assertTrue(results[2]["changed"])
        r4 = self.det.update(hot_frame())
        self.assertEqual(r4["presence"], 1)
        self.assertFalse(r4["changed"])

    def test_leave_flips_back(self):
        """离开：先进入有人，再连续 5 帧无人（=离开去抖帧数）后翻回 0。"""
        for _ in range(3):
            self.det.update(hot_frame())
        self.assertEqual(self.det.presence, 1)
        results = [self.det.update(cold_frame()) for _ in range(5)]
        self.assertEqual(results[3]["presence"], 1)
        self.assertEqual(results[4]["presence"], 0)
        self.assertTrue(results[4]["changed"])

    def test_short_streak_no_flip(self):
        """帧数不够：2 帧候选（<进入去抖 3）后转冷，presence 始终为 0。"""
        for _ in range(2):
            r = self.det.update(hot_frame())
            self.assertFalse(r["changed"])
        for _ in range(3):
            r = self.det.update(cold_frame())
            self.assertEqual(r["presence"], 0)
            self.assertFalse(r["changed"])

    def test_people_count_clamp_and_missing(self):
        """人数：取 targets 个数夹到 0/1/2；targets=None 返回 None 不硬猜。"""
        r = self.det.update(make_frame([3.0] * GATES, targets=[{}, {}, {}]))
        self.assertEqual(r["people_count"], 2)
        r = self.det.update(make_frame([3.0] * GATES, targets=[{}]))
        self.assertEqual(r["people_count"], 1)
        r = self.det.update(cold_frame())
        self.assertEqual(r["people_count"], 0)
        r = self.det.update(make_frame([3.0] * GATES, targets=None))
        self.assertIsNone(r["people_count"])

    def test_changed_only_on_flip(self):
        """changed 只在 presence 翻转那一帧为 True。"""
        flipped = [self.det.update(hot_frame())["changed"] for _ in range(3)]
        self.assertEqual(flipped, [False, False, True])

    # ---------- A3b：静坐保持 ----------

    def test_still_holds_presence_when_motion_cold(self):
        """静坐：运动路径判不出（冷帧），但稳定占用达到帧数后静坐把 presence 保持为 1。"""
        results = [self.det.update(stable_sit_frame(i)) for i in range(60)]
        self.assertEqual(results[-1]["presence"], 1)
        # 触发那一帧 changed=True，之后保持不再变
        self.assertTrue(any(r["changed"] for r in results))
        self.assertFalse(results[-1]["changed"])

    def test_still_not_triggered_in_empty(self):
        """空房：能量稳定但没有门持续占用，静坐不触发，presence 保持 0。"""
        for i in range(60):
            r = self.det.update(cold_frame())
            self.assertEqual(r["presence"], 0)
            self.assertFalse(r["changed"])

    def test_still_not_triggered_when_unstable(self):
        """抖动：有门高能量但方差大（不稳定），静坐不触发。"""
        for i in range(60):
            r = self.det.update(unstable_sit_frame(i))
            self.assertEqual(r["presence"], 0)

    def test_10min_sit_never_drops_to_empty(self):
        """10 分钟量级（6000 帧 @10fps）稳定静坐：预热后 presence 全程为 0 之外不掉回 0。"""
        presences = []
        for i in range(6000):
            r = self.det.update(stable_sit_frame(i))
            presences.append(r["presence"])
        # 预热期（窗口+占用帧数）允许为 0，之后必须一直是 1
        warmup = TEST_CFG["still_window_frames"] + TEST_CFG["still_persist_frames"]
        self.assertTrue(all(p == 1 for p in presences[warmup:]))
        self.assertEqual(presences[-1], 1)


if __name__ == "__main__":
    unittest.main()

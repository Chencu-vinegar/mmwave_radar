"""Detector 与 Frame 的单元测试（合成帧，不依赖真实数据文件）。

直接串口的静坐是"周期性稀疏尖峰"，测试用小 leave_gap 快速验证：
  活动尖峰（距离门短窗超阈值）进入并重置离开计数；
  连续无活动超过 leave_gap 才离开。
"""
import unittest

import numpy as np

from src.data_source import Frame
from src.detector import Detector

# 测试配置：阈值与 config.yaml 一致，leave_gap 用小值（100）避免上万帧慢测试
CFG = {
    "detector": {
        "short_window": 30,
        "active_threshold": 1.2e9,
        "enter_frames": 5,
        "leave_gap_frames": 100,
        "small_move_threshold": 4.0e8,
        "large_move_threshold": 2.6e9,
        # 走动保持用小窗口（100 帧），强动作帧数阈值与 config 一致
        "walk_window": 100,
        "walk_strong_threshold": 2.0e9,
        "walk_strong_frames": 20,
    }
}


def make_frame(index, range_level, motion_level):
    """构造合成帧：距离门 0-5 给 range_level，动作区 6-15 给 motion_level。"""
    ch0 = np.zeros(18, dtype=np.int16)
    ch1 = np.zeros(18, dtype=np.int16)
    ch0[0:6] = range_level
    ch1[0:6] = range_level
    ch0[6:16] = motion_level
    ch1[6:16] = motion_level
    return Frame(index=index, counter=index, ch0=ch0, ch1=ch1)


def feed(det, n, range_level, motion_level):
    last = None
    for i in range(n):
        last = det.update(make_frame(i, range_level, motion_level))
    return last


class TestFrame(unittest.TestCase):
    def test_energy(self):
        f = make_frame(0, 1000, 500)
        # 距离门：2 通道 × 6 位置 × 1000^2
        self.assertEqual(f.range_energy(), 2 * 6 * 1000 ** 2)
        # 动作区：2 通道 × 10 位置 × 500^2
        self.assertEqual(f.motion_energy(), 2 * 10 * 500 ** 2)


class TestDetector(unittest.TestCase):
    def test_empty_keeps_unattended(self):
        det = Detector(CFG)
        d = feed(det, 200, 0, 0)
        self.assertFalse(d.presence)
        self.assertEqual(d.motion_level, 0)

    def test_enter_and_large_move(self):
        det = Detector(CFG)
        # range=12000 → 距离门 1.728e9（活动）；motion=12000 → 动作区 2.88e9（大动）
        d = feed(det, 50, 12000, 12000)
        self.assertTrue(d.presence)
        self.assertEqual(d.motion_level, 2)

    def test_small_move_level(self):
        det = Detector(CFG)
        # range=12000（活动进入）；motion=5000 → 动作区 5e8（小动）
        d = feed(det, 50, 12000, 5000)
        self.assertTrue(d.presence)
        self.assertEqual(d.motion_level, 1)

    def test_sparse_peak_keeps_presence(self):
        det = Detector(CFG)
        feed(det, 40, 12000, 12000)
        self.assertTrue(det.presence)
        # 稀疏尖峰：无活动段（gap 累积但未到 leave_gap）后跟一团尖峰（短窗重新 active、重置 gap）
        for _ in range(3):
            feed(det, 80, 0, 0)
            self.assertTrue(det.presence, "无活动段内误离开")
            feed(det, 35, 12000, 12000)
        self.assertTrue(det.presence)

    def test_leave_after_no_activity(self):
        det = Detector(CFG)
        feed(det, 40, 12000, 12000)
        self.assertTrue(det.presence)
        # 持续无活动：短窗滑出 + gap 超过 leave_gap，判无人
        d = feed(det, 200, 0, 0)
        self.assertFalse(d.presence)
        self.assertEqual(d.motion_level, 0)

    def test_walk_hold_keeps_large_in_blind_turn(self):
        det = Detector(CFG)
        # 走动：强动作（motion=12000，动作区 2.88e9 >= 强动作阈值）积累强动作帧
        feed(det, 40, 12000, 12000)
        self.assertTrue(det.presence)
        # 转身盲区：当下动作门回落到 0，但记忆窗口内强动作帧数仍足够，保持大动
        d = feed(det, 30, 12000, 0)
        self.assertTrue(d.presence)
        self.assertEqual(d.motion_level, 2)

    def test_sit_still_drops_after_strong_frames_leave(self):
        det = Detector(CFG)
        feed(det, 40, 12000, 12000)
        self.assertTrue(det.presence)
        # 静坐：强动作帧从记忆窗口滑出，当下动作门低，判不动（但仍有人）
        d = feed(det, 100, 12000, 0)
        self.assertTrue(d.presence)
        self.assertEqual(d.motion_level, 0)


if __name__ == "__main__":
    unittest.main()

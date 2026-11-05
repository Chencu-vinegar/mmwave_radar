# -*- coding: utf-8 -*-
"""detector.py —— 存在判定核心。

A3：运动检测 + 进/出去抖；A3b：叠加静坐判定。
纯逻辑：不读串口、不发网络，输入统一 Frame，输出判定结果，方便单元测试。
所有阈值从 config 读，不在代码里硬编码。

合并规则：最终 presence = 运动有人 OR 静坐有人；静坐期间不得翻成 0。
"""

from collections import deque
from statistics import pvariance

from src.data_source import Frame


class Detector:
    """存在判定状态机（运动去抖 + 静坐保持）。

    每帧调用 update(frame)；阈值全部来自构造时传入的 config 字典。
    """

    def __init__(self, config: dict):
        # —— 运动检测阈值（A3）——
        self.energy_threshold = float(config["energy_threshold"])
        self.noise_floor = float(config.get("noise_floor", 0.2))
        self.debounce_enter = int(config["debounce_frames_enter"])
        self.debounce_leave = int(config["debounce_frames_leave"])

        # —— 静坐判定阈值（A3b）——
        self.still_energy_variance = float(config["still_energy_variance"])
        self.still_persist = int(config["still_persist_frames"])
        self.still_window = int(config["still_window_frames"])
        self.still_gate_min = float(config["still_gate_min_energy"])

        # —— 运动去抖内部状态 ——
        self._motion_presence = 0   # 运动路径自己的有人状态
        self._enter_streak = 0       # 连续“有人候选”帧数（进入去抖）
        self._leave_streak = 0       # 连续“无人候选”帧数（离开去抖）

        # —— 静坐内部状态 ——
        self._window = deque(maxlen=self.still_window)  # 最近窗口的能量帧
        self._gate_streaks: dict[int, int] = {}          # 每个门连续占用帧数
        self._still_present = False

        # —— 对外输出的最终状态 ——
        self.presence = 0

    def update(self, frame: Frame) -> dict:
        """喂入一帧，返回本轮判定结果。

        返回:
            {"presence": 0|1, "people_count": 0|1|2|None, "changed": bool}
            changed=True 表示这一帧最终 presence 发生翻转（供日志/MQTT 使用）。
        """
        old_presence = self.presence

        motion = self._motion_step(self._is_candidate(frame))
        still = self._still_step(frame)

        # 合并：运动有人 或 静坐有人 → 最终有人；静坐期间不会被运动路径翻成 0
        self.presence = 1 if (motion or still) else 0
        changed = self.presence != old_presence

        return {
            "presence": self.presence,
            "people_count": self._count_people(frame),
            "changed": changed,
        }

    # ---------- 运动路径（A3） ----------

    def _motion_step(self, candidate: int) -> int:
        """运动去抖状态机，返回运动路径的有人状态 0/1。"""
        if self._motion_presence == 0:
            if candidate:
                self._enter_streak += 1
                self._leave_streak = 0
                if self._enter_streak >= self.debounce_enter:
                    self._motion_presence = 1
            else:
                self._enter_streak = 0
        else:
            if candidate:
                self._leave_streak = 0
                self._enter_streak = 0
            else:
                self._leave_streak += 1
                self._enter_streak = 0
                if self._leave_streak >= self.debounce_leave:
                    self._motion_presence = 0
        return self._motion_presence

    def _is_candidate(self, frame: Frame) -> bool:
        """运动检测：超底噪能量之和大于 energy_threshold → 该帧为有人候选。"""
        excess = sum(max(0.0, e - self.noise_floor) for e in frame.range_energy)
        return excess > self.energy_threshold

    # ---------- 静坐路径（A3b） ----------

    def _still_step(self, frame: Frame) -> bool:
        """静坐判定：窗口内整体方差小（稳定）且某门持续占用达到 still_persist 帧。"""
        self._window.append(frame.range_energy)

        # 更新每个距离门的连续占用计数
        for gate, energy in enumerate(frame.range_energy):
            if energy > self.still_gate_min:
                self._gate_streaks[gate] = self._gate_streaks.get(gate, 0) + 1
            else:
                self._gate_streaks[gate] = 0

        # 窗口未满不判定，避免开头几帧误触发
        if len(self._window) < self._window.maxlen:
            self._still_present = False
            return False

        # 整体方差：把窗口内所有门能量摊平后算方差，小于阈值视为“能量稳定”
        flat = [e for row in self._window for e in row]
        stable = pvariance(flat) < self.still_energy_variance

        # 占用：有某个门连续占用达到 still_persist 帧
        occupied = any(streak >= self.still_persist for streak in self._gate_streaks.values())

        self._still_present = stable and occupied
        return self._still_present

    # ---------- 人数 ----------

    def _count_people(self, frame: Frame):
        """优先取目标信息个数，夹在 0/1/2；拿不到（targets=None）返回 None，不硬猜。"""
        # TODO(真实接入): targets=None 表示目标信息缺失，后续可结合能量估计，暂不猜
        if frame.targets is None:
            return None
        return max(0, min(2, len(frame.targets)))

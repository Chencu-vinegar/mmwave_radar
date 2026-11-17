"""存在与动作判定器（基于 DEV202 直接串口数据标定，2026-10-22）。

直接串口（不经过授权软件）时，静态人体的信号特征：
  人静坐时，距离门短窗不是连续高值，而是"周期性稀疏尖峰"——
  呼吸/身体调整每隔几秒到二十几秒触发一次明显尖峰（约 3-5e9）；
  尖峰之间，距离门短窗与空房无法区分（人在弱反射/角度盲区，
  甚至遮挡背景反射，能量可能更低）。

状态机：
  进入（无人→有人）：距离门短窗 > 活动阈值，连续 enter_frames（人进门/坐下有动作）。
  保持（有人）：      周期性活动尖峰不断重置离开计数。
  离开（有人→无人）：连续 leave_gap_frames 无活动尖峰（约 25 秒）才判无人。

实测（30 帧短窗，两通道合计，约 480 fps）：
  空房稳态   距离门短窗约 5-6e8，最大约 1.15e9；动作区约 9e7；
  静坐       活动尖峰约 3-5e9，尖峰间隔最长约 10852 帧（约 22s）；
  明显动作   动作区短窗约 1.5-2e9。
"""
from collections import deque
from dataclasses import dataclass

import numpy as np

MOTION_LABELS = ("不动", "小动", "大动")


@dataclass
class Detection:
    presence: bool
    motion_level: int            # 0 不动 / 1 小动 / 2 大动
    motion_label: str
    range_energy: float          # 本帧距离门
    short_range_energy: float    # 短窗距离门（活动判据）
    short_motion_energy: float   # 短窗动作区（动作分级）
    far_gate_ratio: float        # 短窗远门（门4-5）能量占比
    frames_since_active: int     # 距上次活动尖峰的帧数
    baseline_ready: bool         # 空房基线是否已学习完成
    baseline_range: float        # 基线距离门 p95（未学习为 None）
    range_lift: float            # 当前距离门相对长窗中位数的抬升倍数

    def as_dict(self):
        return {
            "presence": self.presence,
            "motion_level": self.motion_level,
            "motion_label": self.motion_label,
            "range_energy": self.range_energy,
            "short_range_energy": self.short_range_energy,
            "short_motion_energy": self.short_motion_energy,
            "far_gate_ratio": self.far_gate_ratio,
            "frames_since_active": self.frames_since_active,
            "baseline_ready": self.baseline_ready,
            "baseline_range": self.baseline_range,
            "range_lift": self.range_lift,
        }


class Detector:
    def __init__(self, cfg):
        d = cfg.get("detector", {})
        self.short_win = int(d.get("short_window", 30))

        # 活动判据：短窗距离门（空房最大约 1.15e9，静坐尖峰约 3-5e9）
        self.active_threshold = float(d.get("active_threshold", 1.2e9))
        self.enter_frames = int(d.get("enter_frames", 5))

        # 离开：连续无活动尖峰（静坐最长间隔约 10852，取 12000 留余量）
        self.leave_gap = int(d.get("leave_gap_frames", 12000))

        # 动作分级：短窗动作区（纯数据集标定，2026-10-23）
        self.small_threshold = float(d.get("small_move_threshold", 4.0e8))
        self.large_threshold = float(d.get("large_move_threshold", 2.6e9))

        # 近门电器过滤：短窗尖峰必须有一定比例能量传播到远门（门4-5）。
        # 近门电器（饮水机/风扇等）的尖峰只在近门，远门比例 <0.05；
        # 人呼吸的尖峰通过多径传播到所有门，远门比例通常 >=0.1（纯数据集标定）。
        self.far_gate_ratio = float(d.get("far_gate_ratio", 0.05))

        # 走动保持：来回走动时转身会进入角度盲区，当下动作门短暂回落。
        # 用较长记忆窗口统计"强动作帧数"，走动的强动作成簇出现，
        # 纯静坐的强动作只有孤立几帧（纯数据集标定，2026-10-30）。
        self.walk_win = int(d.get("walk_window", 3360))
        self.walk_strong_threshold = float(d.get("walk_strong_threshold", 2.0e9))
        self.walk_strong_frames = int(d.get("walk_strong_frames", 20))

        # 基线自适应：开机/学习期采集空房底噪，活动判据在绝对阈值之上
        # 叠加"相对基线抬升"条件，适应不同房间/档位的底噪水平。
        b = d.get("baseline", {}) if isinstance(d.get("baseline"), dict) else {}
        self.baseline_learn_frames = int(b.get("learn_frames", 0))
        self.baseline_ratio = float(b.get("ratio", 1.8))
        self.long_win = int(b.get("long_window", 300))

        self.short_gate_q = deque(maxlen=self.short_win)
        self.short_range_q = deque(maxlen=self.short_win)
        self.short_motion_q = deque(maxlen=self.short_win)
        self.walk_motion_q = deque(maxlen=self.walk_win)
        self.long_range_q = deque(maxlen=self.long_win)

        # 空房基线（p95）；None 表示尚未学习，相对条件退化为不约束
        self.baseline_range = None
        self.baseline_motion = None
        self._base_rng = []
        self._base_mot = []
        self._baseline_learning = self.baseline_learn_frames > 0
        self._baseline_target = self.baseline_learn_frames

        self.presence = False
        self.enter_cnt = 0
        self.gap = 0

    def reset(self):
        self.short_gate_q.clear()
        self.short_range_q.clear()
        self.short_motion_q.clear()
        self.walk_motion_q.clear()
        self.long_range_q.clear()
        self.presence = False
        self.enter_cnt = 0
        self.gap = 0

    # ---------- 空房基线 ----------
    def start_baseline_learn(self, frames=None):
        """网页按钮触发：清空已收集样本，重新采集空房基线。

        调用时请确保房间无人；采集满 frames 帧后自动固化为基线。
        """
        self._base_rng.clear()
        self._base_mot.clear()
        self._baseline_target = frames or self.baseline_learn_frames or 14400
        self._baseline_learning = True

    def baseline_dict(self):
        """导出基线，供持久化到 JSON。"""
        return {
            "range_p95": self.baseline_range,
            "motion_p95": self.baseline_motion,
            "learned": self.baseline_range is not None,
        }

    def load_baseline(self, data):
        """从 JSON 载入已保存的基线；缺字段则忽略。"""
        if not isinstance(data, dict):
            return
        rp = data.get("range_p95")
        mp = data.get("motion_p95")
        if rp is not None:
            self.baseline_range = float(rp)
        if mp is not None:
            self.baseline_motion = float(mp)
        self._baseline_learning = False

    def _finish_baseline(self):
        if not self._base_rng:
            return
        self.baseline_range = float(np.percentile(self._base_rng, 95))
        self.baseline_motion = float(np.percentile(self._base_mot, 95))
        self._baseline_learning = False

    def _update_baseline(self, short_range, short_motion):
        if not self._baseline_learning:
            return
        self._base_rng.append(short_range)
        self._base_mot.append(short_motion)
        if len(self._base_rng) >= self._baseline_target:
            self._finish_baseline()

    def update(self, frame):
        rng = frame.range_energy()
        motion = frame.motion_energy()

        # 各距离门能量（float64，避免 int16 平方溢出）
        c0 = frame.ch0.astype(np.float64)
        c1 = frame.ch1.astype(np.float64)
        gates = np.array([c0[p] ** 2 + c1[p] ** 2 for p in range(6)])
        self.short_gate_q.append(gates)
        self.short_range_q.append(rng)
        self.short_motion_q.append(motion)

        short_range = float(np.mean(self.short_range_q))
        short_motion = float(np.mean(self.short_motion_q))
        # 走动记忆：存每帧的短窗动作门，用于统计近期强动作帧数
        self.walk_motion_q.append(short_motion)
        # 长窗距离门中位数：辅助判断远距离静坐的弱抬升
        self.long_range_q.append(short_range)
        long_med = float(np.median(self.long_range_q)) if self.long_range_q else short_range
        range_lift = short_range / long_med if long_med > 0 else 1.0

        # 基线学习期：只采集空房底噪，不参与活动判据
        self._update_baseline(short_range, short_motion)

        # 远门比例：短窗内门4-5能量占比（近门电器接近 0，人呼吸约 0.1-0.4）
        gate_sum = np.sum(self.short_gate_q, axis=0)
        gate_total = float(gate_sum.sum())
        far_ratio = float((gate_sum[4] + gate_sum[5]) / gate_total) if gate_total > 0 else 0.0

        # 有效尖峰：绝对阈值 + 相对基线抬升 + 远门传播，三者同时满足。
        # 基线未学习完成时，相对条件退化为不约束（保持旧行为）。
        rel_ok = True
        if self.baseline_range is not None:
            rel_ok = short_range > self.baseline_range * self.baseline_ratio
        active = (short_range > self.active_threshold
                  and rel_ok
                  and far_ratio >= self.far_gate_ratio)

        if not self.presence:
            # 进入：人进门/坐下，距离门短窗连续超阈值
            if active:
                self.enter_cnt += 1
                if self.enter_cnt >= self.enter_frames:
                    self.presence = True
                    self.gap = 0
                    self.enter_cnt = 0
            else:
                self.enter_cnt = 0
        else:
            # 保持：活动尖峰重置计数；连续无尖峰超过 leave_gap 才离开
            if active:
                self.gap = 0
            else:
                self.gap += 1
                if self.gap >= self.leave_gap:
                    self.presence = False
                    self.gap = 0
                    self.enter_cnt = 0

        # 动作分级（有人时）：看动作区能量。
        # 静坐呼吸时动作区贴近底噪（即使距离门有呼吸尖峰），判"不动"；
        # 手指 / 轻微转头动作区中等，判"小动"；挥手 / 起身动作区高，判"大动"。
        # 走动保持：来回走动转身进入盲区时当下动作门低，但记忆窗口内
        # 强动作帧数足够（走动成簇、静坐孤立），仍判"大动"。
        if not self.presence:
            level = 0
        else:
            strong_count = sum(1 for v in self.walk_motion_q if v >= self.walk_strong_threshold)
            if short_motion >= self.large_threshold or strong_count >= self.walk_strong_frames:
                level = 2
            elif short_motion >= self.small_threshold:
                level = 1
            else:
                level = 0

        return Detection(
            presence=self.presence,
            motion_level=level,
            motion_label=MOTION_LABELS[level],
            range_energy=rng,
            short_range_energy=short_range,
            short_motion_energy=short_motion,
            far_gate_ratio=far_ratio,
            frames_since_active=self.gap,
            baseline_ready=self.baseline_range is not None,
            baseline_range=self.baseline_range,
            range_lift=range_lift,
        )

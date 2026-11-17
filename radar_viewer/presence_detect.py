# -*- coding: utf-8 -*-
"""
presence_detect.py — 基于 1dfft 数据的「无人 / 静坐 / 走动」实时判定。

背景：
  radar_viewer 实时串口收到的是 55 AA 裸帧，即雷达 DataType=1dfft 的输出：
  每 chirp 8 个复距离门（int16 I/Q），64 chirp 构成一个雷达帧（约 36 fps）。
  这种数据只有「距离谱」，没有现成的「动作门（多普勒）」信息。

  而 wavesense 项目的 Detector 依赖「6 距离门 + 10 动作门」（DataType=2dfft），
  靠动作门区分「静坐（微动） vs 走动（明显动作）」。

本模块在软件端补齐缺失的多普勒信息（路线 B，不改雷达配置）：
  1. 把一帧的 64 chirp × 8 复距离门累积成复数矩阵；
  2. 沿 chirp 维做加窗 2DFFT（多普勒 FFT），得到距离-多普勒谱；
  3. 从谱中提取：
       距离门能量 range_energy  —— 判「有人 / 无人」（静态目标也有能量）；
       动作门能量 motion_energy —— 判「静坐 / 走动」（非零多普勒 bin 能量）；
  4. 喂给移植自 wavesense 的 Detector 状态机（进入/保持/离开 + 动作分级）。

帧率换算：
  wavesense 的 Detector 按约 480 fps 标定，本链路为约 36 fps，故所有「时间类」
  参数按 FPS_REF / fps 比例缩放，能量类阈值不变（作为标定起点）。

阈值说明：
  软件 2DFFT 的输入（1dfft int16 复数）与硬件 2dfft 一致，能量量级接近，
  故以 wavesense 的标定阈值作为起点，后续用现场录制的 empty/sit/walk 数据微调。
"""
import numpy as np

# wavesense 标定时的参考帧率（约 480 fps）
FPS_REF = 480.0

# 三态（对外标签）
PRESENCE_LABELS = ("无人", "静坐", "走动")
# 内部动作分级标签（沿用 wavesense：0 不动 / 1 小动 / 2 大动）
MOTION_LABELS = ("不动", "小动", "大动")


def extract_features(r0, r1, n_bin, n_chirp, gate_bins=(1, 2, 3, 4, 5, 6), window=True,
                     motion_lo_bin=3):
    """从一帧的 RX0/RX1 复距离门矩阵提取检测特征。

    参数：
      r0, r1   形状 (n_chirp, n_bin) 的 complex128 数组（一帧内按 chirp 排序）。
      n_bin    距离门数（1dfft 为 8）。
      n_chirp  每帧 chirp 数（64）。
      gate_bins 选作「6 个距离门」的 bin 序号（默认跳过 bin0 近距/直流干扰）。
      window   是否加 hamming 窗。
      motion_lo_bin 动作门的最小多普勒 bin 序号。静态目标的直流能量经窗函数
               主瓣泄漏到多普勒 bin ±1、±2（各约 DC 的 19%），必须排除，否则
               静态目标会被误判为「有动作」。默认排除 bin 0..2 及对称的高频侧。

    返回：
      range_energy   距离门能量（两通道合计，用于存在判定）。
      motion_energy  动作门能量（非 DC、非泄漏区多普勒 bin，用于动作分级）。
      gates6         6 个距离门各自的能量（用于远门比例过滤）。
      dc_energy      零多普勒（静态）能量，调试/参考用。
    """
    # 距离门能量：每 bin 沿 chirp 求和的 I²+Q²，两通道合计（与 wavesense 口径一致）
    gate_power = np.sum(np.abs(r0) ** 2 + np.abs(r1) ** 2, axis=0)  # (n_bin,)
    gates6 = gate_power[list(gate_bins)].astype(np.float64)
    range_energy = float(gates6.sum())

    # 2DFFT：沿 chirp 维加窗 FFT，得到距离-多普勒谱
    w = np.hamming(n_chirp) if window else np.ones(n_chirp)
    rd0 = np.fft.fft(r0 * w[:, None], axis=0)  # (n_chirp, n_bin)
    rd1 = np.fft.fft(r1 * w[:, None], axis=0)
    rd_power = np.abs(rd0) ** 2 + np.abs(rd1) ** 2

    # 动作门能量：取 [motion_lo_bin, n_chirp - motion_lo_bin) 的中间多普勒 bin，
    # 剔除 DC 及其窗函数主瓣泄漏区（低频侧 + 对称高频侧）。
    hi = n_chirp - motion_lo_bin
    motion_energy = float(np.sum(rd_power[motion_lo_bin:hi, :]))
    dc_energy = float(np.sum(rd_power[0, :]))
    return range_energy, motion_energy, gates6, dc_energy


class DetectorFrame:
    """把提取到的能量包装成 Detector 需要的 Frame 接口。

    Detector.update 只依赖三个量：
      frame.range_energy() / frame.motion_energy() / frame.ch0(前 6 门)。
    ch0 前 6 门填 sqrt(每门能量)，ch1 全 0，使 c0[p]² + c1[p]² 恰好等于每门能量。
    """

    def __init__(self, gates6, range_energy, motion_energy):
        self._gates6 = np.asarray(gates6, dtype=np.float64)
        self._range = float(range_energy)
        self._motion = float(motion_energy)

    def range_energy(self):
        return self._range

    def motion_energy(self):
        return self._motion

    @property
    def ch0(self):
        a = np.zeros(18, dtype=np.float64)
        a[:6] = np.sqrt(np.maximum(self._gates6, 0.0))
        return a

    @property
    def ch1(self):
        return np.zeros(18, dtype=np.float64)


class Detector:
    """存在与动作判定器（状态机移植自 wavesense/src/detector/detector.py）。

    逻辑不变，仅：
      - 参数由 DetectorConfig 提供（已按链路帧率换算时间类参数）；
      - 输入改为 DetectorFrame（软件 2DFFT 提取的能量）。
    """

    def __init__(self, cfg):
        d = cfg
        self.short_win = d.short_window
        self.active_threshold = d.active_threshold
        self.enter_frames = d.enter_frames
        self.leave_gap = d.leave_gap_frames
        self.small_threshold = d.small_move_threshold
        self.large_threshold = d.large_move_threshold
        self.far_gate_ratio = d.far_gate_ratio
        self.walk_win = d.walk_window
        self.walk_strong_threshold = d.walk_strong_threshold
        self.walk_strong_frames = d.walk_strong_frames
        self.baseline_learn_frames = d.baseline_learn_frames
        self.baseline_ratio = d.baseline_ratio
        self.long_win = d.long_window

        from collections import deque
        self.short_gate_q = deque(maxlen=self.short_win)
        self.short_range_q = deque(maxlen=self.short_win)
        self.short_motion_q = deque(maxlen=self.short_win)
        self.walk_motion_q = deque(maxlen=self.walk_win)
        self.long_range_q = deque(maxlen=self.long_win)

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

        c0 = frame.ch0
        c1 = frame.ch1
        gates = np.array([c0[p] ** 2 + c1[p] ** 2 for p in range(6)])
        self.short_gate_q.append(gates)
        self.short_range_q.append(rng)
        self.short_motion_q.append(motion)

        short_range = float(np.mean(self.short_range_q))
        short_motion = float(np.mean(self.short_motion_q))
        self.walk_motion_q.append(short_motion)
        self.long_range_q.append(short_range)
        long_med = float(np.median(self.long_range_q)) if self.long_range_q else short_range
        range_lift = short_range / long_med if long_med > 0 else 1.0

        self._update_baseline(short_range, short_motion)

        gate_sum = np.sum(self.short_gate_q, axis=0)
        gate_total = float(gate_sum.sum())
        far_ratio = float((gate_sum[4] + gate_sum[5]) / gate_total) if gate_total > 0 else 0.0

        rel_ok = True
        if self.baseline_range is not None:
            rel_ok = short_range > self.baseline_range * self.baseline_ratio
        active = (short_range > self.active_threshold
                  and rel_ok
                  and far_ratio >= self.far_gate_ratio)

        if not self.presence:
            if active:
                self.enter_cnt += 1
                if self.enter_cnt >= self.enter_frames:
                    self.presence = True
                    self.gap = 0
                    self.enter_cnt = 0
            else:
                self.enter_cnt = 0
        else:
            if active:
                self.gap = 0
            else:
                self.gap += 1
                if self.gap >= self.leave_gap:
                    self.presence = False
                    self.gap = 0
                    self.enter_cnt = 0

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
            range_energy=rng,
            short_range_energy=short_range,
            short_motion_energy=short_motion,
            far_gate_ratio=far_ratio,
            frames_since_active=self.gap,
            baseline_ready=self.baseline_range is not None,
            baseline_range=self.baseline_range,
            range_lift=range_lift,
        )


class Detection:
    """一次判定的结果。"""

    def __init__(self, presence, motion_level, range_energy, short_range_energy,
                 short_motion_energy, far_gate_ratio, frames_since_active,
                 baseline_ready, baseline_range, range_lift):
        self.presence = bool(presence)
        self.motion_level = int(motion_level)
        self.motion_label = MOTION_LABELS[self.motion_level]
        self.range_energy = range_energy
        self.short_range_energy = short_range_energy
        self.short_motion_energy = short_motion_energy
        self.far_gate_ratio = far_gate_ratio
        self.frames_since_active = frames_since_active
        self.baseline_ready = baseline_ready
        self.baseline_range = baseline_range
        self.range_lift = range_lift

    @property
    def state_label(self):
        """对外三态标签：无人 / 静坐 / 走动。"""
        if not self.presence:
            return PRESENCE_LABELS[0]
        if self.motion_level >= 2:
            return PRESENCE_LABELS[2]
        return PRESENCE_LABELS[1]

    def summary(self):
        return (f"{self.state_label} | 动作:{self.motion_label} | "
                f"距离门:{self.short_range_energy:.2e} | 动作门:{self.short_motion_energy:.2e}")


class DetectorConfig:
    """Detector 参数集（含按链路帧率的换算）。"""

    def __init__(self, fps=36.0):
        k = FPS_REF / fps  # 480 / 36 ≈ 13.33
        self.fps = float(fps)
        # 雷达上电预热：约 40s 内基带/AGC 未稳定，距离/动作门能量从 ~1e10 指数
        # 衰减到 ~1e6（实测 empty 数据前 ~1400 帧即此过程）。期间判定不可靠，
        # LiveDetector 会直接丢弃这些帧，不喂给 Detector。
        self.warmup_seconds = 40.0
        self.warmup_frames = max(0, int(round(self.warmup_seconds * fps)))
        # 时间类参数：按帧率比例缩放
        self.short_window = max(2, int(round(30 / k)))          # 平滑短窗（~62ms）
        self.long_window = max(10, int(round(300 / k)))         # 长窗（~625ms）
        self.enter_frames = max(2, int(round(5 / k)))           # 进入需连续 active 帧
        self.leave_gap_frames = int(round(12000 / k))           # 25s 无尖峰判离开
        self.walk_window = int(round(3360 / k))                 # 7s 走动记忆窗
        self.walk_strong_frames = max(2, int(round(20 / k)))    # 窗内强动作帧数
        # 能量类阈值：不随帧率变（wavesense 标定值作为起点，现场微调）
        self.active_threshold = 1.2e9
        self.small_move_threshold = 4.0e8
        self.large_move_threshold = 2.6e9
        self.walk_strong_threshold = 2.0e9
        self.far_gate_ratio = 0.05
        # 基线自适应（默认关闭，可现场学习）
        self.baseline_learn_frames = 0
        self.baseline_ratio = 1.8


class LiveDetector:
    """实时检测器：累积 chirp → 组帧 → 提取特征 → 喂 Detector。

    GUI 每轮从 SerialRadarReader.snapshot() 拿到滚动 chirp 列表，调用 feed()；
    内部按 gch 序号去重，攒满一帧（64 chirp）才喂一次，避免重复/丢帧。
    """

    def __init__(self, fps=36.0, cpf=64, n_bin=8, cfg=None, motion_lo_bin=3):
        self.cpf = cpf
        self.n_bin = n_bin
        self.motion_lo_bin = motion_lo_bin
        self.cfg = cfg or DetectorConfig(fps=fps)
        self.detector = Detector(self.cfg)
        self._last_gch = -1
        self._cur_frame = -1
        self._frame = {}
        self.last = None
        self.n_frames_processed = 0
        self.warmup_left = self.cfg.warmup_frames

    def reset(self):
        self.detector.reset()
        self._last_gch = -1
        self._cur_frame = -1
        self._frame.clear()
        self.last = None
        self.n_frames_processed = 0
        self.warmup_left = self.cfg.warmup_frames

    @property
    def warmup_remaining_seconds(self):
        """剩余预热秒数（未预热完成时 >0）。"""
        fps = self.cfg.fps if self.cfg.fps else 1.0
        return self.warmup_left / fps

    def feed(self, chirps):
        """chirps: [(gch, r0, r1), ...] 按时间递增的 chirp 列表。

        返回：本轮回补产生的 Detection（若有新完整帧），否则 None。
        """
        produced = None
        for gch, r0, r1 in chirps:
            if gch <= self._last_gch:
                continue
            self._last_gch = gch
            fr = gch // self.cpf
            ci = gch % self.cpf
            if self._cur_frame != -1 and fr != self._cur_frame:
                # 帧边界：flush 上一帧
                d = self._flush()
                if d is not None:
                    produced = d
            self._cur_frame = fr
            self._frame[ci] = (r0, r1)
        return produced

    def _flush(self):
        if len(self._frame) == self.cpf:
            idxs = sorted(self._frame)
            r0 = np.stack([self._frame[i][0] for i in idxs])  # (cpf, n_bin)
            r1 = np.stack([self._frame[i][1] for i in idxs])
            range_e, motion_e, gates6, dc_e = extract_features(
                r0, r1, self.n_bin, self.cpf, motion_lo_bin=self.motion_lo_bin)
            if self.warmup_left > 0:
                # 预热期：丢弃该帧，不喂给 Detector，避免污染平滑窗口与基线
                self.warmup_left -= 1
                self.last = None
            else:
                self.last = self.detector.update(
                    DetectorFrame(gates6, range_e, motion_e))
                self.n_frames_processed += 1
        self._frame.clear()
        return self.last

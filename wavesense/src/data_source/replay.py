# -*- coding: utf-8 -*-
"""replay.py —— FileReplaySource：读授权软件导出的 DAT 二进制文件回放（B2）。

职责：把 DAT 里的每门平均功率翻译成统一 Frame（range_energy 为去空房底噪后的 dB 能量）。
不做判定、不发网络；判定交给 Detector。

说明：
- 本批 DAT 不含目标信息（软件导出时未勾选），targets 固定为 None → people_count 走“未知”路径；
- bg_db 为空时，range_energy 直接输出原始 dB（用于对拍/排查）；
- exclude_dc_bin0=True 时门 0（直流自杂波）能量置 0，与授权软件“排除直流 bin0”一致。
"""

import math
from collections.abc import Iterator
from pathlib import Path

from src.data_source.base import DataSource
from src.data_source.dat_parser import iter_radar_frames
from src.data_source.frame import Frame


def compute_bg_db(empty_files, num_gates: int) -> list[float]:
    """从若干空房样本里算出每门平均 dB 底噪，供回放时扣除。"""
    sums = [0.0] * num_gates
    count = 0
    for f in empty_files:
        for power, _, _ in iter_radar_frames(f, num_gates):
            for g in range(num_gates):
                sums[g] += 10 * math.log10(max(power[g], 1e-9))
            count += 1
    if count == 0:
        raise ValueError("空房样本为空，无法估计底噪")
    return [sums[g] / count for g in range(num_gates)]


class FileReplaySource(DataSource):
    """导出 DAT 文件回放数据源。"""

    def __init__(self, file_path: str | Path, num_gates: int,
                 fps: float = 10.0, bg_db: list[float] | None = None,
                 exclude_dc_bin0: bool = True):
        self.file_path = Path(file_path)
        self.num_gates = int(num_gates)
        self.fps = float(fps)
        self.bg_db = bg_db              # 空房每门 dB 底噪；None 表示不扣除
        self.exclude_dc_bin0 = bool(exclude_dc_bin0)

    def iter_frames(self) -> Iterator[Frame]:
        for idx, (power, _, _) in enumerate(
                iter_radar_frames(self.file_path, self.num_gates)):
            # 功率 → dB；再按是否给了空房底噪决定是否扣除
            db = [10 * math.log10(max(p, 1e-9)) for p in power]
            if self.bg_db is not None:
                energy = [max(0.0, db[g] - self.bg_db[g]) for g in range(self.num_gates)]
            else:
                energy = list(db)
            if self.exclude_dc_bin0:
                energy[0] = 0.0  # 门 0 直流自杂波，与软件勾选一致

            yield Frame(
                timestamp=idx / self.fps,
                range_energy=energy,
                targets=None,   # 本批 DAT 无目标信息 → people_count 未知
                raw={"power": power},
            )

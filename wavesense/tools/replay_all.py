"""批量回放全部 17 个录制文件，验证存在与动作判定。

输出每个文件的：总帧、有人帧占比、最终状态、进入/离开次数、动作分级分布。
标签说明：
  有人动作/静坐文件：最终应有人。
  真正空房文件（11_21_31 / 11_22_41）：包含"人离开→空房→人回来"，
    最终是人回来；验收看"中段是否出现足够长的无人段"（空房段）。
"""
import os

from src.config import load_config
from src.data_source import FileReplaySource
from src.detector import Detector

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "samples") + os.sep

# (标签, 类型, 文件名)  类型：present=有人；empty=真正空房（看中段）
FILES = [
    ("50cm初版-不动", "present", "RadarData_2026_10_20_10_16_59.gz"),
    ("50cm初版-小动", "present", "RadarData_2026_10_20_10_17_19.gz"),
    ("50cm初版-大动", "present", "RadarData_2026_10_20_10_17_34.gz"),
    ("1m初版-不动", "present", "RadarData_2026_10_20_10_30_12.gz"),
    ("1m初版-小动", "present", "RadarData_2026_10_20_10_30_23.gz"),
    ("1m初版-大动", "present", "RadarData_2026_10_20_10_30_37.gz"),
    ("50cm强化-不动", "present", "RadarData_2026_10_20_10_50_18.gz"),
    ("50cm强化-小动", "present", "RadarData_2026_10_20_10_50_28.gz"),
    ("50cm强化-大动", "present", "RadarData_2026_10_20_10_50_41.gz"),
    ("1m强化-不动", "present", "RadarData_2026_10_20_10_51_18.gz"),
    ("1m强化-小动", "present", "RadarData_2026_10_20_10_51_25.gz"),
    ("1m强化-大动", "present", "RadarData_2026_10_20_10_51_38.gz"),
    ("房间内-动作后静坐", "present", "RadarData_2026_10_20_10_56_14.gz"),
    ("电脑旁静坐", "present", "RadarData_2026_10_20_11_06_19.gz"),
    ("房间内-动作后静坐2", "present", "RadarData_2026_10_20_11_07_21.gz"),
    ("真正空房-离开又回来", "empty", "RadarData_2026_10_20_11_21_31.gz"),
    ("真正空房-离开又回来2", "empty", "RadarData_2026_10_20_11_22_41.gz"),
]


def longest(cond, val):
    best = cur = 0
    for x in cond:
        if x == val:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def main():
    cfg = load_config()
    n_pass = 0
    for label, kind, fn in FILES:
        path = D + fn
        if not os.path.exists(path):
            print(f"{label:<18} 文件缺失: {path}")
            continue
        det = Detector(cfg)
        n = 0
        present_frames = 0
        n_enter = n_leave = 0
        prev = False
        levels = [0, 0, 0]
        pres_seq = []
        for frame in FileReplaySource(path):
            d = det.update(frame)
            n += 1
            pres_seq.append(d.presence)
            if d.presence:
                present_frames += 1
                levels[d.motion_level] += 1
            if d.presence and not prev:
                n_enter += 1
            if prev and not d.presence:
                n_leave += 1
            prev = d.presence

        tot = sum(levels) or 1
        lv = f"不动{levels[0]/tot*100:.0f}% 小动{levels[1]/tot*100:.0f}% 大动{levels[2]/tot*100:.0f}%"
        if kind == "present":
            # 有人文件：最终应有人，且无明显漏检段
            worst_gap = longest(pres_seq, False)
            ok = det.presence and worst_gap < 12000
            note = f"最长无信号 {worst_gap}帧"
        else:
            # 空房文件：中段应出现足够长的无人段（>12000）
            longest_empty = longest(pres_seq, False)
            ok = longest_empty >= 12000
            note = f"最长空房段 {longest_empty}帧"
        n_pass += ok
        mark = "OK" if ok else "XX"
        print(f"[{mark}] {label:<18}{n:>6}帧 有人{present_frames/n*100:>5.1f}%  "
              f"进{n_enter} 离{n_leave}  {note}  {lv}")
    print("-" * 100)
    print(f"验证通过: {n_pass}/{len(FILES)}")


if __name__ == "__main__":
    main()

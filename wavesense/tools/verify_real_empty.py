"""回放真正空房文件（饮水机场景），量化：
- 稳态（去掉开头/结尾人进出）的误报
- 人离开后到判无人的响应帧数
- 人回来后到判有人的响应帧数
"""
from src.config import load_config
from src.data_source import FileReplaySource
from src.detector import Detector

D = "/Users/a123/Desktop/"
FILES = [
    ("空房A(32501帧)", "RadarData_2026_10_20_11_21_31.gz", 32501),
    ("空房B(42701帧)", "RadarData_2026_10_20_11_22_41.gz", 42701),
]
FPS = 480
PAD = 1500   # 去掉开头/结尾各1500帧（人进出+长窗反映），中段视为稳态

cfg = load_config()
for name, fn, total in FILES:
    det = Detector(cfg)
    pres = []
    for fr in FileReplaySource(D + fn):
        pres.append(det.update(fr).presence)
    n = len(pres)
    # 稳态段误报
    core = pres[PAD:n - PAD]
    core_false = sum(core)
    print(f"{name}: 稳态段 {len(core)} 帧（约{len(core)/FPS:.1f}s），误报 {core_false} 帧")

    # 找出所有 presence 区间
    runs = []
    i = 0
    while i < n:
        if pres[i]:
            j = i
            while j < n and pres[j]:
                j += 1
            runs.append((i, j - 1))
            i = j
        else:
            i += 1
    print(f"  判有人区间（共{len(runs)}段，起止帧 / 约秒）:")
    for s, e in runs:
        print(f"    {s}~{e}  ({s/FPS:.1f}~{e/FPS:.1f}s, 长{(e-s+1)/FPS:.1f}s)")
    print()

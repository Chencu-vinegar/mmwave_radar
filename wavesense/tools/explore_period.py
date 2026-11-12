"""周期特征可行性探索：自相关首峰滞后 / 峰值 / 过零率。

对比空房空调干扰、静坐呼吸、走动三类样本，看高频周期特征是否可分。
序列取动作区每帧能量；窗口 128 帧，步长 64，整段统计中位数。

用法: PYTHONPATH=. python tools/explore_period.py
"""
import os
import numpy as np

from src.data_source import FileReplaySource

WIN = 128
MAX_LAG = 48


def autocorr_first_peak(x):
    x = np.asarray(x, dtype=np.float64)
    x = x - x.mean()
    var = float((x ** 2).mean())
    if var <= 0:
        return 0, 0.0
    n = len(x)
    corr = []
    for k in range(MAX_LAG + 1):
        c = float((x[:n - k] * x[k:]).mean()) / var
        corr.append(c)
    # 首个局部极大值且超过 0.4
    peak_lag, peak_val = 0, 0.0
    for k in range(2, MAX_LAG):
        if corr[k] > corr[k - 1] and corr[k] > corr[k + 1] and corr[k] > 0.4:
            peak_lag, peak_val = k, corr[k]
            break
    return peak_lag, peak_val


def zero_cross(x):
    x = np.asarray(x, dtype=np.float64)
    x = x - x.mean()
    s = np.sign(x)
    changes = int(np.sum(s[1:] * s[:-1] < 0))
    return changes / (len(x) - 1)


def features(path):
    motion = []
    for f in FileReplaySource(path):
        motion.append(f.motion_energy())
    motion = np.array(motion, dtype=np.float64)
    lags, peaks, zcr = [], [], []
    for i in range(0, len(motion) - WIN, WIN // 2):
        seg = motion[i:i + WIN]
        lag, peak = autocorr_first_peak(seg)
        lags.append(lag)
        peaks.append(peak)
        zcr.append(zero_cross(seg))
    if not lags:
        return None
    return {
        "lag_med": float(np.median(lags)),
        "peak_med": float(np.median(peaks)),
        "zcr_med": float(np.median(zcr)),
        "n_win": len(lags),
    }


S = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples")
CASES = [
    ("空房 空调开", "RadarData_2026_11_05_17_46_34.gz"),
    ("空房 空调开", "RadarData_2026_11_05_17_49_06.gz"),
    ("空房 空调关", "RadarData_2026_11_05_18_29_27.gz"),
    ("静坐 0.5m", "RadarData_2026_11_05_18_37_47.gz"),
    ("静坐 1m", "RadarData_2026_11_05_18_38_31.gz"),
    ("静坐 1.5m", "RadarData_2026_11_05_18_39_37.gz"),
    ("静坐 2m b", "RadarData_2026_11_12_sit_2m_b.dat"),
    ("走动 2", "RadarData_2026_11_05_17_55_36.gz"),
    ("走动 1", "RadarData_2026_11_05_17_54_28.gz"),
]

for label, fn in CASES:
    p = os.path.join(S, fn)
    if not os.path.exists(p):
        print(f"缺失 {fn}")
        continue
    r = features(p)
    print(f"{label:12s} {fn[-22:]:22s} 首峰滞后lag={r['lag_med']:5.1f}  自相关峰={r['peak_med']:.2f}  过零率={r['zcr_med']:.2f}")

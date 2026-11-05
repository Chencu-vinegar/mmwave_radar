# -*- coding: utf-8 -*-
"""analyze_samples.py —— B1 离线样本统计脚本（纯离线，不接串口、不发 MQTT）。

做什么：
1. 解析 samples/ 下授权软件导出的 DAT 二进制样本，按文件名前缀（empty/sit/walk）分组；
2. 统计每类、每个距离门的平均能量与能量方差；
3. 画三类距离门能量对比图（PNG）；
4. 参考统计分布，给出 energy_threshold 区间与静坐稳定方差上限的建议值，写进 Markdown 报告。

帧格式（依据手册 §4.2 MCU 封装 + §4.1 芯片数据，2026-10 解析实测确认）：
  MCU 帧：ICLH(4B) | 长度 LE(2B) | 类型(1B) | 扩展/通道(1B) | 数据段 | ICLT(4B)
  数据段内：芯片头(4B, 大端, 0xAA 开头) | 8 个复距离门(I, Q 各 int16, 交织) | 校验帧尾(4B)
  一个雷达帧 = 64 个 chirp × 2 接收通道；每门功率 = 各 chirp/通道 I²+Q² 的平均值。
  TODO(B2)：接真实回放链路时，用软件界面悬停数值复核 I/Q 排列、门顺序与跨 chirp 聚合方式。

运行：
    python analyze_samples.py
可选参数：--samples 样本目录（默认 samples/）  --out 报告输出目录（默认 docs/analysis）
"""

import argparse
import glob
import math
import statistics
import struct
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 无界面环境只存图，不弹窗
import matplotlib.pyplot as plt

# 允许从项目根目录直接运行本脚本
sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.config import load_config  # noqa: E402
from src.data_source.dat_parser import (  # noqa: E402
    CHIRPS_PER_FRAME, decode_chip_frame, iter_radar_frames,
)

# 标签与中文名、绘图颜色
LABELS = ("empty", "sit", "walk")
LABEL_CN = {"empty": "空房", "sit": "静坐", "walk": "走动"}
LABEL_COLOR = {"empty": "#2e7d32", "sit": "#1565c0", "walk": "#c62828"}

# 数值保护：功率接近 0 时取对数用的小常数
_EPS = 1.0


# ---------- 一、DAT 解析 ----------

def label_of_file(path: Path) -> str | None:
    """按文件名前缀取场景标签（empty/sit/walk）；识别不出来返回 None。"""
    prefix = path.stem.split("_")[0].lower()
    return prefix if prefix in LABELS else None


# ---------- 二、统计累加器 ----------

class GateStats:
    """在线累加每类样本的原始功率、去底噪能量均值/方差与帧级标量分布。"""

    def __init__(self, num_gates: int):
        self.n = 0
        self.gates = num_gates
        # 原始功率
        self.sum_p = [0.0] * num_gates
        self.sum_p2 = [0.0] * num_gates
        # 去底噪 dB 能量
        self.sum_e = [0.0] * num_gates
        self.sum_e2 = [0.0] * num_gates
        # 帧级：超底噪能量之和、帧内能量向量方差
        self.excess: list[float] = []
        self.frame_var: list[float] = []

    def add_raw(self, power: list[float]):
        self.n += 1
        for g in range(self.gates):
            p = power[g]
            self.sum_p[g] += p
            self.sum_p2[g] += p * p

    def add_clean(self, energy: list[float]):
        self.n += 1
        for g in range(self.gates):
            e = energy[g]
            self.sum_e[g] += e
            self.sum_e2[g] += e * e
        self.excess.append(sum(energy))
        self.frame_var.append(statistics.pvariance(energy))

    # —— 门级均值/方差 ——
    def raw_mean(self, g: int) -> float:
        return self.sum_p[g] / self.n

    def raw_var(self, g: int) -> float:
        m = self.raw_mean(g)
        return max(0.0, self.sum_p2[g] / self.n - m * m)

    def clean_mean(self, g: int) -> float:
        return self.sum_e[g] / self.n

    def clean_var(self, g: int) -> float:
        m = self.clean_mean(g)
        return max(0.0, self.sum_e2[g] / self.n - m * m)


def percentile(values: list[float], q: float) -> float:
    """最近秩分位数，q ∈ [0,1]；空列表返回 NaN。"""
    if not values:
        return float("nan")
    s = sorted(values)
    k = (len(s) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


# ---------- 三、主流程 ----------

def main():
    parser = argparse.ArgumentParser(description="WaveSense B1 离线样本统计分析")
    parser.add_argument("--samples", default="samples", help="样本目录，默认 samples/")
    parser.add_argument("--out", default="docs/analysis", help="报告输出目录，默认 docs/analysis/")
    args = parser.parse_args()

    cfg = load_config()
    num_gates = int(cfg["sim_num_gates"])
    # 门 0 是直流自杂波门（授权软件“排除直流 bin0”），统计与判定时剔除
    exclude_dc_bin0 = bool(cfg.get("exclude_dc_bin0", True))
    sample_dir = Path(args.samples)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(Path(p) for p in glob.glob(str(sample_dir / "*.dat")))
    if not files:
        print(f"[B1] 未在 {sample_dir} 找到 .dat 样本文件。")
        return

    # 按标签分组文件
    grouped: dict[str, list[Path]] = {label: [] for label in LABELS}
    unmatched: list[str] = []
    for f in files:
        tag = label_of_file(f)
        if tag is None:
            unmatched.append(f.name)
        else:
            grouped[tag].append(f)
    if unmatched:
        print(f"[B1] 警告：以下文件名前缀不识别，已跳过：{unmatched}")

    print(f"[B1] 距离门数={num_gates}，每帧 chirp 数={CHIRPS_PER_FRAME}，"
          f"排除直流门0={exclude_dc_bin0}；样本分组："
          + ", ".join(f"{LABEL_CN[t]} {len(grouped[t])} 个" for t in LABELS))

    # ---------- 第一遍：原始功率统计 + 空房底噪（dB） ----------
    raw_stats: dict[str, GateStats] = {t: GateStats(num_gates) for t in LABELS}
    empty_db_sum = [0.0] * num_gates
    sample_rows: list[tuple[str, str, int, int, int]] = []  # 文件、标签、帧数、chirp 覆盖、通道样本

    for label in LABELS:
        for f in grouped[label]:
            frame_count = chirp_seen = channel_seen = 0
            for power, chirp_seen, channel_seen in iter_radar_frames(f, num_gates):
                raw_stats[label].add_raw(power)
                if label == "empty":
                    for g in range(num_gates):
                        empty_db_sum[g] += 10 * math.log10(power[g] + _EPS)
                frame_count += 1
            sample_rows.append((f.name, LABEL_CN[label], frame_count,
                                chirp_seen, channel_seen))
            print(f"  解析 {f.name}: {frame_count} 雷达帧（末帧 chirp={chirp_seen}, "
                  f"通道样本={channel_seen}）")

    if raw_stats["empty"].n == 0:
        print("[B1] 错误：缺少空房(empty)样本，无法估计底噪，终止。")
        return

    # 空房每门平均 dB 底噪
    bg_db = [empty_db_sum[g] / raw_stats["empty"].n for g in range(num_gates)]

    # ---------- 第二遍：去底噪 dB 能量统计 ----------
    clean_stats: dict[str, GateStats] = {t: GateStats(num_gates) for t in LABELS}
    # 帧间能量差：相邻帧各门 |ΔE| 之和；空房稳定→ΔE 小，人走动→逐帧跳变
    diff_stats: dict[str, list[float]] = {t: [] for t in LABELS}
    for label in LABELS:
        for f in grouped[label]:
            prev_energy: list[float] | None = None
            for power, _, _ in iter_radar_frames(f, num_gates):
                energy = [
                    max(0.0, 10 * math.log10(power[g] + _EPS) - bg_db[g])
                    for g in range(num_gates)
                ]
                if exclude_dc_bin0:
                    # 门 0 为直流自杂波门，不计入超底噪能量（与授权软件勾选一致）
                    energy[0] = 0.0
                if prev_energy is not None:
                    delta = sum(abs(energy[g] - prev_energy[g]) for g in range(num_gates))
                    diff_stats[label].append(delta)
                prev_energy = energy
                clean_stats[label].add_clean(energy)

    # ---------- 控制台打印汇总 ----------
    print("\n===== 每门去底噪能量均值（dB，空房为 0 基准） =====")
    header = "门".rjust(4) + "".join(LABEL_CN[t].rjust(12) for t in LABELS)
    print(header)
    for g in range(num_gates):
        row = f"{g:>4}"
        for t in LABELS:
            row += f"{clean_stats[t].clean_mean(g):12.2f}"
        print(row)

    print("\n===== 帧级超底噪能量之和 分位数（dB·门） =====")
    print("类别".ljust(8) + "均值".rjust(10) + "P50".rjust(10) + "P90".rjust(10)
          + "P95".rjust(10) + "P99".rjust(10))
    dist: dict[str, dict[str, float]] = {}
    for t in LABELS:
        ex = clean_stats[t].excess
        dist[t] = {
            "mean": statistics.mean(ex), "p50": percentile(ex, 0.5),
            "p90": percentile(ex, 0.9), "p95": percentile(ex, 0.95),
            "p99": percentile(ex, 0.99),
        }
        print(LABEL_CN[t].ljust(8) + "".join(f"{dist[t][k]:10.2f}" for k in
              ("mean", "p50", "p90", "p95", "p99")))

    print("\n===== 帧内能量向量方差 分位数（静坐稳定性参考） =====")
    var_dist: dict[str, dict[str, float]] = {}
    for t in LABELS:
        fv = clean_stats[t].frame_var
        var_dist[t] = {
            "mean": statistics.mean(fv), "p50": percentile(fv, 0.5),
            "p95": percentile(fv, 0.95),
        }
        print(LABEL_CN[t].ljust(8) + f"均值={var_dist[t]['mean']:.4f} "
              f"P50={var_dist[t]['p50']:.4f} P95={var_dist[t]['p95']:.4f}")

    print("\n===== 帧间能量差 |ΔE| 之和 分位数（走动候选特征） =====")
    print("类别".ljust(8) + "均值".rjust(10) + "P50".rjust(10) + "P90".rjust(10)
          + "P95".rjust(10) + "P99".rjust(10))
    diff_dist: dict[str, dict[str, float]] = {}
    for t in LABELS:
        dv = diff_stats[t]
        diff_dist[t] = {
            "mean": statistics.mean(dv), "p50": percentile(dv, 0.5),
            "p90": percentile(dv, 0.9), "p95": percentile(dv, 0.95),
            "p99": percentile(dv, 0.99),
        }
        print(LABEL_CN[t].ljust(8) + "".join(f"{diff_dist[t][k]:10.2f}" for k in
              ("mean", "p50", "p90", "p95", "p99")))
    # 帧间差口径下的分离判定：空房 P99 与走动 P50 比较
    diff_sep_ok = diff_dist["empty"]["p99"] < diff_dist["walk"]["p50"]
    diff_suggest = ((diff_dist["empty"]["p99"] + diff_dist["walk"]["p50"]) / 2
                    if diff_sep_ok else None)
    print(f"帧间差分离：{'可分离' if diff_sep_ok else '仍不可分离'} "
          f"（空房 P99={diff_dist['empty']['p99']:.2f} vs 走动 P50={diff_dist['walk']['p50']:.2f}）")

    # ---------- 阈值建议 ----------
    empty_p99 = dist["empty"]["p99"]
    walk_p50 = dist["walk"]["p50"]
    sit_p10 = percentile(clean_stats["sit"].excess, 0.10)
    sit_p50 = dist["sit"]["p50"]
    # 运动阈值区间：下界取空房 P99（低于它空房易误触发），上界取走动 P50（保证走动能触发）
    motion_low, motion_high = empty_p99, walk_p50
    separation_ok = motion_low < motion_high
    motion_suggest = (motion_low + motion_high) / 2 if separation_ok else None
    # 静坐占用线：空房各门去底噪均值 + 2 倍标准差（近似 P95）的最大值，保守取值
    gate_floor = [
        clean_stats["empty"].clean_mean(g) + 2 * math.sqrt(clean_stats["empty"].clean_var(g))
        for g in range(num_gates)
    ]
    still_gate_min = max(gate_floor)
    # 静坐方差上限：静坐帧向量方差 P95；空房均值作为参照
    still_var_suggest = var_dist["sit"]["p95"]
    still_var_ref = var_dist["empty"]["mean"]

    print("\n===== 阈值建议（去底噪 dB 刻度，待 B2 回放链路复核） =====")
    if separation_ok:
        print(f"energy_threshold 区间：[{motion_low:.2f}, {motion_high:.2f}]，建议取 {motion_suggest:.2f}")
    else:
        print(f"energy_threshold：待定。空房 P99={motion_low:.2f} ≥ 走动 P50={motion_high:.2f}，")
        print("  当前样本在‘平均距离门能量’口径下无法区分走动与空房，config 先保持占位，B2 复核。")
    print(f"noise_floor：去底噪后基准为 0，建议 0.0（空房 P50={dist['empty']['p50']:.2f}）")
    print(f"still_gate_min_energy：先沿用占位 0.5；空房各门 P95 最大值 {still_gate_min:.2f} 仅作量级参照，B2 复核")
    print(f"still_energy_variance：建议 ≤ {still_var_suggest:.4f}（静坐 P95；空房均值参照 {still_var_ref:.4f}）")
    if diff_sep_ok:
        print(f"motion_delta_threshold（帧间差口径）：建议取 {diff_suggest:.2f} "
              f"（区间 [{diff_dist['empty']['p99']:.2f}, {diff_dist['walk']['p50']:.2f}]）")
    else:
        print("motion_delta_threshold：帧间差口径下空房 P99 仍 ≥ 走动 P50，暂不填。")
    if not separation_ok:
        print("诊断：走动与空房在当前 1DFFT 平均能量口径下分离不足，详见报告第 9 节。")

    # ---------- 绘图 ----------
    plot_path = out_dir / "gate_energy_comparison.png"
    _plot(clean_stats, raw_stats, num_gates, plot_path, exclude_dc_bin0)
    print(f"\n[B1] 对比图已保存：{plot_path}")

    # ---------- Markdown 报告 ----------
    report_path = out_dir / "B1-样本统计与阈值建议.md"
    _write_report(
        report_path, sample_rows, num_gates, bg_db,
        raw_stats, clean_stats, dist, var_dist,
        motion_low, motion_high, motion_suggest, still_gate_min,
        still_var_suggest, still_var_ref, separation_ok, plot_path,
        exclude_dc_bin0, diff_dist,
    )
    print(f"[B1] 统计报告已保存：{report_path}")


def _plot(clean_stats, raw_stats, num_gates: int, plot_path: Path,
          exclude_dc_bin0: bool = False):
    """画两类对比图：左=去底噪 dB 门均值（±标准差），右=原始功率门均值（对数轴）。"""
    # Windows 中文字体，找不到就退回默认
    plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    gates = list(range(num_gates))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    for t in LABELS:
        means = [clean_stats[t].clean_mean(g) for g in gates]
        stds = [math.sqrt(clean_stats[t].clean_var(g)) for g in gates]
        ax1.plot(gates, means, marker="o", label=LABEL_CN[t], color=LABEL_COLOR[t])
        ax1.fill_between(gates,
                         [m - s for m, s in zip(means, stds)],
                         [m + s for m, s in zip(means, stds)],
                         alpha=0.15, color=LABEL_COLOR[t])
    if exclude_dc_bin0:
        # 门 0 是直流自杂波门，绘图上用阴影标出“已剔除”
        ax1.axvspan(-0.5, 0.5, color="gray", alpha=0.15)
        ax1.text(0, ax1.get_ylim()[1] * 0.9 if ax1.get_ylim()[1] > 0 else 0.5,
                 " 门0\n 直流", fontsize=8, color="gray", va="top")
    ax1.set_title("去底噪后各距离门平均能量（dB，±1σ）")
    ax1.set_xlabel("距离门编号（0 近场 → 7 远场）")
    ax1.set_ylabel("能量（dB，相对空房底噪）")
    ax1.legend()
    ax1.grid(alpha=0.3)

    for t in LABELS:
        means = [raw_stats[t].raw_mean(g) for g in gates]
        ax2.plot(gates, means, marker="s", label=LABEL_CN[t], color=LABEL_COLOR[t])
    ax2.set_yscale("log")
    ax2.set_title("各距离门原始功率均值（对数轴）")
    ax2.set_xlabel("距离门编号（0 近场 → 7 远场）")
    ax2.set_ylabel("原始功率（两通道、跨 chirp 平均）")
    ax2.legend()
    ax2.grid(alpha=0.3, which="both")

    fig.tight_layout()
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)


def _write_report(report_path: Path, sample_rows, num_gates, bg_db,
                  raw_stats, clean_stats, dist, var_dist,
                  motion_low, motion_high, motion_suggest, still_gate_min,
                  still_var_suggest, still_var_ref, separation_ok, plot_path,
                  exclude_dc_bin0: bool = False, diff_dist: dict | None = None):
    """把统计结果和阈值建议写成 Markdown 报告，方便填回 config.yaml。"""
    lines: list[str] = []
    lines.append("# B1 样本统计与阈值建议（离线分析）\n")
    lines.append("> 本报告由 `analyze_samples.py` 自动生成，仅基于 `samples/` 现有样本；"
                 "阈值建议为**参考值**，B2 接入回放/实时链路后需复核。\n")

    # 1. 样本清单
    lines.append("## 1. 样本清单\n")
    lines.append("| 文件 | 场景 | 雷达帧数 | 末帧 chirp 覆盖 | 通道样本数 |")
    lines.append("|---|---|---|---|---|")
    for name, cn, n, chirp_n, ch_n in sample_rows:
        lines.append(f"| {name} | {cn} | {n} | {chirp_n} | {ch_n} |")

    # 2. 格式说明
    lines.append("\n## 2. 解析口径说明\n")
    lines.append("- MCU 帧：`ICLH` + 长度(LE) + 类型 + 通道 + 数据段 + `ICLT`；类型 0x00 为芯片数据。")
    lines.append("- 数据段：芯片头(4B, 0xAA 开头) + 8 个复距离门（I、Q 各 int16，交织）+ 校验帧尾(4B)。")
    lines.append("- 一个雷达帧 = 64 chirp × 2 通道；门功率 = 各 chirp/通道 `I²+Q²` 的平均值。")
    lines.append("- `dB = 10·log10(P)`；去底噪能量 `E_g = max(0, dB_g − 空房该门平均 dB)`，空房门基准为 0。")
    if exclude_dc_bin0:
        lines.append("- **门 0 按直流自杂波门剔除**（与授权软件界面“排除直流 bin0”勾选一致）："
                     "门 0 不计入超底噪能量之和、帧内方差与占用线；下表中门 0 列为 0 仅作参考。")
    lines.append("- TODO(B2)：用授权软件界面悬停数值复核 I/Q 排列、门顺序与跨 chirp 聚合方式；"
                 "拿到 `FILE_Radar_Config.ini` 核对真实门定义。\n")

    # 3. 原始功率均值表
    lines.append("## 3. 各距离门原始功率均值\n")
    lines.append("| 距离门 | " + " | ".join(LABEL_CN[t] for t in LABELS) + " |")
    lines.append("|---|" + "---|" * len(LABELS))
    for g in range(num_gates):
        row = f"| {g} | " + " | ".join(
            f"{raw_stats[t].raw_mean(g):.0f}" for t in LABELS) + " |"
        lines.append(row)

    # 4. 去底噪能量均值 ± 标准差
    lines.append("\n## 4. 各距离门去底噪能量（dB，均值 ± 标准差）\n")
    lines.append("| 距离门 | " + " | ".join(LABEL_CN[t] for t in LABELS) + " |")
    lines.append("|---|" + "---|" * len(LABELS))
    for g in range(num_gates):
        cells = []
        for t in LABELS:
            m = clean_stats[t].clean_mean(g)
            s = math.sqrt(clean_stats[t].clean_var(g))
            cells.append(f"{m:.2f} ± {s:.2f}")
        lines.append(f"| {g} | " + " | ".join(cells) + " |")

    # 5. 帧级分布
    lines.append("\n## 5. 帧级超底噪能量之和分布（dB·门）\n")
    lines.append("| 场景 | 均值 | P50 | P90 | P95 | P99 |")
    lines.append("|---|---|---|---|---|---|")
    for t in LABELS:
        d = dist[t]
        lines.append(f"| {LABEL_CN[t]} | {d['mean']:.2f} | {d['p50']:.2f} | "
                     f"{d['p90']:.2f} | {d['p95']:.2f} | {d['p99']:.2f} |")

    lines.append("\n## 6. 帧内能量向量方差分布（静坐稳定性参考）\n")
    lines.append("| 场景 | 均值 | P50 | P95 |")
    lines.append("|---|---|---|---|")
    for t in LABELS:
        v = var_dist[t]
        lines.append(f"| {LABEL_CN[t]} | {v['mean']:.4f} | {v['p50']:.4f} | {v['p95']:.4f} |")

    # 6b. 帧间能量差
    if diff_dist is not None:
        lines.append("\n## 6b. 帧间能量差 |ΔE| 分布（走动候选特征）\n")
        lines.append("> 相邻帧各距离门去底噪能量之差的绝对值之和；空房稳定→小，人走动→逐帧跳变。")
        lines.append("| 场景 | 均值 | P50 | P90 | P95 | P99 |")
        lines.append("|---|---|---|---|---|---|")
        for t in LABELS:
            d = diff_dist[t]
            lines.append(f"| {LABEL_CN[t]} | {d['mean']:.2f} | {d['p50']:.2f} | "
                         f"{d['p90']:.2f} | {d['p95']:.2f} | {d['p99']:.2f} |")
        sep_ok = diff_dist["empty"]["p99"] < diff_dist["walk"]["p50"]
        if sep_ok:
            sug = (diff_dist["empty"]["p99"] + diff_dist["walk"]["p50"]) / 2
            lines.append(f"\n> ✅ 帧间差口径下可分离：建议 `motion_delta_threshold ≈ {sug:.2f}` "
                         f"（区间 [{diff_dist['empty']['p99']:.2f}, {diff_dist['walk']['p50']:.2f}]）。")
        else:
            lines.append(f"\n> ⚠️ 帧间差口径下仍不可分离（空房 P99={diff_dist['empty']['p99']:.2f}，"
                         f"走动 P50={diff_dist['walk']['p50']:.2f}）。")

    # 7. 阈值建议
    lines.append("\n## 7. 阈值建议（填回 config.yaml 前请人工复核）\n")
    lines.append("| 配置项 | 建议值 | 依据 |")
    lines.append("|---|---|---|")
    if separation_ok:
        lines.append(f"| `energy_threshold` | 区间 [{motion_low:.2f}, {motion_high:.2f}]，"
                     f"建议 {motion_suggest:.2f} | 下界=空房 P99，上界=走动 P50 |")
    else:
        lines.append("| `energy_threshold` | **暂不填，保持占位** | 当前样本走动与空房在平均距离能量上"
                     f"未分离（空房 P99={motion_low:.2f}，走动 P50={motion_high:.2f}），B2 复核后再定 |")
    lines.append(f"| `noise_floor` | 0.0 | 已在去底噪步骤扣除空房底噪 |")
    lines.append(f"| `still_gate_min_energy` | 先沿用 config 占位 {0.5}；空房各门 P95 最大值 {still_gate_min:.2f} 仅作量级参照 | "
                 "当前样本静坐近门抬升不足，占用线 B2 上机复核后再定 |")
    lines.append(f"| `still_energy_variance` | ≤ {still_var_suggest:.4f} | 静坐帧向量方差 P95；"
                 f"空房均值参照 {still_var_ref:.4f} |")
    lines.append("\n> 说明：以上为单帧向量方差口径；Detector 静坐窗口为 90 帧摊平方差，"
                 "刻度不可直接等同，B2 回放链路接通后用真实窗口方差复核。\n")

    # 8. 各门静坐占用对比
    lines.append("\n## 8. 各门去底噪能量对比（静坐占用线参考）\n")
    lines.append("| 距离门 | 空房 P95 近似（均值+2σ） | 静坐均值 | 走动均值 |")
    lines.append("|---|---|---|---|")
    for g in range(num_gates):
        e_floor = clean_stats["empty"].clean_mean(g) + 2 * math.sqrt(clean_stats["empty"].clean_var(g))
        lines.append(f"| {g} | {e_floor:.2f} | {clean_stats['sit'].clean_mean(g):.2f} | "
                     f"{clean_stats['walk'].clean_mean(g):.2f} |")

    # 9. 分离度诊断与后续动作
    lines.append("\n## 9. 分离度诊断（B1 实测结论）\n")
    sit_p10 = percentile(clean_stats["sit"].excess, 0.10)
    empty_p10 = percentile(clean_stats["empty"].excess, 0.10)
    if not separation_ok:
        lines.append("**现象**：")
        lines.append("- 走动与空房在‘帧内平均距离门能量’口径下没有拉开差距"
                     f"（走动 P50={dist['walk']['p50']:.2f}，空房 P99={dist['empty']['p99']:.2f}）；")
        lines.append(f"- 静坐帧总超底噪能量反而更低（静坐 P10={sit_p10:.2f}、P50={dist['sit']['p50']:.2f}，"
                     f"空房 P50={dist['empty']['p50']:.2f}），**总能量阈值无法区分静坐与空房**，"
                     "静坐只能靠“能量稳定 + 固定门持续占用”这一路特征；")
        lines.append("- 静坐近场门（0~1）略高、远门接近 0，且帧内方差最小（见第 6 节）。")
        lines.append("\n**可能原因**：")
        lines.append("1. I/Q 排列、门顺序尚未用授权软件界面悬停数值复核，解码口径可能有偏差；")
        lines.append("2. 走动目标在采样时段来回移动，峰值被平均稀释，均值口径天然吃亏；")
        lines.append("3. 空房本身含固定杂波（墙体、家具、设备自杂波），抬高了空房基准；")
        lines.append("4. 运动特征可能主要体现在帧间能量差或速度维（2DFFT），本批 DAT 只含 1DFFT。")
        lines.append("\n**后续动作（B2 及以后）**：")
        lines.append("- 接通回放链路后，用软件悬停数值复核 I/Q 排列与门顺序；")
        lines.append("- 重采样本时固定安装高度/角度/距离，三类场景条件保持一致；")
        lines.append("- 评估把‘帧间能量差’纳入运动候选特征；")
        lines.append("- `energy_threshold` 在复核前不填入 config.yaml，继续使用占位值。")
    else:
        lines.append("当前样本下走动与空房在平均距离能量口径上可分离；B2 回放链路复核后再定稿阈值。")

    lines.append(f"\n## 10. 对比图\n\n![三类距离门能量对比]({plot_path.name})\n")

    report_path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
radar_app.py — 矽典微 DEV202H (ICL1122) 毫米波雷达数据解析桌面应用 (GUI)。

两种模式：
  1. 文件解析：选择 .dat/.ini，一次性解析并可视化，支持帧回放与导出。
  2. 串口实时：打开 USB 串口（默认 921600），实时读取并解析数据流，
     持续刷新 BEV / 距离-角度 / 距离-时间图，支持导出当前数据。
"""
import os
import csv
import time
import numpy as np
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from matplotlib.colors import LogNorm
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401  3D 点云标签页

import radar_parser as rp
import serial_live as sl

MAX_RANGE = 5.0
HEAT_FRAMES = 6      # BEV / 距离-角度热图只累积最近这几帧
RT_WINDOW = 50       # 距离-时间图窗口帧数
LIVE_REFRESH_MS = 33  # 实时刷新目标间隔（约 30 FPS，配合 blit 增量重绘）
BAUD_CHOICES = ["921600", "115200", "460800", "256000", "128000", "1000000", "230400"]


def _log_clim(data):
    """按 5%–99% 分位数裁剪的对数范围 (vmin, vmax)，无有效数据返回 None。"""
    pos = data[data > 0]
    if pos.size == 0:
        return None
    vmin = float(np.percentile(pos, 5.0))
    vmax = float(np.percentile(pos, 99.0))
    if vmax <= vmin:
        vmax = vmin * 10.0
    return (vmin, vmax)


def _log_norm(data):
    """返回按 5%–99% 分位数裁剪的对数归一化，避免空 bin / 极端值拉低对比度。"""
    c = _log_clim(data)
    if c is None:
        return LogNorm()
    return LogNorm(vmin=c[0], vmax=c[1], clip=True)


def _draw_heat2d(ax, x, y, weights, xbins, ybins, xrange, yrange):
    """在 ax 上绘制加权二维热图（对数颜色，用 imshow 加速渲染）。"""
    if len(x) == 0:
        return
    H, xe, ye = np.histogram2d(x, y, bins=[xbins, ybins], range=[xrange, yrange], weights=weights)
    if not np.any(H > 0):
        return
    Hm = np.ma.masked_where(H <= 0, H)
    ax.imshow(Hm.T, extent=[xrange[0], xrange[1], yrange[0], yrange[1]],
              origin="lower", cmap="inferno", norm=_log_norm(H), aspect="auto")


class RadarApp:
    def __init__(self, root):
        self.root = root
        root.title("毫米波雷达数据解析器 — DEV202H (ICL1122)")
        root.geometry("1180x840")

        # ---- 文件模式状态 ----
        self.dat_path = tk.StringVar()
        self.ini_path = tk.StringVar()
        self.scale = tk.DoubleVar(value=1.0)
        self.exclude_dc = tk.BooleanVar(value=True)
        self.mirror = tk.BooleanVar(value=False)
        self.frame_index = tk.IntVar(value=0)
        self.file_status = tk.StringVar(value="请选择 .dat 文件后点击「解析」")
        self.chirps = None
        self.config = None
        self.frames = self.ranges = self.angles = self.amps = self.bins = None
        self.n_frames = 0
        self.rt_matrix = None
        self.playing = False

        # ---- 实时模式状态 ----
        self.live_port = tk.StringVar()
        self.live_baud = tk.StringVar(value="921600")
        self.live_ini = tk.StringVar()
        self.live_status = tk.StringVar(value="选择串口后点击「连接」")
        self.live_reader = None
        self.live_connected = False
        self.live_t0 = time.time()
        # 实时显示优化状态：blit 背景缓存 / 热图 clim EMA / 瞬时帧率 EMA
        self._live_bg = None
        self._clim_ema = {}
        self._fps_ema = 0.0
        self._last_frames = 0
        self._last_fps_t = 0.0

        # ---- 3D 点云状态 ----
        self.threed_frame = tk.IntVar(value=-1)
        self.threed_status = tk.StringVar(value="先解析 .dat 或连接串口获取数据")
        self._last_3d_update = 0.0

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True)
        self.file_tab = ttk.Frame(self.notebook)
        self.live_tab = ttk.Frame(self.notebook)
        self.tab3d = ttk.Frame(self.notebook)
        self.notebook.add(self.file_tab, text=" 文件解析 ")
        self.notebook.add(self.live_tab, text=" 串口实时 ")
        self.notebook.add(self.tab3d, text=" 3D 点云 ")

        self._build_file_tab()
        self._build_live_tab()
        self._build_3d_tab()
        self._live_loop()

    # ================= 文件解析标签页 =================
    def _build_file_tab(self):
        top = ttk.LabelFrame(self.file_tab, text="文件", padding=8)
        top.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(top, text="数据文件:").grid(row=0, column=0, sticky="e", padx=(0, 4))
        ttk.Entry(top, textvariable=self.dat_path, width=70).grid(row=0, column=1, sticky="we", padx=4)
        ttk.Button(top, text="浏览...", command=self.browse_dat).grid(row=0, column=2, padx=4)
        ttk.Label(top, text="配置文件:").grid(row=1, column=0, sticky="e", padx=(0, 4), pady=(4, 0))
        ttk.Entry(top, textvariable=self.ini_path, width=70).grid(row=1, column=1, sticky="we", padx=4, pady=(4, 0))
        ttk.Button(top, text="浏览...", command=self.browse_ini).grid(row=1, column=2, padx=4, pady=(4, 0))
        ttk.Button(top, text="解析", command=self.parse).grid(row=1, column=3, padx=8, pady=(4, 0))
        top.columnconfigure(1, weight=1)

        param = ttk.LabelFrame(self.file_tab, text="参数", padding=8)
        param.pack(fill="x", padx=10, pady=4)
        ttk.Label(param, text="距离缩放:").grid(row=0, column=0, sticky="e")
        ttk.Spinbox(param, from_=0.5, to=3.0, increment=0.25, textvariable=self.scale,
                    width=6, command=self.on_param_change).grid(row=0, column=1, padx=4)
        ttk.Checkbutton(param, text="排除直流 bin0", variable=self.exclude_dc,
                        command=self.on_param_change).grid(row=0, column=2, padx=12)
        ttk.Checkbutton(param, text="X 镜像", variable=self.mirror,
                        command=self.on_param_change).grid(row=0, column=3, padx=4)
        ttk.Label(param, text="帧:").grid(row=0, column=4, sticky="e", padx=(12, 0))
        self.frame_spin = ttk.Spinbox(param, from_=0, to=0, textvariable=self.frame_index,
                                      width=6, command=self.on_frame_change)
        self.frame_spin.grid(row=0, column=5, padx=4)
        self.play_btn = ttk.Button(param, text="播放", command=self.toggle_play)
        self.play_btn.grid(row=0, column=6, padx=4)

        self.fig = Figure(figsize=(11.5, 7.0), dpi=100)
        self.ax_bev = self.fig.add_subplot(2, 2, 1)
        self.ax_ra = self.fig.add_subplot(2, 2, 2)
        self.ax_rt = self.fig.add_subplot(2, 2, 3)
        self.ax_fr = self.fig.add_subplot(2, 2, 4)
        self.fig.tight_layout(pad=2.5)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.file_tab)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=4)
        NavigationToolbar2Tk(self.canvas, self.file_tab).update()

        bottom = ttk.Frame(self.file_tab)
        bottom.pack(fill="x", padx=10, pady=(4, 8))
        ttk.Button(bottom, text="导出 CSV", command=self.export_csv).pack(side="left")
        ttk.Button(bottom, text="导出热图", command=self.export_heatmap_file).pack(side="left", padx=(8, 0))
        ttk.Label(bottom, textvariable=self.file_status, relief="sunken", anchor="w").pack(
            side="left", fill="x", expand=True, padx=(8, 0))

    # ---- 文件模式事件 ----
    def browse_dat(self):
        p = filedialog.askopenfilename(title="选择雷达数据文件",
                                       filetypes=[("雷达数据", "*.dat"), ("所有文件", "*.*")])
        if p:
            self.dat_path.set(p)
            self._autopick_ini(p)

    def browse_ini(self):
        p = filedialog.askopenfilename(title="选择配置文件",
                                       filetypes=[("配置文件", "*.ini"), ("所有文件", "*.*")])
        if p:
            self.ini_path.set(p)

    def _autopick_ini(self, dat_path):
        d = os.path.dirname(dat_path)
        cand = os.path.join(d, "FILE_Radar_Config.ini")
        if os.path.isfile(cand):
            self.ini_path.set(cand)
            return
        if os.path.isdir(d):
            inis = [f for f in os.listdir(d) if f.lower().endswith(".ini")]
            if inis:
                self.ini_path.set(os.path.join(d, inis[0]))

    def parse(self):
        dat = self.dat_path.get().strip()
        if not dat or not os.path.isfile(dat):
            messagebox.showwarning("提示", "请先选择一个有效的 .dat 文件")
            return
        self.file_status.set("解析中，请稍候...")
        self.root.update_idletasks()
        try:
            self.config = rp.parse_config(self.ini_path.get().strip())
            self.chirps, stats = rp.parse_dat(dat, self.config)
            self._rebuild_points()
            self.n_frames = stats["n_frames"]
            self.rt_matrix = rp.range_time_matrix(self.chirps, self.config, self.n_frames)
            self.frame_spin.config(to=max(0, self.n_frames - 1))
            self.frame_index.set(0)
            self.update_plots()
            self.file_status.set(
                f"完成 | {stats['n_chirps']} chirp · {self.n_frames} 帧 · "
                f"{len(self.frames)} 点 | {self.config.summary()}")
        except Exception as e:
            self.file_status.set("解析失败")
            messagebox.showerror("错误", f"解析失败：\n{e}")

    def _rebuild_points(self):
        self.frames, self.ranges, self.angles, self.amps, self.bins = rp.build_point_arrays(
            self.chirps, self.config, exclude_bin0=self.exclude_dc.get())

    def on_param_change(self):
        if self.chirps is None:
            return
        self._rebuild_points()
        self.update_plots()

    def on_frame_change(self):
        if self.chirps is None:
            return
        self._draw_frame_ax()
        self.canvas.draw_idle()

    def toggle_play(self):
        if self.chirps is None:
            return
        self.playing = not self.playing
        self.play_btn.config(text="暂停" if self.playing else "播放")
        if self.playing:
            self._play_step()

    def _play_step(self):
        if not self.playing or self.chirps is None:
            return
        cur = self.frame_index.get()
        nxt = cur + 1 if cur < self.n_frames - 1 else 0
        self.frame_index.set(nxt)
        self._draw_frame_ax()
        self.canvas.draw_idle()
        self.root.after(150, self._play_step)

    def update_plots(self):
        if self.chirps is None:
            return
        scale = self.scale.get()
        mirror = -1.0 if self.mirror.get() else 1.0
        rng = self.ranges * scale
        x = rng * np.sin(np.radians(self.angles)) * mirror
        y = rng * np.cos(np.radians(self.angles))

        self.ax_bev.clear()
        self.ax_bev.set_title("BEV Top View")
        self.ax_bev.set_xlabel("X (m)"); self.ax_bev.set_ylabel("Y (m)")
        self.ax_bev.set_xlim(-MAX_RANGE, MAX_RANGE); self.ax_bev.set_ylim(0, MAX_RANGE)
        self.ax_bev.set_aspect("equal")
        _draw_heat2d(self.ax_bev, x, y, self.amps, 120, 60,
                     [-MAX_RANGE, MAX_RANGE], [0, MAX_RANGE])
        self.ax_bev.scatter([0], [0], marker="*", s=140, c="lime", edgecolors="k")

        self.ax_ra.clear()
        self.ax_ra.set_title("Range-Angle")
        self.ax_ra.set_xlabel("Angle (deg)"); self.ax_ra.set_ylabel("Range (m)")
        _draw_heat2d(self.ax_ra, self.angles, rng, self.amps, 180, 50,
                     [-90, 90], [0, MAX_RANGE])

        self.ax_rt.clear()
        self.ax_rt.set_title("Range-Time")
        self.ax_rt.set_xlabel("Frame"); self.ax_rt.set_ylabel("Bin")
        if self.rt_matrix is not None and self.rt_matrix.size:
            self.ax_rt.imshow(self.rt_matrix.T, aspect="auto", origin="lower", cmap="inferno",
                              norm=_log_norm(self.rt_matrix))
            self.ax_rt.set_yticks(range(self.config.n_bin))
            self.ax_rt.set_yticklabels([str(i) for i in range(self.config.n_bin)])

        self._draw_frame_ax()
        self.canvas.draw_idle()

    def _draw_frame_ax(self):
        self.ax_fr.clear()
        self.ax_fr.set_xlabel("X (m)"); self.ax_fr.set_ylabel("Y (m)")
        self.ax_fr.set_xlim(-MAX_RANGE, MAX_RANGE); self.ax_fr.set_ylim(0, MAX_RANGE)
        self.ax_fr.set_aspect("equal")
        self.ax_fr.set_title("Single Frame BEV")
        if self.chirps is None:
            return
        scale = self.scale.get()
        mirror = -1.0 if self.mirror.get() else 1.0
        fi = self.frame_index.get()
        mask = self.frames == fi
        if np.any(mask):
            rng = self.ranges[mask] * scale
            x = rng * np.sin(np.radians(self.angles[mask])) * mirror
            y = rng * np.cos(np.radians(self.angles[mask]))
            self.ax_fr.scatter(x, y, c=np.log10(self.amps[mask] + 1), cmap="inferno",
                               s=18, edgecolors="none")
        self.ax_fr.scatter([0], [0], marker="*", s=140, c="lime", edgecolors="k")
        self.ax_fr.set_title(f"Single Frame BEV (frame {fi})")

    def export_csv(self):
        if self.chirps is None:
            messagebox.showinfo("提示", "请先解析数据")
            return
        p = filedialog.asksaveasfilename(title="导出点云 CSV", defaultextension=".csv",
                                         filetypes=[("CSV 文件", "*.csv")])
        if not p:
            return
        try:
            self._write_csv(p, self.frames, self.ranges, self.angles, self.amps)
            self.file_status.set(f"已导出 CSV：{p}")
            messagebox.showinfo("完成", f"已导出 {len(self.frames)} 个点到：\n{p}")
        except Exception as e:
            messagebox.showerror("错误", f"导出失败：\n{e}")

    def export_heatmap_file(self):
        if self.chirps is None:
            messagebox.showinfo("提示", "请先解析数据")
            return
        scale = self.scale.get()
        mirror = -1.0 if self.mirror.get() else 1.0
        rng = self.ranges * scale
        x = rng * np.sin(np.radians(self.angles)) * mirror
        y = rng * np.cos(np.radians(self.angles))
        self._export_heatmap(x, y, self.amps, "BEV Top View", self.file_status)

    # ================= 串口实时标签页 =================
    def _build_live_tab(self):
        top = ttk.LabelFrame(self.live_tab, text="串口设置", padding=8)
        top.pack(fill="x", padx=10, pady=(10, 4))

        ttk.Label(top, text="串口:").grid(row=0, column=0, sticky="e", padx=(0, 4))
        self.port_combo = ttk.Combobox(top, textvariable=self.live_port, width=16)
        self.port_combo.grid(row=0, column=1, padx=4)
        ttk.Button(top, text="刷新", command=self.refresh_ports).grid(row=0, column=2, padx=4)

        ttk.Label(top, text="波特率:").grid(row=0, column=3, sticky="e", padx=(12, 4))
        ttk.Combobox(top, textvariable=self.live_baud, values=BAUD_CHOICES, width=10).grid(
            row=0, column=4, padx=4)

        ttk.Label(top, text="配置:").grid(row=0, column=5, sticky="e", padx=(12, 4))
        ttk.Entry(top, textvariable=self.live_ini, width=36).grid(row=0, column=6, sticky="we", padx=4)
        ttk.Button(top, text="...", width=3, command=self.browse_live_ini).grid(row=0, column=7, padx=4)

        self.connect_btn = ttk.Button(top, text="连接", command=self.toggle_connect)
        self.connect_btn.grid(row=0, column=8, padx=(8, 0))
        ttk.Button(top, text="导出 CSV", command=self.export_live_csv).grid(row=0, column=9, padx=4)
        ttk.Button(top, text="导出热图", command=self.export_heatmap_live).grid(row=0, column=10, padx=4)
        self.record_btn = ttk.Button(top, text="录制", command=self.toggle_recording)
        self.record_btn.grid(row=0, column=11, padx=4)
        top.columnconfigure(6, weight=1)

        self.live_fig = Figure(figsize=(11.5, 7.0), dpi=100)
        self.lax_bev = self.live_fig.add_subplot(2, 2, 1)
        self.lax_ra = self.live_fig.add_subplot(2, 2, 2)
        self.lax_rt = self.live_fig.add_subplot(2, 2, 3)
        self.lax_fr = self.live_fig.add_subplot(2, 2, 4)
        self._init_live_axes()
        self.live_fig.tight_layout(pad=2.5)
        self.live_canvas = FigureCanvasTkAgg(self.live_fig, master=self.live_tab)
        self.live_canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=4)
        NavigationToolbar2Tk(self.live_canvas, self.live_tab).update()
        # 尺寸变化或任何全量重绘（含工具栏缩放/平移）后，blit 背景需重新捕获
        self.live_canvas.mpl_connect("resize_event", self._on_live_resize)
        self.live_canvas.mpl_connect("draw_event", self._on_live_draw_event)

        bottom = ttk.Frame(self.live_tab)
        bottom.pack(fill="x", padx=10, pady=(4, 8))
        ttk.Label(bottom, textvariable=self.live_status, relief="sunken", anchor="w").pack(
            fill="x", expand=True)

        self.refresh_ports()

    def _init_live_axes(self):
        """预创建实时图 artist（后续仅 set_data/set_offsets 更新，避免反复 clear 重建）。"""
        # BEV 热图
        self._bev_img = self.lax_bev.imshow(
            np.zeros((60, 120)), extent=[-MAX_RANGE, MAX_RANGE, 0, MAX_RANGE],
            origin="lower", cmap="inferno", norm=LogNorm(), aspect="auto")
        self.lax_bev.set_title("BEV Top View (live)")
        self.lax_bev.set_xlabel("X (m)"); self.lax_bev.set_ylabel("Y (m)")
        self.lax_bev.set_xlim(-MAX_RANGE, MAX_RANGE); self.lax_bev.set_ylim(0, MAX_RANGE)
        self.lax_bev.set_aspect("equal")
        self._bev_origin = self.lax_bev.scatter([0], [0], marker="*", s=140, c="lime",
                                                edgecolors="k", zorder=5)
        # 距离-角度热图
        self._ra_img = self.lax_ra.imshow(
            np.zeros((50, 180)), extent=[-90, 90, 0, MAX_RANGE],
            origin="lower", cmap="inferno", norm=LogNorm(), aspect="auto")
        self.lax_ra.set_title("Range-Angle (live)")
        self.lax_ra.set_xlabel("Angle (deg)"); self.lax_ra.set_ylabel("Range (m)")
        # 距离-时间（固定 50 帧窗口）
        self._rt_img = self.lax_rt.imshow(
            np.zeros((8, 50)), aspect="auto", origin="lower", cmap="inferno", norm=LogNorm())
        self.lax_rt.set_title("Range-Time (live)")
        self.lax_rt.set_xlabel("Frame"); self.lax_rt.set_ylabel("Bin")
        self.lax_rt.set_yticks(range(8))
        # 最新单帧散点
        self._fr_scatter = self.lax_fr.scatter([], [], c=[], cmap="inferno", s=18,
                                               edgecolors="none")
        self._fr_origin = self.lax_fr.scatter([0], [0], marker="*", s=140, c="lime",
                                              edgecolors="k", zorder=5)
        self.lax_fr.set_title("Latest Frame BEV")
        self.lax_fr.set_xlabel("X (m)"); self.lax_fr.set_ylabel("Y (m)")
        self.lax_fr.set_xlim(-MAX_RANGE, MAX_RANGE); self.lax_fr.set_ylim(0, MAX_RANGE)
        self.lax_fr.set_aspect("equal")

    def refresh_ports(self):
        ports = sl.list_serial_ports()
        self.port_combo["values"] = [p[0] for p in ports]
        if ports and not self.live_port.get():
            self.live_port.set(ports[0][0])

    def browse_live_ini(self):
        p = filedialog.askopenfilename(title="选择配置文件",
                                       filetypes=[("配置文件", "*.ini"), ("所有文件", "*.*")])
        if p:
            self.live_ini.set(p)

    def toggle_connect(self):
        if self.live_connected:
            self._disconnect()
        else:
            self._connect()

    def toggle_recording(self):
        if self.live_reader is None or not self.live_connected:
            messagebox.showinfo("提示", "请先连接串口")
            return
        reader = self.live_reader
        if reader.recording:
            reader.stop_recording()
            self.record_btn.config(text="录制")
            self.live_status.set(f"{self.live_status.get()} · 录制已停止")
        else:
            p = filedialog.asksaveasfilename(
                title="录制原始数据为 .dat", defaultextension=".dat",
                filetypes=[("雷达数据", "*.dat"), ("所有文件", "*.*")])
            if not p:
                return
            reader.start_recording(p)
            self.record_btn.config(text="停止录制")
            self.live_status.set(f"录制中 → {p}")

    def _connect(self):
        port = self.live_port.get().strip()
        if not port:
            messagebox.showwarning("提示", "请先选择串口")
            return
        try:
            baud = int(self.live_baud.get().strip())
        except ValueError:
            messagebox.showwarning("提示", "波特率无效")
            return
        cfg = rp.parse_config(self.live_ini.get().strip())
        try:
            self.live_reader = sl.SerialRadarReader(port, baud, cfg)
            self.live_reader.start()
        except Exception as e:
            self.live_reader = None
            self.live_status.set(f"连接失败：{e}")
            messagebox.showerror("连接失败", f"无法打开串口 {port}：\n{e}")
            return
        self.live_connected = True
        self.live_t0 = time.time()
        self.connect_btn.config(text="断开")
        self.live_status.set(f"已连接 {port} @ {baud} | {cfg.summary()}")

    def _disconnect(self):
        if self.live_reader is not None:
            self.live_reader.stop()
        self.live_reader = None
        self.live_connected = False
        self.connect_btn.config(text="连接")
        self.record_btn.config(text="录制")
        self.live_status.set("已断开")

    def _on_live_resize(self, event):
        self._live_bg = None

    def _on_live_draw_event(self, event):
        # 任何全量重绘（工具栏缩放/平移等）后，blit 背景失效，下次重新捕获
        self._live_bg = None

    def _smooth_clim(self, tag, c, alpha=0.5):
        """对热图 clim 做 EMA 平滑，避免逐帧颜色剧烈抖动（提高显示稳定性）。"""
        prev = self._clim_ema.get(tag)
        if c is None:
            return prev
        if prev is None:
            self._clim_ema[tag] = c
            return c
        new = (prev[0] * (1 - alpha) + c[0] * alpha, prev[1] * (1 - alpha) + c[1] * alpha)
        self._clim_ema[tag] = new
        return new

    def _live_draw(self):
        """增量重绘实时图：优先 blit（只重绘 4 个动态 artist），失败回退全量 draw_idle。"""
        canvas = self.live_canvas
        if self._live_bg is None:
            canvas.draw()
            self._live_bg = canvas.copy_from_bbox(self.live_fig.bbox)
            return
        try:
            canvas.restore_region(self._live_bg)
            fig = self.live_fig
            fig.draw_artist(self._bev_img)
            fig.draw_artist(self._bev_origin)
            fig.draw_artist(self._ra_img)
            fig.draw_artist(self._rt_img)
            fig.draw_artist(self._fr_scatter)
            fig.draw_artist(self._fr_origin)
            canvas.blit(self.live_fig.bbox)
        except Exception:
            self._live_bg = None
            canvas.draw_idle()

    def _live_loop(self):
        if self.live_connected and self.live_reader is not None:
            t0 = time.time()
            self._update_live_plots()
            # 自适应间隔：目标 LIVE_REFRESH_MS，扣除本轮渲染耗时，避免事件堆积
            cost = time.time() - t0
            delay = max(5, int(LIVE_REFRESH_MS - cost * 1000))
            # 3D 标签页节流刷新（约 1 秒一次，仅当用户正停留在 3D 页时）
            now = time.time()
            if now - self._last_3d_update > 1.0 and self.notebook.index("current") == 2:
                self._update_3d()
                self._last_3d_update = now
        else:
            delay = 200  # 未连接时低频轮询，降低空闲 CPU 占用
        self.root.after(delay, self._live_loop)

    def _update_live_plots(self):
        cfg = self.live_reader.config
        cpf = cfg.chirps_per_frame
        # 只取最近 RT_WINDOW 帧多一点（BEV/RA/RT/最新帧都够用），避免复制整个滚动缓冲
        chirps, stats = self.live_reader.snapshot(n=RT_WINDOW * cpf + cpf)
        now = time.time()

        # 瞬时帧率（EMA 平滑，反映实时刷新，而非长时间累计平均）
        dframes = stats["frames"] - self._last_frames
        dt = now - self._last_fps_t
        if self._last_fps_t > 0 and dt > 0:
            inst = dframes / dt
            self._fps_ema = inst if self._fps_ema == 0 else 0.7 * self._fps_ema + 0.3 * inst
        self._last_frames = stats["frames"]
        self._last_fps_t = now

        if not chirps:
            self.live_status.set(
                f"已连接，等待数据... | {stats['chirps']} chirp · {stats['frames']} 帧 · "
                f"{stats['bytes']} B · 错误 {stats['errors']}")
            return
        scale = self.scale.get()
        mirror = -1.0 if self.mirror.get() else 1.0

        gmax = chirps[-1][0] // cpf

        # BEV / 距离-角度：只取最近 HEAT_FRAMES 帧（场景快照，避免对全部滚动缓冲做直方图）
        heat_chirps = chirps[-(HEAT_FRAMES * cpf):]
        frames, ranges, angles, amps, bins = rp.build_point_arrays(heat_chirps, cfg, exclude_bin0=True)
        rng = ranges * scale
        x = rng * np.sin(np.radians(angles)) * mirror
        y = rng * np.cos(np.radians(angles))

        # BEV（复用 imshow artist）
        H, _, _ = np.histogram2d(x, y, bins=[120, 60],
                                 range=[[-MAX_RANGE, MAX_RANGE], [0, MAX_RANGE]], weights=amps)
        self._bev_img.set_data(np.ma.masked_where(H <= 0, H).T)
        c = self._smooth_clim("bev", _log_clim(H))
        if c:
            self._bev_img.set_clim(*c)

        # 距离-角度（复用）
        H, _, _ = np.histogram2d(angles, rng, bins=[180, 50],
                                 range=[[-90, 90], [0, MAX_RANGE]], weights=amps)
        self._ra_img.set_data(np.ma.masked_where(H <= 0, H).T)
        c = self._smooth_clim("ra", _log_clim(H))
        if c:
            self._ra_img.set_clim(*c)

        # 距离-时间（最近 RT_WINDOW 帧窗口，含 bin0，向量化）
        nw = RT_WINDOW
        rt_chirps = chirps[-(nw * cpf):]
        gch = np.fromiter((c[0] for c in rt_chirps), dtype=np.int64, count=len(rt_chirps))
        rel = (gch // cpf) - (gmax - nw + 1)
        R0 = np.stack([c[1] for c in rt_chirps])
        R1 = np.stack([c[2] for c in rt_chirps])
        amp = (np.abs(R0) ** 2 + np.abs(R1) ** 2) / 2.0
        rt = np.zeros((nw, cfg.n_bin))
        valid = (rel >= 0) & (rel < nw)
        np.add.at(rt, rel[valid], amp[valid])
        self._rt_img.set_data(np.ma.masked_where(rt <= 0, rt).T)
        c = self._smooth_clim("rt", _log_clim(rt))
        if c:
            self._rt_img.set_clim(*c)
        if not getattr(self, "_rt_ticks_init", False):
            self.lax_rt.set_yticks(range(cfg.n_bin))
            self.lax_rt.set_yticklabels([str(i) for i in range(cfg.n_bin)])
            self._rt_ticks_init = True

        # 最新单帧散点（复用 scatter artist）
        mask = frames == gmax
        if np.any(mask):
            rr = ranges[mask] * scale
            xx = rr * np.sin(np.radians(angles[mask])) * mirror
            yy = rr * np.cos(np.radians(angles[mask]))
            cc = np.log10(amps[mask] + 1)
            self._fr_scatter.set_offsets(np.column_stack([xx, yy]))
            self._fr_scatter.set_array(cc)
            self._fr_scatter.set_clim(cc.min(), cc.max())
        else:
            self._fr_scatter.set_offsets(np.zeros((0, 2)))
            self._fr_scatter.set_array(np.array([]))

        self._live_draw()

        self.live_status.set(
            f"实时 | {self._fps_ema:.1f} 帧/s · 最新帧 {gmax} · {stats['chirps']} chirp · "
            f"{stats['bytes']/1024:.0f} KB · 错误 {stats['errors']}")

    def export_live_csv(self):
        if self.live_reader is None:
            messagebox.showinfo("提示", "请先连接串口")
            return
        chirps, stats = self.live_reader.snapshot()
        if not chirps:
            messagebox.showinfo("提示", "暂无可导出的数据")
            return
        cfg = self.live_reader.config
        frames, ranges, angles, amps, bins = rp.build_point_arrays(chirps, cfg, exclude_bin0=True)
        p = filedialog.asksaveasfilename(title="导出实时点云 CSV", defaultextension=".csv",
                                         filetypes=[("CSV 文件", "*.csv")])
        if not p:
            return
        try:
            self._write_csv(p, frames, ranges, angles, amps)
            self.live_status.set(f"已导出 {len(frames)} 点 → {p}")
            messagebox.showinfo("完成", f"已导出 {len(frames)} 个点到：\n{p}")
        except Exception as e:
            messagebox.showerror("错误", f"导出失败：\n{e}")

    def export_heatmap_live(self):
        if self.live_reader is None:
            messagebox.showinfo("提示", "请先连接串口")
            return
        chirps, stats = self.live_reader.snapshot()
        if not chirps:
            messagebox.showinfo("提示", "暂无可导出的数据")
            return
        cfg = self.live_reader.config
        cpf = cfg.chirps_per_frame
        heat_chirps = chirps[-(HEAT_FRAMES * cpf):]
        _, ranges, angles, amps, _ = rp.build_point_arrays(heat_chirps, cfg, exclude_bin0=True)
        scale = self.scale.get()
        mirror = -1.0 if self.mirror.get() else 1.0
        rng = ranges * scale
        x = rng * np.sin(np.radians(angles)) * mirror
        y = rng * np.cos(np.radians(angles))
        self._export_heatmap(x, y, amps, "BEV Top View (live)", self.live_status)

    # ================= 3D 点云标签页 =================
    def _build_3d_tab(self):
        top = ttk.LabelFrame(self.tab3d, text="3D 点云（Z = 信号幅度 · 伪高度）", padding=8)
        top.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(top, text="帧(-1=全部累积):").grid(row=0, column=0, sticky="e")
        self.threed_spin = ttk.Spinbox(top, from_=-1, to=100000, textvariable=self.threed_frame,
                                       width=8, command=self._update_3d)
        self.threed_spin.grid(row=0, column=1, padx=4)
        ttk.Button(top, text="更新", command=self._update_3d).grid(row=0, column=2, padx=4)
        ttk.Label(top, text="x/y = 真实水平坐标 · z/颜色 = log10(幅度)（伪高度，非真实高度）",
                  foreground="gray").grid(row=0, column=3, padx=12)

        self.fig3d = Figure(figsize=(9.0, 7.0), dpi=100)
        self.ax3d = self.fig3d.add_subplot(111, projection="3d")
        self.ax3d.set_xlabel("X (m)")
        self.ax3d.set_ylabel("Y (m)")
        self.ax3d.set_zlabel("log10(amp) pseudo-height")
        self.canvas3d = FigureCanvasTkAgg(self.fig3d, master=self.tab3d)
        self.canvas3d.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=4)
        NavigationToolbar2Tk(self.canvas3d, self.tab3d).update()

        bottom = ttk.Frame(self.tab3d)
        bottom.pack(fill="x", padx=10, pady=(4, 8))
        ttk.Label(bottom, textvariable=self.threed_status, relief="sunken", anchor="w").pack(
            fill="x", expand=True)

    def _collect_3d_points(self):
        """返回 (x, y, z, label)；z 为 log10(幅度)。实时优先，其次文件。"""
        mirror = -1.0 if self.mirror.get() else 1.0
        scale = self.scale.get()
        fi = self.threed_frame.get()
        if self.live_connected and self.live_reader is not None:
            chirps, stats = self.live_reader.snapshot()
            if not chirps:
                return None
            cfg = self.live_reader.config
            _, ranges, angles, amps, _ = rp.build_point_arrays(chirps, cfg, exclude_bin0=True)
            label = f"live · {stats['chirps']} chirps / {stats['frames']} frames"
        elif self.chirps is not None:
            ranges, angles, amps = self.ranges, self.angles, self.amps
            if fi >= 0:
                m = self.frames == fi
                if not np.any(m):
                    return None
                ranges, angles, amps = self.ranges[m], self.angles[m], self.amps[m]
                label = f"file · frame {fi}"
            else:
                label = f"file · all {self.n_frames} frames"
        else:
            return None
        rng = ranges * scale
        x = rng * np.sin(np.radians(angles)) * mirror
        y = rng * np.cos(np.radians(angles))
        z = np.log10(amps + 1.0)
        return x, y, z, label

    def _update_3d(self):
        pts = self._collect_3d_points()
        self.ax3d.clear()
        self.ax3d.set_xlabel("X (m)")
        self.ax3d.set_ylabel("Y (m)")
        self.ax3d.set_zlabel("log10(amp) pseudo-height")
        if pts is None:
            self.threed_status.set("暂无数据：请先解析 .dat 或连接串口")
            self.canvas3d.draw_idle()
            return
        x, y, z, label = pts
        if len(x) == 0:
            self.threed_status.set("该帧无有效点")
            self.canvas3d.draw_idle()
            return
        # 降采样保护 3D 渲染性能（>15000 点做等间隔抽样）
        if len(x) > 15000:
            step = int(np.ceil(len(x) / 15000))
            x, y, z = x[::step], y[::step], z[::step]
        self.ax3d.scatter(x, y, z, c=z, cmap="inferno", s=10, depthshade=True)
        self.ax3d.set_xlim(-MAX_RANGE, MAX_RANGE)
        self.ax3d.set_ylim(-MAX_RANGE, MAX_RANGE)
        self.ax3d.set_zlim(0, None)
        self.ax3d.set_title("3D point cloud | " + label)
        self.threed_status.set(f"{label} | {len(x)} 点（z=log10 幅度，伪高度）")
        self.canvas3d.draw_idle()

    # ================= 公共 =================
    def _build_bev_heatmap(self, x, y, amps):
        """BEV 加权二维直方图，返回 (H, xedges, yedges)。"""
        H, xe, ye = np.histogram2d(x, y, bins=[120, 60],
                                   range=[[-MAX_RANGE, MAX_RANGE], [0, MAX_RANGE]], weights=amps)
        return H, xe, ye

    def _export_heatmap(self, x, y, amps, title, status_var):
        """导出 BEV 热图：PNG 图片 + 同名 CSV 数值矩阵。"""
        if len(x) == 0:
            messagebox.showinfo("提示", "暂无可导出的热图数据")
            return
        base = filedialog.asksaveasfilename(
            title="导出热图（PNG 图片，同名 CSV 数值矩阵会自动生成）",
            defaultextension=".png",
            filetypes=[("PNG 图片", "*.png")])
        if not base:
            return
        try:
            H, xe, ye = self._build_bev_heatmap(x, y, amps)
            # PNG
            fig = Figure(figsize=(6.4, 6.4), dpi=150)
            ax = fig.add_subplot(111)
            Hm = np.ma.masked_where(H <= 0, H)
            im = ax.imshow(Hm.T, extent=[xe[0], xe[-1], ye[0], ye[-1]],
                           origin="lower", cmap="inferno", norm=_log_norm(H), aspect="auto")
            ax.set_xlabel("X (m)"); ax.set_ylabel("Y (m)")
            ax.set_title(title)
            fig.colorbar(im, ax=ax, label="amplitude")
            fig.savefig(base, dpi=150, bbox_inches="tight")
            # CSV 数值矩阵（同名 .csv）——保留原始数值（空 bin 为 0）
            csv_path = os.path.splitext(base)[0] + ".csv"
            self._write_heatmap_csv(csv_path, H, xe, ye)
            status_var.set(f"已导出热图：{base} + {csv_path}")
            messagebox.showinfo("完成", f"已导出：\n{base}\n{csv_path}")
        except Exception as e:
            messagebox.showerror("错误", f"导出失败：\n{e}")

    def _write_heatmap_csv(self, path, H, xe, ye):
        """保存热图数值矩阵：行=Y(距离) bin 中心，列=X(方位) bin 中心。"""
        M = H.T  # (ny, nx) = (60, 120)
        ny, nx = M.shape
        xc = (xe[:-1] + xe[1:]) / 2.0
        yc = (ye[:-1] + ye[1:]) / 2.0
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["y_m\\x_m"] + [round(float(v), 3) for v in xc])
            for j in range(ny):
                w.writerow([round(float(yc[j]), 3)] + [round(float(v), 1) for v in M[j]])

    def _write_csv(self, path, frames, ranges, angles, amps):
        mirror = -1.0 if self.mirror.get() else 1.0
        scale = self.scale.get()
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["frame", "x_m", "y_m", "range_m", "angle_deg", "amplitude"])
            for fr, rng, ang, amp in zip(frames, ranges, angles, amps):
                rr = rng * scale
                x = rr * np.sin(np.radians(ang)) * mirror
                y = rr * np.cos(np.radians(ang))
                w.writerow([int(fr), round(float(x), 4), round(float(y), 4),
                            round(float(rr), 4), round(float(ang), 2), round(float(amp), 1)])

    def on_close(self):
        if self.live_connected:
            self._disconnect()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = RadarApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()

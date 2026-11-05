"""后台采集引擎：在独立线程跑数据源 + 判定器，维护网页所需的全部状态。

支持运行时切换数据源（实时串口 / 文件回放），线程安全。
事件：presence 翻转（进入/离开）和动作分级变化，带本地时间戳，
并持久化到 JSON 文件（重启不丢，可在页面清空）。
回放结束后生成整段分析 analysis（帧数、时长、有人占比、动作分布、进出次数）。
"""
import json
import os
import threading
import time
from collections import deque
from datetime import datetime

from src.detector import Detector

SERIES_LEN = 2400          # 最近约 5s 的曲线点（480fps）
EVENTS_LIMIT = 5000        # 事件最多保留条数
FPS = 480                  # 逻辑帧率，用于把帧数换算成时长


class MonitorEngine:
    def __init__(self, source, cfg, history_path, pace=False, mode="live", source_name=""):
        self.cfg = cfg
        self.history_path = history_path
        self.frame_interval = 1.0 / FPS

        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()

        self.source = source
        self.pace = pace
        self.mode = mode                 # "live" 实时 / "replay" 回放
        self.source_name = source_name

        self.detector = Detector(cfg)
        self.latest = None
        self.series = deque(maxlen=SERIES_LEN)
        self.events = self._load_history()
        self.frame_count = 0
        self.started_at = time.time()
        self.finished = False
        self.analysis = None

        self._prev_presence = None
        self._prev_level = None

        # 整段统计
        self._n_present = 0
        self._levels = [0, 0, 0]
        self._n_enter = 0
        self._n_leave = 0
        self._wall_start = ""

    # ---------- 持久化 ----------
    def _load_history(self):
        if self.history_path and os.path.exists(self.history_path):
            try:
                with open(self.history_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data[-EVENTS_LIMIT:]
            except (ValueError, OSError):
                pass
        return []

    def _save_history(self):
        if not self.history_path:
            return
        tmp = self.history_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.events, f, ensure_ascii=False)
            os.replace(tmp, self.history_path)
        except OSError:
            pass

    # ---------- 生命周期 ----------
    def start(self):
        self._thread = threading.Thread(target=self.run, name="monitor", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def switch_source(self, source, pace, mode, source_name):
        """停止当前任务，重置状态，用新数据源重新启动。"""
        if self._thread is not None and self._thread.is_alive():
            self._stop.set()
            self._thread.join(timeout=3.0)

        with self._lock:
            self.source = source
            self.pace = pace
            self.mode = mode
            self.source_name = source_name
            self.detector = Detector(self.cfg)
            self.latest = None
            self.series = deque(maxlen=SERIES_LEN)
            self.frame_count = 0
            self.finished = False
            self.analysis = None
            self._prev_presence = None
            self._prev_level = None
            self._n_present = 0
            self._levels = [0, 0, 0]
            self._n_enter = 0
            self._n_leave = 0
            self._wall_start = ""

        self._stop = threading.Event()
        self.start()

    def run(self):
        self.started_at = time.time()
        self._wall_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for frame in self.source:
            if self._stop.is_set():
                break
            d = self.detector.update(frame)
            now = time.time()
            new_events = []

            with self._lock:
                self.latest = d.as_dict()
                self.latest["wall_clock"] = datetime.now().strftime("%H:%M:%S")
                self.series.append({
                    "t": now,
                    "range": d.short_range_energy,
                    "motion": d.short_motion_energy,
                    "presence": 1 if d.presence else 0,
                    "level": d.motion_level,
                })
                self.frame_count += 1
                if d.presence:
                    self._n_present += 1
                    self._levels[d.motion_level] += 1

                if self._prev_presence is not None and d.presence != self._prev_presence:
                    if d.presence:
                        self._n_enter += 1
                    else:
                        self._n_leave += 1
                    new_events.append(self._event(
                        "进入" if d.presence else "离开", frame.index, d))
                if d.presence and self._prev_level is not None and d.motion_level != self._prev_level:
                    new_events.append(self._event(
                        "动作·" + d.motion_label, frame.index, d))
                self._prev_presence = d.presence
                self._prev_level = d.motion_level

                if new_events:
                    self.events.extend(new_events)
                    self.events = self.events[-EVENTS_LIMIT:]

            if new_events:
                self._save_history()

            if self.pace and self.frame_interval:
                time.sleep(self.frame_interval)

        with self._lock:
            self.finished = True
            self.analysis = self._build_analysis()
        self._save_history()

    def _build_analysis(self):
        n = self.frame_count
        if n == 0:
            return None
        present_ratio = self._n_present / n * 100
        tot_levels = sum(self._levels) or 1
        return {
            "source_name": self.source_name,
            "total_frames": n,
            "duration_sec": round(n / FPS, 1),
            "present_ratio": round(present_ratio, 1),
            "level_dist": {
                "不动": round(self._levels[0] / tot_levels * 100, 1),
                "小动": round(self._levels[1] / tot_levels * 100, 1),
                "大动": round(self._levels[2] / tot_levels * 100, 1),
            },
            "enter_count": self._n_enter,
            "leave_count": self._n_leave,
            "final_presence": bool(self.detector.presence),
            "wall_start": self._wall_start,
            "wall_end": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _event(self, kind, frame_index, d):
        return {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ts": round(time.time(), 3),
            "kind": kind,
            "frame": frame_index,
            "presence": 1 if d.presence else 0,
            "level": d.motion_level,
            "label": d.motion_label,
        }

    # ---------- 给 HTTP 的读取接口 ----------
    def snapshot(self):
        with self._lock:
            uptime = time.time() - self.started_at
            return {
                "mode": self.mode,
                "source_name": self.source_name,
                "latest": self.latest,
                "series": list(self.series),
                "frame_count": self.frame_count,
                "uptime": round(uptime, 1),
                "finished": self.finished,
                "event_count": len(self.events),
                "analysis": self.analysis,
            }

    def get_events(self):
        with self._lock:
            return list(self.events)

    def clear_events(self):
        with self._lock:
            self.events = []
        self._save_history()
        return True

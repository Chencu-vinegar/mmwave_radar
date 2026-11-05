"""本地网页演示与记录包。

用标准库 http.server 起本地服务，浏览器打开即可看到实时状态、
动作分级、能量曲线和进出事件记录，支持实时串口 / 回放 / 合成三种数据源。
"""
from .engine import MonitorEngine
from .server import WebServer

__all__ = ["MonitorEngine", "WebServer"]

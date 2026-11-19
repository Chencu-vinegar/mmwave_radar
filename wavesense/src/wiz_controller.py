"""WiZ 灯泡本地直连控制器（pywizlight，UDP 局域网，不走云 / Home Assistant）。

同步封装：引擎后台线程和网页测试按钮都能安全调用。
pywizlight 是异步库，这里每次命令用独立事件循环（asyncio.run）执行，
不与引擎线程共享 loop；命令加锁，避免并发指令互相干扰。

未安装库 / 未配置 IP / 网络不通时，返回 (ok, msg) 并记录 last_result，
不抛异常打断主流程。灯泡返回的 JSON-RPC 错误（如 -32602 Invalid params）
原样带到前端，便于排查签名 / 本地控制权限问题。
"""
import asyncio
import re
import threading
from datetime import datetime

try:
    from pywizlight import wizlight, PilotBuilder
except ImportError:  # 未安装依赖时降级，主流程不受影响
    wizlight = None
    PilotBuilder = None

IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
DEFAULT_TIMEOUT = 6.0


def normalize_ip(ip):
    """规整并校验 IPv4 字符串；非法返回 None。"""
    if not ip:
        return None
    ip = str(ip).strip()
    if not IP_RE.match(ip):
        return None
    parts = ip.split(".")
    if all(0 <= int(p) <= 255 for p in parts):
        return ip
    return None


class WizLightController:
    """单台 WiZ 灯泡的本地控制封装。"""

    def __init__(self, ip=None, brightness=200, warm_white=255,
                 signing_key=None, timeout=DEFAULT_TIMEOUT):
        self._lock = threading.Lock()
        self.ip = normalize_ip(ip)
        self.brightness = int(brightness or 200)
        self.warm_white = int(warm_white or 255)
        self.signing_key = (signing_key or "").strip() or None
        self.timeout = timeout
        self.last_result = None   # {"ok", "action", "msg", "time"}
        self.last_state = None    # 最近一次读到的灯泡开关态（探测时）

    # ---------- 配置 ----------
    def is_configured(self):
        return self.ip is not None

    def set_ip(self, ip):
        """保存并校验 IP；返回是否合法。"""
        self.ip = normalize_ip(ip)
        return self.ip is not None

    def set_signing_key(self, key):
        self.signing_key = (key or "").strip() or None

    # ---------- 内部异步 ----------
    def _make_bulb(self):
        if self.signing_key:
            return wizlight(self.ip, signing_key=self.signing_key)
        return wizlight(self.ip)

    async def _send(self, on):
        bulb = self._make_bulb()
        if on:
            builder = PilotBuilder(brightness=self.brightness,
                                   warm_white=self.warm_white)
            await asyncio.wait_for(bulb.turn_on(builder), self.timeout)
        else:
            await asyncio.wait_for(bulb.turn_off(), self.timeout)

    async def _probe(self):
        bulb = self._make_bulb()
        return await asyncio.wait_for(bulb.updateState(), self.timeout)

    # ---------- 对外同步命令 ----------
    def turn_on(self):
        return self._command(True)

    def turn_off(self):
        return self._command(False)

    def _command(self, on):
        action = "开灯" if on else "关灯"
        if wizlight is None:
            return self._record(False, action, "未安装 pywizlight 库（pip install pywizlight）")
        if not self.is_configured():
            return self._record(False, action, "未配置灯泡 IP")
        with self._lock:
            try:
                asyncio.run(self._send(on))
                return self._record(True, action, "指令已发送")
            except Exception as exc:  # 网络不通 / 超时 / 灯泡拒绝
                return self._record(False, action, self._fmt_err(exc))

    def test_connection(self):
        """读取灯泡当前状态，验证连通性；不改变开关。"""
        if wizlight is None:
            return self._record(False, "测试", "未安装 pywizlight 库")
        if not self.is_configured():
            return self._record(False, "测试", "未配置灯泡 IP")
        with self._lock:
            try:
                state = asyncio.run(self._probe())
                self.last_state = bool(getattr(state, "state", None))
                return self._record(True, "测试",
                                    f"已连上灯泡，当前{'开' if self.last_state else '关'}")
            except Exception as exc:
                return self._record(False, "测试", self._fmt_err(exc))

    # ---------- 状态 ----------
    def _record(self, ok, action, msg):
        self.last_result = {
            "ok": ok,
            "action": action,
            "msg": msg,
            "time": datetime.now().strftime("%H:%M:%S"),
        }
        return ok, msg

    @staticmethod
    def _fmt_err(exc):
        text = str(exc).strip()
        return (text[:200] if text else type(exc).__name__)

    def status_dict(self):
        return {
            "configured": self.is_configured(),
            "ip": self.ip or "",
            "library_ready": wizlight is not None,
            "last_state": self.last_state,
            "last_result": self.last_result,
        }

"""实时串口数据源：从 DEV202 连续读字节，实时产出 Frame。

硬件连接：雷达 → 4 根杜邦线 → USB 转 TTL 串口模组 → 电脑（见 docs/硬件连接方式.jpg）。
用 StreamParser 处理粘包 / 分包，判定逻辑与回放完全一致。
"""
import time

from .base import DataSource
from .stream_parser import StreamParser

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover
    serial = None
    list_ports = None


def list_serial_ports():
    """列出当前可用串口（device, description）。"""
    if list_ports is None:
        return []
    return [(p.device, p.description) for p in list_ports.comports()]


class SerialSource(DataSource):
    """实时读取串口 Frame。

    port  串口设备，如 /dev/tty.usbserial-110（Windows 为 COMx）
    baud  波特率（默认 921600；约 50KB/s 数据需要 ≥460800，以手册为准）
    """

    def __init__(self, port, baud=921600, read_size=4096, poll=0.005):
        self.port = port
        self.baud = baud
        self.read_size = read_size
        self.poll = poll

    def frames(self):
        if serial is None:
            raise RuntimeError("未安装 pyserial，请先运行：pip install pyserial")

        parser = StreamParser()
        empty_streak = 0
        with serial.Serial(self.port, self.baud, timeout=0.1) as ser:
            ser.reset_input_buffer()
            print("[串口] 已连接，等待雷达数据 ...")
            while True:
                data = ser.read(self.read_size)
                if data:
                    empty_streak = 0
                    for frame in parser.feed(data):
                        yield frame
                else:
                    empty_streak += 1
                    if empty_streak % 20 == 0:   # 约每 2 秒
                        print("[串口] 已连接但没收到数据：检查雷达供电 / 接线，或按 Ctrl+C 退出。")
                    time.sleep(self.poll)

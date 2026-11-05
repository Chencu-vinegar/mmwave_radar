# -*- coding: utf-8 -*-
"""serial_probe.py —— 串口原始字节探针（调试用，不做任何解析）。

用法（在 vibe 目录下）：
    python tools/serial_probe.py COM5 921600

作用：打开串口读 5 秒，把收到的原始字节统计几个关键帧头标记出现次数，并落盘
serial_capture.bin 供后续分析。
- ICLH (49 43 4c 48) 出现 N 次 → 实时流带 MCU 包装层，按 DAT 拼帧即可；
- ICLH 出现 0 次、AA 芯片头大量出现 → 板子裸串口只发芯片帧，需要改 SerialSource；
"""

import sys
import time


def main() -> None:
    if len(sys.argv) < 3:
        print("用法: python tools/serial_probe.py <COM口> <波特率>")
        print('例: python tools/serial_probe.py COM5 921600')
        return
    port, baud = sys.argv[1], int(sys.argv[2])

    import serial  # pyserial
    print(f"打开 {port} @ {baud} ...")
    ser = serial.Serial(port, baud, bytesize=8, parity="N", stopbits=1, timeout=0.5)
    print("已打开，监听 5 秒...")

    chunks = []
    total = 0
    t0 = time.time()
    while time.time() - t0 < 5.0:
        data = ser.read(4096)
        if data:
            chunks.append(data)
            total += len(data)
    ser.close()

    raw = b"".join(chunks)
    with open("serial_capture.bin", "wb") as f:
        f.write(raw)

    iclh = raw.count(b"ICLH")
    iclt = raw.count(b"ICLT")
    # 芯片头：0xAA 开头，第 4 字节固定 0x09（DAT 里观察到的）
    aa_head = sum(1 for i in range(len(raw) - 4) if raw[i] == 0xAA and raw[i + 3] == 0x09)

    print(f"--- 5 秒共收到 {total} 字节，已存到 serial_capture.bin ---")
    print(f"ICLH(49 43 4c 48) 出现 {iclh} 次")
    print(f"ICLT(49 43 4c 54) 出现 {iclt} 次")
    print(f"AA..09 芯片头 出现 {aa_head} 次")
    print()
    if iclh > 0:
        pos = raw.find(b"ICLH")
        print(f"结论: 带 MCU 包装层，第一个 ICLH 在偏移 {pos}，附近: "
              f"{raw[pos:pos+16].hex(' ')}")
    elif aa_head > 10:
        pos = raw.find(b"\xaa")
        print(f"结论: 没找到 ICLH，但有 {aa_head} 个芯片头——实时流是裸芯片帧，"
              f"需要改 SerialSource 拼帧逻辑。第一个芯片头附近: {raw[pos:pos+16].hex(' ')}")
    else:
        print("结论: 既没 ICLH 也没芯片头，波特率大概率不对。")


if __name__ == "__main__":
    main()

#!/bin/bash
# WaveSense 一键启动（macOS）：双击即起网页，自动开浏览器。
# 接了雷达就实时读串口；没接雷达自动跑内置演示。
cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
else
  echo "未找到 Python 3，请先安装 Python 3（官网 python.org 下载安装包）。"
  echo "安装后再双击本文件。"
  read -n 1 -s -r -p "按任意键关闭窗口..."
  exit 1
fi

export PYTHONPATH=.
echo "正在启动 WaveSense 网页..."
exec "$PY" -m src.main --web

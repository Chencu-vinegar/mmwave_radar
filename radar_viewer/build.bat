@echo off
REM 打包 毫米波雷达数据解析器 为单个 exe
cd /d "%~dp0"

pyinstaller --noconfirm --clean --onefile --windowed ^
  --name radar_viewer ^
  --hidden-import matplotlib.backends.backend_tkagg ^
  --hidden-import serial ^
  --hidden-import serial.tools.list_ports ^
  --hidden-import serial.serialwin32 ^
  --hidden-import mpl_toolkits.mplot3d ^
  --collect-data matplotlib ^
  radar_app.py

echo.
echo 打包完成，输出在 dist\radar_viewer.exe
pause

@echo off
rem 启动菜单（先 install.bat）。参数原样传给 menu.py。
cd /d "%~dp0game_vision"
if not exist "..\.venv\Scripts\python.exe" (
  echo 还没安装：先运行 install.bat
  pause
  exit /b 1
)
"..\.venv\Scripts\python.exe" menu.py %*
if errorlevel 1 pause

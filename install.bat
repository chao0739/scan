@echo off
rem 一键安装（Windows）：在本目录建 .venv 虚拟环境并装依赖。
rem   install.bat            只装运行依赖
rem   install.bat --tools    额外装精灵图导出工具的依赖（UnityPy/lz4）
rem 需要先装 Python 3.10+（python.org，勾选 "Add python.exe to PATH"）。装完用 run.bat 启动。
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=python"
if not defined PY (
  echo [install] 找不到 Python 3.10+。请到 https://www.python.org/downloads/ 安装，并勾选 "Add python.exe to PATH"
  exit /b 1
)
echo [install] 使用 %PY%

if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv || (echo [install] 创建虚拟环境失败 & exit /b 1)
)
set "VPY=.venv\Scripts\python.exe"
"%VPY%" -m pip install -q -U pip wheel || (echo [install] 升级 pip 失败（网络？） & exit /b 1)
echo [install] 安装运行依赖（opencv / numpy / pyyaml / cyndilib，约 100 MB）……
"%VPY%" -m pip install -r game_vision\requirements.txt || (echo [install] pip 安装依赖失败 & exit /b 1)
if "%~1"=="--tools" (
  echo [install] 安装精灵图导出工具依赖……
  "%VPY%" -m pip install -r game_vision\requirements-tools.txt || (echo [install] pip 安装工具依赖失败 & exit /b 1)
)

"%VPY%" -c "import cv2, numpy, yaml, cyndilib; print('[install] OK  opencv', cv2.__version__, ' numpy', numpy.__version__)" || (echo [install] 自检失败 & exit /b 1)

if not exist "game_vision\settings.yaml" echo [install] 提示：没有 game_vision\settings.yaml（本机设置）。首次启动菜单后配：画面源、玩家名牌、Pico IP。
if not exist "game_vision\calibration\homography.json" echo [install] 提示：没有标定文件，NDI 画面首次运行会自动标定。
echo [install] 完成。启动：run.bat
endlocal

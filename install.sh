#!/usr/bin/env bash
# 一键安装（Linux / macOS）：在本目录建 .venv 虚拟环境并装依赖。
#   ./install.sh            只装运行依赖
#   ./install.sh --tools    额外装精灵图导出工具的依赖（UnityPy/lz4，只有要从客户端 aa/ 导图才需要）
# 环境变量 PYTHON=/path/to/python3 可指定解释器。装完用 ./run.sh 启动菜单。
set -u
cd "$(dirname "$0")"

die() { echo "[install] 失败：$*" >&2; exit 1; }

# 挑解释器：3.10+ 且能建虚拟环境（带 ensurepip；Ubuntu 系统 python 没装 python3-venv 时不带）
ok_py() { "$1" -c 'import sys, ensurepip; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; }
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for c in python3 python python3.13 python3.12 python3.11 python3.10; do
    command -v "$c" >/dev/null 2>&1 && ok_py "$c" && { PY="$c"; break; }
  done
fi
if [ -z "$PY" ]; then
  echo "[install] 找不到「3.10+ 且能建虚拟环境」的 Python。" >&2
  if command -v apt-get >/dev/null 2>&1; then
    echo "[install] Ubuntu/Debian 需要：sudo apt install python3 python3-venv" >&2
    if [ -t 0 ]; then
      read -r -p "[install] 现在执行 sudo apt install -y python3 python3-venv ？[y/N] " ans
      if [ "$ans" = "y" ] || [ "$ans" = "Y" ]; then
        sudo apt install -y python3 python3-venv && ok_py python3 && PY=python3
      fi
    fi
  else
    echo "[install] macOS: brew install python；其他系统请安装 Python 3.10+ 并确保 python3 -m venv 可用" >&2
  fi
  [ -n "$PY" ] || exit 1
fi
echo "[install] 使用 $("$PY" -c 'import sys; print(sys.executable, sys.version.split()[0])')"

VPY=.venv/bin/python
# 已有但残缺的 .venv（上次建到一半、没 pip）删掉重建
if [ -e .venv ] && ! "$VPY" -m pip --version >/dev/null 2>&1; then
  echo "[install] 现有 .venv 不完整，重建"
  rm -rf .venv
fi
if [ ! -x "$VPY" ]; then
  "$PY" -m venv .venv || { rm -rf .venv; die "创建虚拟环境失败。Ubuntu 需要：sudo apt install python3-venv"; }
fi
"$VPY" -m pip install -q -U pip wheel || die "升级 pip 失败（网络？）"
echo "[install] 安装运行依赖（opencv / numpy / pyyaml / cyndilib，约 100 MB）……"
"$VPY" -m pip install -r game_vision/requirements.txt || die "pip 安装依赖失败"
if [ "${1:-}" = "--tools" ]; then
  echo "[install] 安装精灵图导出工具依赖……"
  "$VPY" -m pip install -r game_vision/requirements-tools.txt || die "pip 安装工具依赖失败"
fi

# 自检：能 import、OpenCV 带 GUI
"$VPY" - <<'PYEOF' || die "自检失败（见上面的报错）"
import cv2, numpy, yaml
import cyndilib
info = cv2.getBuildInformation()
gui = any(k in info for k in ("GTK", "QT", "Cocoa", "WIN32UI"))
from importlib.metadata import version
print(f"[install] OK  opencv {cv2.__version__} (GUI {'有' if gui else '无 —— 装的是 headless 版，菜单开不了窗口'})  numpy {numpy.__version__}  cyndilib {version('cyndilib')}")
PYEOF

# Linux 系统层面的提示（不自动 sudo）
if [ "$(uname -s)" = "Linux" ]; then
  if ! ldconfig -p 2>/dev/null | grep -q 'libGL.so.1'; then
    echo "[install] 提示：缺 libGL（OpenCV 窗口需要）：sudo apt install libgl1 libglib2.0-0"
  fi
  if command -v systemctl >/dev/null 2>&1 && ! systemctl is-active --quiet avahi-daemon 2>/dev/null; then
    echo "[install] 提示：NDI 源发现靠 mDNS，avahi-daemon 没在运行：sudo apt install avahi-daemon && sudo systemctl enable --now avahi-daemon"
  fi
fi

[ -f game_vision/settings.yaml ] || echo "[install] 提示：没有 game_vision/settings.yaml（本机设置）。首次启动菜单后到「玩家设置 / 怪物模板 / 设置」里配：画面源、玩家名牌、Pico IP。"
[ -f game_vision/calibration/homography.json ] || echo "[install] 提示：没有标定文件，NDI 画面首次运行会自动标定（菜单「标定屏幕四角」→ 自动）。"
echo "[install] 完成。启动：./run.sh"

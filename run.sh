#!/usr/bin/env bash
# 启动菜单（先 ./install.sh）。参数原样传给 menu.py。
cd "$(dirname "$0")/game_vision" || exit 1
[ -x ../.venv/bin/python ] || { echo "还没安装：先运行 ./install.sh" >&2; exit 1; }
exec ../.venv/bin/python menu.py "$@"

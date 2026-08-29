"""把工程打成一个可拷到另一台电脑的 zip（对方解压后运行 install.sh / install.bat 即可）。

    python tools/pack.py                 -> dist/game_vision-YYYYMMDD.zip（代码 + 配置 + 模板 + 标定 + 本机 settings.yaml）
    python tools/pack.py --with-wz       额外带上全怪物精灵库 templates/_wz（约 180 MB；不带的话对方要有客户端 aa/ 才能重建）
    python tools/pack.py --no-settings   不带本机 settings.yaml（给别人用、不想带自己的玩家/怪物/Pico 设置时）
    python tools/pack.py --out /path/x.zip

不打包：aa/（游戏客户端资源）、logs/、recordings/、harvest/、*.mp4、__pycache__/、.venv/、.ndi/、dist/、archive/、templates/*/_dup/。
"""
import argparse
import datetime
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))        # 仓库根（game_vision 的上一级）

TOP_LEVEL = ["game_vision", "pico", "docs", "install.sh", "install.bat", "run.sh", "run.bat", ".gitignore"]
EXCLUDE_DIRS = {"__pycache__", ".venv", ".ndi", "logs", "recordings", "harvest", "_dup", "dist", ".git", ".wz_cache"}
EXCLUDE_EXT = {".mp4", ".avi", ".pyc", ".log", ".jsonl"}


def iter_files(top, with_wz, with_settings):
    for dirpath, dirnames, filenames in os.walk(top):
        rel_dir = os.path.relpath(dirpath, ROOT)
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS and not (d == "_wz" and not with_wz))
        for f in sorted(filenames):
            if os.path.splitext(f)[1].lower() in EXCLUDE_EXT:
                continue
            if f == "settings.yaml" and rel_dir == "game_vision" and not with_settings:
                continue
            yield os.path.join(dirpath, f)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--with-wz", action="store_true", help="带上 templates/_wz 精灵库（约 180 MB）")
    ap.add_argument("--no-settings", action="store_true", help="不带本机 settings.yaml")
    ap.add_argument("--out", default=None, help="输出 zip 路径（默认 dist/game_vision-YYYYMMDD.zip）")
    a = ap.parse_args(argv)

    name = f"game_vision-{datetime.date.today():%Y%m%d}"
    out = a.out or os.path.join(ROOT, "dist", name + ".zip")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    # 根目录的说明文档也带上（*.md）
    tops = list(TOP_LEVEL) + sorted(f for f in os.listdir(ROOT) if f.lower().endswith(".md"))
    n = 0
    total = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for t in tops:
            p = os.path.join(ROOT, t)
            if not os.path.exists(p):
                print(f"[pack] 跳过不存在的 {t}")
                continue
            files = iter_files(p, a.with_wz, not a.no_settings) if os.path.isdir(p) else [p]
            for f in files:
                arc = os.path.join(name, os.path.relpath(f, ROOT))
                z.write(f, arc)
                n += 1
                total += os.path.getsize(f)
    print(f"[pack] {n} 个文件，原始 {total / 1e6:.0f} MB -> {out} ({os.path.getsize(out) / 1e6:.0f} MB)")
    print(f"[pack] 精灵库 templates/_wz {'已带上' if a.with_wz else '未带（--with-wz 可带上）'}；本机 settings.yaml {'未带' if a.no_settings else '已带上'}")
    print(f"[pack] 对方：解压 -> 进入 {name}/ -> ./install.sh（Windows: install.bat）-> ./run.sh（run.bat）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

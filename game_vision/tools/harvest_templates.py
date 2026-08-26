"""半自动采集怪物模板。

第 1 步 scan：用少量种子模板低阈值扫整段视频，把候选自动抠出并拼成编号缩略图。
    实机（摄像头 0 现场录 120 秒再扫）：
    python tools/harvest_templates.py scan --source 0 --seconds 120 --seeds templates/stump_map01 --name stump_map01
    或对已有录像（app.py 里按 r 录的 recordings/*.mp4）：
    python tools/harvest_templates.py scan --source recordings/rec_xxx.mp4 --seeds templates/stump_map01 --name stump_map01
    -> 生成 harvest/<name>/sheet.jpg（编号缩略图）和 candidates.json

第 2 步 pick：看图报编号，按匹配位置统一尺寸保存为模板。
    python tools/harvest_templates.py pick --name stump_map01 --ids 0,3,5-9 --out templates/stump_map01

可选参数：--step 抽帧间隔(默认 20)  --thr 候选阈值(默认 0.55)  --per-frame 每帧最多候选数(默认 4)
         --band y1,y2 只在该纵向范围搜索(矫正后坐标，默认避开顶部/底部 HUD)
"""
import argparse
import glob
import json
import os
import sys

import cv2
import numpy as np
import yaml

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from calibration import Rectifier, load_corners  # noqa: E402
from camera import open_writer, open_camera, resolve_source  # noqa: E402

HARVEST_DIR = "harvest"
MARGIN = 8  # 缩略图上下文边距（保存模板时不含）


def load_seeds(seed_dir):
    seeds = []
    for p in sorted(glob.glob(os.path.join(seed_dir, "*.png"))):
        img = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        seeds.append((os.path.basename(p), img))
        seeds.append((os.path.basename(p) + "~flip", cv2.flip(img, 1)))
    if not seeds:
        raise SystemExit(f"种子目录没有 png: {seed_dir}")
    return seeds


def scan(args):
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    sc = cfg["screen"]
    if getattr(args, "calib", None):
        sc["calibration_file"] = args.calib
    out_w, out_h = sc["output_width"], sc["output_height"]
    rect = Rectifier(load_corners(sc["calibration_file"]), out_w, out_h)
    seeds = load_seeds(args.seeds)
    y1, y2 = (map(int, args.band.split(","))) if args.band else (int(out_h * 0.1), int(out_h * 0.85))

    src = resolve_source(args.source)
    live = isinstance(src, int)
    if live:
        # 直接用摄像头：先录制 --seconds 秒到 recordings/，再按文件流程处理（便于之后 pick 复用）
        cap = open_camera(src, cfg["camera"]["width"], cfg["camera"]["height"])
        os.makedirs("recordings", exist_ok=True)
        rec_base = os.path.abspath(os.path.join("recordings", f"harvest_{args.name}"))
        rec_path = rec_base + ".mp4"
        fps = cap.get(cv2.CAP_PROP_FPS)
        writer = None
        import time
        t_end = time.time() + args.seconds
        n = 0
        print(f"[rec] 摄像头录制 {args.seconds}s -> {rec_path}  (按 q 提前结束)")
        while time.time() < t_end:
            ok, f = cap.read()
            if not ok:
                break
            if writer is None:
                writer, rec_path = open_writer(rec_base, fps, (f.shape[1], f.shape[0]))
                print(f"[rec] 实际分辨率 {f.shape[1]}x{f.shape[0]} -> {rec_path}")
            writer.write(f)
            n += 1
            cv2.imshow("recording (q to stop)", cv2.resize(f, (960, 540)))
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
        cap.release()
        if writer is not None:
            writer.release()
        cv2.destroyAllWindows()
        print(f"[rec] 录制完成 {n} 帧")
        args.source = rec_path

    cap = cv2.VideoCapture(args.source)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cands, thumbs = [], []
    for fi in range(0, total, args.step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
        ok, f = cap.read()
        if not ok:
            break
        g = rect(f)
        gray = cv2.cvtColor(g, cv2.COLOR_BGR2GRAY)
        # 每个像素位置记录最高分及对应种子尺寸
        best = np.full((out_h, out_w), -1.0, np.float32)
        best_wh = np.zeros((out_h, out_w, 2), np.int32)
        for name, t in seeds:
            th, tw = t.shape
            res = cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED)
            h, w = res.shape
            m = res > best[:h, :w]
            best[:h, :w][m] = res[m]
            best_wh[:h, :w][m] = (tw, th)
        best[:y1, :] = -1
        best[y2:, :] = -1
        for _ in range(args.per_frame):
            _, mx, _, (x, y) = cv2.minMaxLoc(best)
            if mx < args.thr:
                break
            tw, th = best_wh[y, x]
            cands.append({"frame": fi, "x": int(x), "y": int(y), "w": int(tw), "h": int(th), "score": round(float(mx), 3)})
            crop = g[max(0, y - MARGIN):y + th + MARGIN, max(0, x - MARGIN):x + tw + MARGIN]
            thumbs.append(crop)
            cv2.rectangle(best, (x - tw // 2, y - th // 2), (x + tw // 2, y + th // 2), -1, -1)  # 抑制邻近重复
        print(f"\r扫描 {fi}/{total}  候选 {len(cands)}", end="")
    print()
    cap.release()
    if not cands:
        raise SystemExit("没有找到任何候选：检查标定是否正确、种子模板是否来自同一机位画面，或调低 --thr")

    hdir = os.path.join(HARVEST_DIR, args.name)
    os.makedirs(hdir, exist_ok=True)
    json.dump({"source": os.path.abspath(args.source), "calib": args.calib, "candidates": cands}, open(os.path.join(hdir, "candidates.json"), "w"), indent=1)
    # 拼缩略图
    tiles = []
    for i, im in enumerate(thumbs):
        t = cv2.resize(im, (args.tile, int(args.tile * im.shape[0] / im.shape[1])))
        t = cv2.copyMakeBorder(t, 0, max(0, args.tile + 20 - t.shape[0]), 0, 0, cv2.BORDER_CONSTANT)
        t = t[:args.tile + 20]
        cv2.putText(t, str(i), (2, 14), 0, 0.5, (0, 255, 255), 2)
        cv2.putText(t, f"{cands[i]['score']:.2f}", (2, t.shape[0] - 4), 0, 0.45, (0, 255, 0), 1)
        tiles.append(t)
    cols = args.cols
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[k:k + cols]) for k in range(0, len(tiles), cols)]
    sheet = np.vstack(rows)
    # 过高时分页保存
    page_h = 4000
    pages = [sheet[i:i + page_h] for i in range(0, sheet.shape[0], page_h)]
    for k, pg in enumerate(pages):
        cv2.imwrite(os.path.join(hdir, f"sheet{'' if len(pages) == 1 else k + 1}.jpg"), pg, [cv2.IMWRITE_JPEG_QUALITY, 85])
    print(f"共 {len(cands)} 个候选，缩略图: {hdir}/sheet*.jpg  （看图后用 pick --ids 选择）")


def parse_ids(s):
    ids = set()
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            ids.update(range(int(a), int(b) + 1))
        else:
            ids.add(int(part))
    return sorted(ids)


def pick(args):
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    sc = cfg["screen"]
    if getattr(args, "calib", None):
        sc["calibration_file"] = args.calib
    rect = Rectifier(load_corners(sc["calibration_file"]), sc["output_width"], sc["output_height"])
    hdir = os.path.join(HARVEST_DIR, args.name)
    data = json.load(open(os.path.join(hdir, "candidates.json")))
    cands = data["candidates"]
    if not args.calib and data.get("calib"):
        sc["calibration_file"] = data["calib"]
        rect = Rectifier(load_corners(sc["calibration_file"]), sc["output_width"], sc["output_height"])
    ids = parse_ids(args.ids)
    cap = cv2.VideoCapture(args.source or data["source"])
    os.makedirs(args.out, exist_ok=True)
    cache = {}
    n = 0
    for i in ids:
        if i < 0 or i >= len(cands):
            print(f"跳过无效编号 {i}")
            continue
        c = cands[i]
        if c["frame"] not in cache:
            cap.set(cv2.CAP_PROP_POS_FRAMES, c["frame"])
            ok, f = cap.read()
            if not ok:
                continue
            cache[c["frame"]] = rect(f)
        g = cache[c["frame"]]
        crop = g[c["y"]:c["y"] + c["h"], c["x"]:c["x"] + c["w"]]
        p = os.path.join(args.out, f"{args.prefix}_f{c['frame']}_{i:03d}.png")
        cv2.imwrite(p, crop)
        n += 1
    print(f"已保存 {n} 张模板到 {args.out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--source", required=True, help="视频文件，或摄像头编号（配合 --seconds 现场录制）")
    s.add_argument("--seconds", type=int, default=120, help="source 为摄像头时的录制时长")
    s.add_argument("--seeds", required=True, help="种子模板目录")
    s.add_argument("--name", required=True, help="采集任务名（harvest/<name>/）")
    s.add_argument("--step", type=int, default=20)
    s.add_argument("--thr", type=float, default=0.55)
    s.add_argument("--per-frame", type=int, default=4)
    s.add_argument("--band", default=None, help="y1,y2")
    s.add_argument("--calib", default=None, help="标定文件（离线视频用对应的备份）")
    s.add_argument("--tile", type=int, default=120)
    s.add_argument("--cols", type=int, default=10)
    p = sub.add_parser("pick")
    p.add_argument("--name", required=True)
    p.add_argument("--ids", required=True, help="如 0,3,5-9")
    p.add_argument("--out", required=True)
    p.add_argument("--prefix", default="auto")
    p.add_argument("--source", default=None, help="默认用 scan 时的视频")
    p.add_argument("--calib", default=None)
    args = ap.parse_args()
    for k in ("source", "seeds", "out", "calib"):
        v = getattr(args, k, None)
        if v and not (k == "source" and not os.path.exists(str(v))):  # source 为摄像头编号/名称时不转路径
            setattr(args, k, os.path.abspath(v))
    os.chdir(ROOT)
    scan(args) if args.cmd == "scan" else pick(args)


if __name__ == "__main__":
    main()

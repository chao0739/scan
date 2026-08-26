"""把一次运行日志叠加到视频帧上，生成缩略图表便于人工核对。
用法：python tools/review_log.py --source ../shot.mp4 --log logs/run_xxx.jsonl --step 27 --out review.jpg
"""
import argparse
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--step", type=int, default=27)
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--out", default="review.jpg")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--calib", default=None, help="标定文件")
    ap.add_argument("--end", type=int, default=10**9)
    args = ap.parse_args()
    if args.calib:
        args.calib = os.path.abspath(args.calib)
    source = os.path.abspath(args.source)
    log = os.path.abspath(args.log)
    out = os.path.abspath(args.out)
    os.chdir(ROOT)
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    sc = cfg["screen"]
    if getattr(args, "calib", None):
        sc["calibration_file"] = args.calib
    rect = Rectifier(load_corners(sc["calibration_file"]), sc["output_width"], sc["output_height"])
    R = {r["frame"]: r for r in (json.loads(l) for l in open(log, encoding="utf-8"))}
    cap = cv2.VideoCapture(source)
    tiles = []
    for i in range(args.start, min(args.end, int(cap.get(cv2.CAP_PROP_FRAME_COUNT))), args.step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, f = cap.read()
        if not ok:
            break
        g = rect(f)
        r = R.get(i)
        if r:
            if r["player"]:
                cv2.drawMarker(g, tuple(map(int, r["player"])), (255, 0, 255), cv2.MARKER_CROSS, 30, 3)
            if r["roi"]:
                x1, y1, x2, y2 = r["roi"]
                cv2.rectangle(g, (x1, y1), (x2, y2), (0, 255, 0) if r["detected"] else (255, 255, 0), 3)
            cv2.putText(g, f'{i} {"DET" if r["detected"] else "-"} s={r["score"]:.2f} p={r["player_score"]:.2f}',
                        (10, 60), 0, 1.4, (0, 0, 255), 3)
        tiles.append(cv2.resize(g, (640, 360)))
    while len(tiles) % args.cols:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[k:k + args.cols]) for k in range(0, len(tiles), args.cols)]
    cv2.imwrite(out, np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 80])
    print("wrote", out, len(tiles), "tiles")


if __name__ == "__main__":
    main()

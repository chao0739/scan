"""从（矫正后的）画面里手工框选模板并保存。

用法：python tools/crop_templates.py --source ../shot.mp4 --out templates/map_01 [--raw]
键位：a/d 前后 1 帧，A/D 前后 30 帧，鼠标拖框后按 s 保存，q 退出。--raw 表示不做透视矫正。
"""
import argparse
import os
import sys

import cv2
import yaml

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from camera import FrameSource  # noqa: E402
from calibration import Rectifier, load_corners  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--prefix", default="t")
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--calib", default=None, help="标定文件")
    args = ap.parse_args()
    if args.calib:
        args.calib = os.path.abspath(args.calib)
    source = os.path.abspath(args.source) if not args.source.isdigit() else args.source
    os.chdir(ROOT)
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    sc = cfg["screen"]
    if getattr(args, "calib", None):
        sc["calibration_file"] = args.calib
    rect = None if args.raw else Rectifier(load_corners(sc["calibration_file"]), sc["output_width"], sc["output_height"])
    src = FrameSource(source, cfg["camera"]["width"], cfg["camera"]["height"])
    os.makedirs(args.out, exist_ok=True)
    idx = args.start
    n = len([f for f in os.listdir(args.out) if f.endswith(".png")])
    box = {"p0": None, "p1": None}

    def on_mouse(ev, x, y, flags, _):
        if ev == cv2.EVENT_LBUTTONDOWN:
            box["p0"], box["p1"] = (x, y), (x, y)
        elif ev == cv2.EVENT_MOUSEMOVE and flags & cv2.EVENT_FLAG_LBUTTON:
            box["p1"] = (x, y)
        elif ev == cv2.EVENT_LBUTTONUP:
            box["p1"] = (x, y)

    cv2.namedWindow("crop")
    cv2.setMouseCallback("crop", on_mouse)
    frame = None
    while True:
        if frame is None:
            src.seek(idx)
            ok, f = src.read()
            if not ok:
                break
            frame = rect(f) if rect else f
        vis = frame.copy()
        if box["p0"] and box["p1"]:
            cv2.rectangle(vis, box["p0"], box["p1"], (0, 255, 0), 1)
        cv2.putText(vis, f"frame {idx}  a/d +-1  A/D +-30  s save  q quit  saved={n}",
                    (8, 20), 0, 0.6, (0, 255, 255), 2)
        cv2.imshow("crop", vis)
        k = cv2.waitKey(30) & 0xFF
        if k == ord("q"):
            break
        elif k == ord("a"):
            idx = max(0, idx - 1)
            frame = None
        elif k == ord("d"):
            idx += 1
            frame = None
        elif k == ord("A"):
            idx = max(0, idx - 30)
            frame = None
        elif k == ord("D"):
            idx += 30
            frame = None
        elif k == ord("s") and box["p0"] and box["p1"]:
            (x0, y0), (x1, y1) = box["p0"], box["p1"]
            x0, x1 = sorted((x0, x1))
            y0, y1 = sorted((y0, y1))
            if x1 - x0 > 4 and y1 - y0 > 4:
                p = os.path.join(args.out, f"{args.prefix}_{idx}_{n:02d}.png")
                cv2.imwrite(p, frame[y0:y1, x0:x1])
                n += 1
                print("saved", p, (x1 - x0, y1 - y0))
    src.release()


if __name__ == "__main__":
    main()

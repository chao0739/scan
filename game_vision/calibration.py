"""屏幕四角标定与透视矫正。"""
import json
import os
import cv2
import numpy as np


def load_corners(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    return np.array(d["corners"], dtype=np.float32)


def save_corners(path, corners, src_size):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"corners": np.asarray(corners, dtype=float).tolist(),
                   "source_size": list(src_size)}, f, indent=2)


class Rectifier:
    """根据四角(左上、右上、右下、左下)计算单应矩阵，输出固定尺寸的游戏画面。"""

    def __init__(self, corners, out_w, out_h):
        self.out_w, self.out_h = out_w, out_h
        dst = np.array([[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]], dtype=np.float32)
        self.H = cv2.getPerspectiveTransform(np.asarray(corners, dtype=np.float32), dst)

    def __call__(self, frame):
        return cv2.warpPerspective(frame, self.H, (self.out_w, self.out_h), flags=cv2.INTER_LINEAR)


def order_corners(pts):
    """任意顺序的四点 -> 左上、右上、右下、左下。"""
    pts = np.asarray(pts, dtype=np.float32)
    s = pts.sum(1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], dtype=np.float32)


def run_calibration_ui(source, save_path, out_w, out_h, existing=None):
    """交互式标定：在画面上依次点击游戏区域四角。
    键位：r 重置  u 撤销  s 保存  n 下一帧  q 退出不保存
    """
    win = "calibration - click 4 corners (TL, TR, BR, BL)"
    pts = [] if existing is None else [tuple(map(float, p)) for p in existing]
    ok, frame = source.read()
    if not ok:
        raise RuntimeError("无法读取画面用于标定")
    state = {"frame": frame, "pts": pts}

    def on_mouse(ev, x, y, flags, _):
        if ev == cv2.EVENT_LBUTTONDOWN and len(state["pts"]) < 4:
            state["pts"].append((float(x), float(y)))

    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win, 1280, 720)
    cv2.setMouseCallback(win, on_mouse)
    while True:
        vis = state["frame"].copy()
        for i, p in enumerate(state["pts"]):
            cv2.circle(vis, (int(p[0]), int(p[1])), 6, (0, 0, 255), -1)
            cv2.putText(vis, str(i + 1), (int(p[0]) + 8, int(p[1]) - 8), 0, 0.8, (0, 255, 255), 2)
        if len(state["pts"]) == 4:
            cv2.polylines(vis, [np.array(state["pts"], np.int32)], True, (0, 255, 0), 2)
            prev = Rectifier(order_corners(state["pts"]), out_w, out_h)(state["frame"])
            cv2.imshow("rectified preview", cv2.resize(prev, (out_w // 2, out_h // 2)))
        cv2.putText(vis, "click TL,TR,BR,BL | r reset  u undo  s save  n next frame  q quit",
                    (10, 30), 0, 0.8, (255, 255, 255), 2)
        cv2.imshow(win, vis)
        k = cv2.waitKey(30) & 0xFF
        if k == ord("q"):
            cv2.destroyAllWindows()
            return None
        if k == ord("r"):
            state["pts"] = []
        if k == ord("u") and state["pts"]:
            state["pts"].pop()
        if k == ord("n"):
            ok, f = source.read()
            if ok:
                state["frame"] = f
        if k == ord("s") and len(state["pts"]) == 4:
            c = order_corners(state["pts"])
            save_corners(save_path, c, (state["frame"].shape[1], state["frame"].shape[0]))
            cv2.destroyAllWindows()
            return c

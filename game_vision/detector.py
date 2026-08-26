"""ROI 内模板匹配：输出 best_score / 位置 / 命中模板名。"""
import glob
import os
import cv2
import numpy as np


class TemplateDetector:
    def __init__(self, template_dir, threshold=0.7, scales=(1.0,), grayscale=True, flip=True):
        self.threshold = threshold
        self.scales = list(scales)
        self.grayscale = grayscale
        self.templates = []  # (name, image)
        paths = sorted(glob.glob(os.path.join(template_dir, "*.png")))
        if not paths:
            raise RuntimeError(f"模板目录为空: {template_dir}")
        for p in paths:
            img = cv2.imread(p, cv2.IMREAD_COLOR)
            if img is None:
                continue
            if grayscale:
                img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            for s in self.scales:
                t = img if s == 1.0 else cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
                self.templates.append((f"{os.path.basename(p)}@{s}", t))
                if flip:  # 水平翻转覆盖左右朝向
                    self.templates.append((f"{os.path.basename(p)}@{s}~flip", cv2.flip(t, 1)))

    def detect(self, roi):
        """返回 dict(score, loc=(x,y,w,h) 相对 ROI, template, raw)"""
        if self.grayscale and roi.ndim == 3:
            roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        best = {"score": -1.0, "loc": None, "template": None}
        for name, t in self.templates:
            th, tw = t.shape[:2]
            if roi.shape[0] < th or roi.shape[1] < tw:
                continue
            res = cv2.matchTemplate(roi, t, cv2.TM_CCOEFF_NORMED)
            _, mx, _, mloc = cv2.minMaxLoc(res)
            if mx > best["score"]:
                best = {"score": float(mx), "loc": (mloc[0], mloc[1], tw, th), "template": name}
        best["raw"] = best["score"] >= self.threshold
        return best

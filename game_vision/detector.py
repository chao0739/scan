"""ROI 内模板匹配：输出 best_score / 位置 / 命中模板名。

可选「颜色校验」(color_verify)：模板匹配只负责找候选框（阈值可以放低保证召回），
再把候选框的 H-S 颜色直方图与模板比较（Bhattacharyya 距离），距离太大的候选（蓝天/树干/地面等
灰度纹理相似但颜色不同的假框）直接否决。实测（woniu 地图，96 帧标注）：纯灰度匹配有怪/无怪分数重叠，
加颜色校验后误报 0、召回 2/18→11/18。
"""
import glob
import os
import cv2
import numpy as np


def edge_map(img):
    """Sobel 梯度幅值图：怪物 sprite 有粗黑轮廓，灌木/天空/地面没有，用它做第二道校验。"""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))


def hs_hist(bgr, h_bins=18, s_bins=8):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h = cv2.calcHist([hsv], [0, 1], None, [h_bins, s_bins], [0, 180, 0, 256])
    return cv2.normalize(h, h).flatten()


class TemplateDetector:
    def __init__(self, template_dir, threshold=0.7, scales=(1.0,), grayscale=True, flip=True, color_verify=None):
        """color_verify: None/False 关闭；或 dict(max_dist=0.48, edge_min=0.4, topk=3, h_bins=18, s_bins=8)。
        edge_min: 候选框与模板在 Sobel 边缘图上的匹配分下限（0 关闭）。"""
        self.threshold = threshold
        self.scales = list(scales)
        self.grayscale = grayscale
        cv = color_verify if isinstance(color_verify, dict) else {}
        self.color_verify = bool(color_verify) and cv.get("enabled", True)
        self.max_dist = float(cv.get("max_dist", 0.48))
        self.edge_min = float(cv.get("edge_min", 0.4))
        self.topk = int(cv.get("topk", 3))
        self.h_bins, self.s_bins = int(cv.get("h_bins", 18)), int(cv.get("s_bins", 8))
        self.templates = []  # (name, match_image, color_hist or None, edge_map or None)
        paths = sorted(glob.glob(os.path.join(template_dir, "*.png")))
        if not paths:
            raise RuntimeError(f"模板目录为空: {template_dir}")
        for p in paths:
            img = cv2.imread(p, cv2.IMREAD_COLOR)
            if img is None:
                continue
            for s in self.scales:
                c = img if s == 1.0 else cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
                variants = [(f"{os.path.basename(p)}@{s}", c)]
                if flip:  # 水平翻转覆盖左右朝向
                    variants.append((f"{os.path.basename(p)}@{s}~flip", cv2.flip(c, 1)))
                for name, cimg in variants:
                    m = cv2.cvtColor(cimg, cv2.COLOR_BGR2GRAY) if grayscale else cimg
                    hist = hs_hist(cimg, self.h_bins, self.s_bins) if self.color_verify else None
                    em = edge_map(cimg) if (self.color_verify and self.edge_min > 0) else None
                    self.templates.append((name, m, hist, em))

    def detect(self, roi):
        """返回 dict(score, loc=(x,y,w,h) 相对 ROI, template, raw, color_dist, edge_score, verified)。
        score = 最佳（通过颜色校验的）候选的模板分；未开颜色校验时 verified 恒为 True。"""
        roi_bgr = roi
        roi_m = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if (self.grayscale and roi.ndim == 3) else roi
        best = {"score": -1.0, "loc": None, "template": None, "color_dist": None, "edge_score": None, "verified": False}
        roi_edge = edge_map(roi_bgr) if (self.color_verify and self.edge_min > 0) else None
        fallback = dict(best)  # 没有任何候选通过校验时，记录分数最高的未通过候选（便于日志/调参）
        for name, t, hist, tedge in self.templates:
            th, tw = t.shape[:2]
            if roi_m.shape[0] < th or roi_m.shape[1] < tw:
                continue
            res = cv2.matchTemplate(roi_m, t, cv2.TM_CCOEFF_NORMED)
            if not self.color_verify:
                _, mx, _, (x, y) = cv2.minMaxLoc(res)
                if mx > best["score"]:
                    best = {"score": float(mx), "loc": (x, y, tw, th), "template": name, "color_dist": None,
                            "edge_score": None, "verified": True}
                continue
            for _ in range(self.topk):
                _, mx, _, (x, y) = cv2.minMaxLoc(res)
                if mx < self.threshold or mx <= best["score"]:
                    break  # 后面的峰只会更低
                d = float(cv2.compareHist(hist, hs_hist(roi_bgr[y:y + th, x:x + tw], self.h_bins, self.s_bins),
                                          cv2.HISTCMP_BHATTACHARYYA))
                es = None
                if tedge is not None:
                    es = float(cv2.matchTemplate(roi_edge[y:y + th, x:x + tw], tedge, cv2.TM_CCOEFF_NORMED)[0, 0])
                cand = {"score": float(mx), "loc": (x, y, tw, th), "template": name, "color_dist": round(d, 3),
                        "edge_score": None if es is None else round(es, 3)}
                if d <= self.max_dist and (es is None or es >= self.edge_min):
                    best = dict(cand, verified=True)
                    break
                if mx > fallback["score"]:
                    fallback = dict(cand, verified=False)
                res[max(0, y - th // 2):y + th // 2 + 1, max(0, x - tw // 2):x + tw // 2 + 1] = -1  # 压掉该峰再找下一个
        if best["loc"] is None and fallback["loc"] is not None:
            best = fallback
        best["raw"] = bool(best["verified"] and best["score"] >= self.threshold)
        return best

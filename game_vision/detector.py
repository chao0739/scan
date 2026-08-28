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
    def __init__(self, template_dir, threshold=0.7, scales=(1.0,), grayscale=True, flip=True, color_verify=None,
                 sure_score=0.0, motion_min=5.0, sprite_scale=1.0, sprite_threshold=0.6, sprite_sure=0.75, sprite_topk=4):
        """color_verify: None/False 关闭；或 dict(max_dist=0.48, edge_min=0.4, topk=3, h_bins=18, s_bins=8)。
        edge_min: 候选框与模板在 Sobel 边缘图上的匹配分下限（0 关闭）。
        sure_score/motion_min: 运动门槛（只在开了 color_verify 且 detect() 传了 diff 时生效）：模板分 ≥ sure_score 直接接受；
        threshold~sure_score 之间的候选还要求候选框内与上一帧的平均灰度差 ≥ motion_min（岩壁纹理不动，怪会动）。sure_score=0 关闭。"""
        self.threshold = threshold
        self.sure_score, self.motion_min = float(sure_score or 0), float(motion_min)
        # 精灵图模板（tools/wz_sprites.py 从客户端导出的带透明通道 png）：只比精灵像素（masked NCC），背景是什么都不影响分数。
        # OpenCV 的 masked matchTemplate 是朴素实现（全分辨率 42 个变体 205 ms/ROI），所以两阶段：0.5 倍粗找各变体的峰，
        # 取总分前 sprite_topk 个到全分辨率 ±6px 精修（与穷举分差 ≤0.02 的 94%，35 ms/ROI）。
        # 分数 ≥ sprite_sure 直接接受，sprite_threshold~sprite_sure 之间要求候选框在动（同 sure_score/motion_min 的逻辑）。
        self.sprite_scale, self.sprite_threshold, self.sprite_sure, self.sprite_topk = float(sprite_scale), float(sprite_threshold), float(sprite_sure), int(sprite_topk)
        self.sprites = []   # (name, gray, mask, gray_half, mask_half)
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
            img = cv2.imread(p, cv2.IMREAD_UNCHANGED)
            if img is None:
                continue
            if img.ndim == 3 and img.shape[2] == 4 and (img[..., 3] < 128).any():   # 有透明像素 = 精灵图模板
                sp = img if self.sprite_scale == 1.0 else cv2.resize(img, None, fx=self.sprite_scale, fy=self.sprite_scale, interpolation=cv2.INTER_AREA)
                for name, v in ((os.path.basename(p), sp),) + (((os.path.basename(p) + "~flip", cv2.flip(sp, 1)),) if flip else ()):
                    g = cv2.cvtColor(v[..., :3], cv2.COLOR_BGR2GRAY)
                    m = (v[..., 3] > 128).astype(np.uint8)
                    gh = cv2.resize(g, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
                    mh = (cv2.resize(v[..., 3], None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA) > 128).astype(np.uint8)
                    if m.sum() < 50 or mh.sum() < 12:
                        continue
                    self.sprites.append((name, g, m, gh, mh))
                continue
            if img.ndim == 3 and img.shape[2] == 4:
                img = np.ascontiguousarray(img[..., :3])
            elif img.ndim == 2:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
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

    def detect(self, roi, diff=None):
        """返回 dict(score, loc=(x,y,w,h) 相对 ROI, template, raw, color_dist, edge_score, motion, verified)。
        score = 最佳（通过校验的）候选的模板分；未开颜色校验时 verified 恒为 True。
        diff: 与 ROI 同尺寸的「本帧-上一帧」灰度差图（roi.ROIProvider.motion_diff 切出来的），None = 不做运动门槛。"""
        roi_bgr = roi
        roi_m = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if (self.grayscale and roi.ndim == 3) else roi
        best = {"score": -1.0, "loc": None, "template": None, "color_dist": None, "edge_score": None, "motion": None, "verified": False}
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
                            "edge_score": None, "motion": None, "verified": True}
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
                mv = None
                if diff is not None and self.sure_score and mx < self.sure_score:
                    mv = float(diff[y:y + th, x:x + tw].mean())   # 中分候选：框里必须在动
                cand = {"score": float(mx), "loc": (x, y, tw, th), "template": name, "color_dist": round(d, 3),
                        "edge_score": None if es is None else round(es, 3), "motion": None if mv is None else round(mv, 1)}
                if d <= self.max_dist and (es is None or es >= self.edge_min) and (mv is None or mv >= self.motion_min):
                    best = dict(cand, verified=True)
                    break
                if mx > fallback["score"]:
                    fallback = dict(cand, verified=False)
                res[max(0, y - th // 2):y + th // 2 + 1, max(0, x - tw // 2):x + tw // 2 + 1] = -1  # 压掉该峰再找下一个
        if self.sprites:
            sb, sf = self._detect_sprites(roi_m, diff)
            if sb is not None and (best["loc"] is None or not best["verified"] or sb["score"] > best["score"]):
                best = sb
            elif sf is not None and best["loc"] is None and (fallback["loc"] is None or sf["score"] > fallback["score"]):
                fallback = sf
        if best["loc"] is None and fallback["loc"] is not None:
            best = fallback
        thr = self.sprite_threshold if best.get("sprite") else self.threshold
        best["raw"] = bool(best["verified"] and best["score"] >= thr)
        return best

    @staticmethod
    def _mm(res):
        res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)   # mask 区域全同色时 NCC 是 NaN
        _, mx, _, (x, y) = cv2.minMaxLoc(res)
        return float(mx), x, y

    def _detect_sprites(self, roi_m, diff, margin=6):
        """精灵图模板的两阶段 masked 匹配。返回 (通过的最佳候选 或 None, 未通过的最高候选 或 None)。"""
        half = cv2.resize(roi_m, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        cands = []
        for i, (name, g, m, gh, mh) in enumerate(self.sprites):
            if gh.shape[0] > half.shape[0] or gh.shape[1] > half.shape[1]:
                continue
            mx, x, y = self._mm(cv2.matchTemplate(half, gh, cv2.TM_CCOEFF_NORMED, mask=mh))
            cands.append((mx, i, x, y))
        cands.sort(reverse=True)
        best = fallback = None
        for _, i, x, y in cands[:self.sprite_topk]:
            name, g, m, _, _ = self.sprites[i]
            th, tw = g.shape
            x1, y1 = max(0, 2 * x - margin), max(0, 2 * y - margin)
            x2, y2 = min(roi_m.shape[1], 2 * x + tw + margin), min(roi_m.shape[0], 2 * y + th + margin)
            if x2 - x1 < tw or y2 - y1 < th:
                continue
            sc, lx, ly = self._mm(cv2.matchTemplate(roi_m[y1:y2, x1:x2], g, cv2.TM_CCOEFF_NORMED, mask=m))
            if sc < self.sprite_threshold:
                continue
            bx, by = x1 + lx, y1 + ly
            mv = None
            if diff is not None and sc < self.sprite_sure:
                mv = float(diff[by:by + th, bx:bx + tw].mean())
            cand = {"score": sc, "loc": (bx, by, tw, th), "template": name, "color_dist": None, "edge_score": None,
                    "motion": None if mv is None else round(mv, 1), "sprite": True}
            if mv is None or mv >= self.motion_min:
                if best is None or sc > best["score"]:
                    best = dict(cand, verified=True)
            elif fallback is None or sc > fallback["score"]:
                fallback = dict(cand, verified=False)
        return best, fallback

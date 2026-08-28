"""小地图黄点定位：玩家在地图里的绝对位置（不受镜头跟随/顶住影响，也不会被宠物名牌挡住）。

冒险岛左上角「小地图」窗口：标题栏「小地图」+ 表头（地图图标+名字）+ 地图缩略图。缩略图里
黄点=自己、红点=其他玩家、蓝点=传送门。缩略图尺寸随地图变化，窗口也可能被拖动，所以每次先按
窗口边框颜色（偏蓝灰的浅色）找出缩略图区域，再只在区域内找黄点——标题栏的按钮、表头的太阳图标
都是黄色的，必须排除。

比例：2026-08-28 勇士部落东入口实测 1 个小地图像素 ≈ 10.3 个矫正后画面像素（1280×720），
不同地图不同；巡逻端点直接用小地图坐标存（settings patrol.<怪物>.left_mm/right_mm），
交给 Decision 时乘 px_scale，让 patrol_tolerance(30px) 仍等价于约 3 个小地图像素。
"""
import cv2
import numpy as np


class MinimapTracker:
    def __init__(self, cfg, frame_w, frame_h):
        cfg = cfg or {}
        self.region = [int(v) for v in cfg.get("region", [0, 0, 400, 260])]   # 小地图窗口大致在哪（矫正后画面坐标）
        self.region[2], self.region[3] = min(self.region[2], frame_w), min(self.region[3], frame_h)
        self.px_scale = float(cfg.get("px_scale", 10))
        self.lo = np.array(cfg.get("yellow_lo", [20, 120, 180]), dtype=np.uint8)
        self.hi = np.array(cfg.get("yellow_hi", [38, 255, 255]), dtype=np.uint8)
        self.min_area, self.max_area = int(cfg.get("min_area", 10)), int(cfg.get("max_area", 90))
        self.area_every = int(cfg.get("area_every", 60))
        self.area = None          # 缩略图区域 (x0, y0, x1, y1)，画面坐标
        self.last = None          # 上一次黄点 (x, y)，画面坐标
        self.rel = None           # 黄点相对缩略图左上角的 (x, y)：端点用这个存，小地图窗口被拖动也不失效
        self.lost = 0
        self._n = 0

    # ---- 缩略图区域 ----
    @staticmethod
    def _longest_enclosed_run(is_border, min_len):
        """is_border: 每行(列)是否边框色。返回前后都紧邻边框的最长非边框段 (a, b)（缩略图夹在表头/底边框、左/右边框之间），
        没有就 None。标题栏（白色，不算边框色）也是一段被夹住的非边框段，但只有十几行，比缩略图短。"""
        n = len(is_border)
        best = None
        i = 0
        while i < n:
            if is_border[i]:
                i += 1
                continue
            j = i
            while j < n and not is_border[j]:
                j += 1
            if i > 0 and j < n and j - i >= min_len and (best is None or j - i > best[1] - best[0]):
                best = (i, j)
            i = j
        return best

    def find_area(self, game):
        """按窗口边框颜色定位缩略图：表头(地图名那块)下面、底边框上面；左边框右边、右边框左边。
        找不到就沿用上一次的区域（窗口没动的话仍然正确）。"""
        x1, y1, x2, y2 = self.region
        sub = game[y1:y2, x1:x2].astype(np.int16)
        b, g, r = sub[..., 0], sub[..., 1], sub[..., 2]
        frame = (b > 150) & (b - r > 8) & (b >= g)          # 窗口边框/表头：偏蓝灰的浅色（地图本身是棕色 R>B）
        seg = self._longest_enclosed_run(frame.mean(axis=1) > 0.3, 20)
        if seg is None:
            return self.area
        ya, yb = seg
        seg = self._longest_enclosed_run(frame[ya:yb].mean(axis=0) > 0.5, 40)
        if seg is None:
            return self.area
        xa, xb = seg
        self.area = (x1 + xa, y1 + ya, x1 + xb, y1 + yb)
        return self.area

    # ---- 黄点 ----
    def locate(self, game):
        """返回黄点相对缩略图左上角的 (x, y) 或 None（画面坐标在 self.last）。"""
        self._n += 1
        if self.area is None or self._n % self.area_every == 0 or self.lost > 5:
            self.find_area(game)
        if self.area is None:
            self.lost += 1
            return None
        x0, y0, x1, y1 = self.area
        hsv = cv2.cvtColor(game[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
        m = cv2.inRange(hsv, self.lo, self.hi)
        n, _lab, st, cen = cv2.connectedComponentsWithStats(m, 8)
        cands = [(int(cen[i][0]) + x0, int(cen[i][1]) + y0, int(st[i][4])) for i in range(1, n)
                 if self.min_area <= st[i][4] <= self.max_area]
        if not cands:
            self.lost += 1
            return None
        if self.last is not None and len(cands) > 1:
            cx, cy = self.last
            cands.sort(key=lambda c: (c[0] - cx) ** 2 + (c[1] - cy) ** 2)
        else:
            cands.sort(key=lambda c: -c[2])
        self.last = (cands[0][0], cands[0][1])
        self.rel = (self.last[0] - x0, self.last[1] - y0)
        self.lost = 0
        return self.rel

    def to_map_x(self, x):
        """缩略图内 x -> 交给巡逻逻辑的 x（乘比例，让 patrol_tolerance 的像素含义不变）。"""
        return None if x is None else x * self.px_scale

    def draw(self, vis):
        if self.area is not None:
            x0, y0, x1, y1 = self.area
            cv2.rectangle(vis, (x0, y0), (x1, y1), (0, 255, 255), 1)
        if self.last is not None:
            cv2.circle(vis, self.last, 5, (0, 255, 255), 1)

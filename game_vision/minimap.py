"""小地图黄点定位：玩家在地图里的绝对位置（不受镜头跟随/顶住影响，也不会被宠物名牌挡住）。

冒险岛左上角「小地图」窗口：标题栏「小地图」+ 表头（地图图标+名字）+ 地图缩略图。缩略图里
黄点=自己、红点=其他玩家、蓝点=传送门。缩略图尺寸随地图变化，窗口也可能被拖动，所以每次先找出
缩略图区域，再只在区域内找黄点——标题栏的按钮、表头的太阳图标都是黄色的，必须排除。

缩略图区域怎么找（find_area）：窗口框是固定图样，用三块 UI 模板（templates/_ui/，1280×720 矫正画面尺度）定位：
  minimap_title.png       标题栏左端「小地图」 -> 缩略图左边、上边（锚点 + 固定偏移）
  minimap_title_right.png 标题栏右端「大地图」按钮 -> 缩略图右边（窗口宽随地图变）
  minimap_bottom.png      窗口底部 9 行条纹（沿 x 均匀，任意宽度都能匹配）-> 缩略图下边（窗口高随地图变）
三块都只在标题栏那一行/左边一条窄带里搜，不看缩略图内容和游戏背景，实时/录像匹配分都 ≥0.99。
之前按「偏蓝灰浅色 = 边框」的统计找法在岩壁/天空地图会把窗口右边的游戏背景当成缩略图（2026-08-29 野猪的领土
26% 的帧丢黄点），录像压缩后底边框 b-r 只剩 7 也会失效；沿边框线追踪也被抗锯齿渐变卡住。都弃用，颜色法只留作没有模板时的兜底。

比例：2026-08-28 勇士部落东入口实测 1 个小地图像素 ≈ 10.3 个矫正后画面像素（1280×720），
不同地图不同；巡逻端点直接用小地图坐标存（settings patrol.<怪物>.left_mm/right_mm），
交给 Decision 时乘 px_scale，让 patrol_tolerance(30px) 仍等价于约 3 个小地图像素。
"""
import os

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
        self.border_solid = float(cfg.get("border_solid", 0.9))   # 兜底颜色找法：缩略图上/下边框线的实线比例下限
        # 三块 UI 模板（见文件头）。缩略图 左=标题锚点x+anchor_dx，上=锚点y+anchor_dy，右=右锚点x+right_dx，下=底条纹y
        ui = cfg.get("ui_dir", os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "_ui"))
        self.t_title = self._load_gray(os.path.join(ui, "minimap_title.png"))
        self.t_right = self._load_gray(os.path.join(ui, "minimap_title_right.png"))
        self.t_bottom = self._load_gray(os.path.join(ui, "minimap_bottom.png"))
        self.anchor_min = float(cfg.get("anchor_min", 0.8))
        self.anchor_dx, self.anchor_dy = int(cfg.get("anchor_dx", 4)), int(cfg.get("anchor_dy", 61))
        self.right_dx = int(cfg.get("right_dx", 37))
        self.anchor_score = (0.0, 0.0, 0.0)      # 最近一次三块模板的匹配分（调试用）
        self.area = None          # 缩略图区域 (x0, y0, x1, y1)，画面坐标
        self.last = None          # 上一次黄点 (x, y)，画面坐标
        self.rel = None           # 黄点相对缩略图左上角的 (x, y)：端点用这个存，小地图窗口被拖动也不失效
        self.lost = 0
        self._n = 0

    # ---- 缩略图区域 ----
    @staticmethod
    def _enclosed_runs(is_border, min_len):
        """is_border: 每行(列)是否边框色。返回所有前后都紧邻边框、长度 ≥min_len 的非边框段 [(a, b), ...]。"""
        n = len(is_border)
        runs = []
        i = 0
        while i < n:
            if is_border[i]:
                i += 1
                continue
            j = i
            while j < n and not is_border[j]:
                j += 1
            if i > 0 and j < n and j - i >= min_len:
                runs.append((i, j))
            i = j
        return runs

    @classmethod
    def _longest_enclosed_run(cls, is_border, min_len):
        """最长的被边框夹住的非边框段 (a, b)（缩略图夹在表头/底边框之间），没有就 None。
        标题栏（白色，不算边框色）也是一段被夹住的非边框段，但只有十几行，比缩略图短。"""
        runs = cls._enclosed_runs(is_border, min_len)
        return max(runs, key=lambda r: r[1] - r[0]) if runs else None

    @staticmethod
    def _load_gray(path):
        return cv2.imread(path, cv2.IMREAD_GRAYSCALE) if os.path.exists(path) else None

    @staticmethod
    def _match(gray, tpl, x0, y0, x1, y1):
        """在 gray[y0:y1, x0:x1] 里找 tpl，返回 (分, 左上角 x, 左上角 y)（画面坐标）；区域太小返回 (0, None, None)。"""
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(gray.shape[1], x1), min(gray.shape[0], y1)
        if y1 - y0 < tpl.shape[0] or x1 - x0 < tpl.shape[1]:
            return 0.0, None, None
        res = cv2.matchTemplate(gray[y0:y1, x0:x1], tpl, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        return float(mx), x0 + loc[0], y0 + loc[1]

    def find_area(self, game):
        """三块 UI 模板定位缩略图（见文件头）。任一块没找到就沿用上一次的区域（窗口没动的话仍然正确）。"""
        if self.t_title is None or self.t_right is None or self.t_bottom is None:
            return self._find_area_by_color(game)
        x1, y1, x2, y2 = self.region
        gray = cv2.cvtColor(game, cv2.COLOR_BGR2GRAY)
        s_t, ax, ay = self._match(gray, self.t_title, x1, y1, x2, y2)
        if ax is None or s_t < self.anchor_min:
            self.anchor_score = (s_t, 0.0, 0.0)
            return self.area
        th, tw = self.t_title.shape
        # 右锚点：和标题锚点同一行（±2），在它右边
        s_r, rx, ry = self._match(gray, self.t_right, ax + tw, ay - 2, x2, ay + th + 2)
        # 底条纹：标题锚点下方，左边一条窄带（模板宽 40，带宽 +20 让它有地方滑）
        s_b, bx, by = self._match(gray, self.t_bottom, ax + 12, ay + self.anchor_dy + 20, ax + 12 + self.t_bottom.shape[1] + 20, y2)
        self.anchor_score = (s_t, s_r, s_b)
        if rx is None or s_r < self.anchor_min or by is None or s_b < self.anchor_min:
            return self.area
        left, top = ax + self.anchor_dx, ay + self.anchor_dy
        right, bottom = rx + self.right_dx, by
        if right - left < 40 or bottom - top < 20:
            return self.area
        self.area = (left, top, right, bottom)
        return self.area

    def _find_area_by_color(self, game):
        """兜底：按窗口边框颜色定位缩略图：表头(地图名那块)下面、底边框上面；左边框右边、右边框左边。
        找不到就沿用上一次的区域（窗口没动的话仍然正确）。"""
        x1, y1, x2, y2 = self.region
        sub = game[y1:y2, x1:x2].astype(np.int16)
        b, g, r = sub[..., 0], sub[..., 1], sub[..., 2]
        frame = (b > 150) & (b - r > 8) & (b >= g)          # 窗口边框/表头：偏蓝灰的浅色（地图本身是棕色 R>B）
        seg = self._longest_enclosed_run(frame.mean(axis=1) > 0.3, 20)
        if seg is None:
            return self.area
        ya, yb = seg
        # 列方向可能有多段“被边框夹住”的候选：缩略图本身，以及窗口右边框到画面里下一根偏蓝灰竖条之间的游戏背景
        # （岩壁/天空地图上很常见，而且往往比缩略图还宽——2026-08-29 野猪的领土 26% 的帧就这样把黄点弄丢了）。
        # 真缩略图的上边（表头底线）和下边（窗口底边）都是整条实线（实测 ≥0.99），背景段上下没有（≤0.22），按这个筛。
        cands = []
        for xa, xb in self._enclosed_runs(frame[ya:yb].mean(axis=0) > 0.5, 40):
            top, bottom = frame[ya - 1, xa:xb].mean(), frame[yb, xa:xb].mean()
            if top >= self.border_solid and bottom >= self.border_solid:
                cands.append((xa, xb))
        if not cands:
            return self.area
        if self.area is not None:                       # 窗口一般不动：多个候选时沿用离上次最近的那个
            prev = self.area[0] - x1
            xa, xb = min(cands, key=lambda r: abs(r[0] - prev))
        else:
            xa, xb = max(cands, key=lambda r: r[1] - r[0])
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

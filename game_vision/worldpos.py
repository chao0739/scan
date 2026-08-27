"""地图 x 坐标估计（航位推算 + 边界校零）。

屏幕 x 只有在镜头被地图边界顶住时才等于地图位置；镜头跟着人走时屏幕 x 恒在中间。
这里用每帧背景滚动量 bg_dx（roi.measure_scroll，相位相关）累加出镜头位移 cam_x，
    world_x = screen_x + cam_x
两个实测事实决定了下面的设计（2026-08-27，woniu-mogu 地图 10 min bot 日志 + 手动走全图录像）：
  1. 这游戏的镜头是「死区」式的：玩家在屏幕中间 ±300 px 内走动时镜头完全不动，只有人靠近屏幕边缘才滚。
     所以「玩家在走、背景不滚」在地图中段也会出现，不能当作到了地图边界；只有 **人贴近屏幕边缘**（< 0.2 屏宽或 > 0.8 屏宽）
     且镜头仍不滚，才是真的顶到了边界。
  2. 静止时 bg_dx 有 ±0.1~0.3 的噪声，逐帧累加 10 min 漂 89 px；真实滚动是每帧 4~5 px。所以 |bg_dx| < deadband 不累加。
校准：顶在左边界 -> cam_x := 0；顶在右边界 -> cam_x := cam_w（镜头总行程，第一次到右边界时学到，之后 EMA 更新；也可从 settings 预置）。
顶住期间 cam_x 钉住不累加。第一次校零之前 world_x 只是相对值（zeroed=False），端点逻辑应等 zeroed 再用。

地标校准（landmarks）：巡逻路线常常碰不到屏幕边缘（本图 10 min 从未碰到，纯累加漂了 80 px），
所以标端点时顺便存一块玩家头顶上方的背景小图 + 它当时的 world_x；运行时每隔几帧在「预测屏幕位置 ±win」内找它，
匹配分 ≥ thr 就把 cam_x 校到 (地标 world_x − 找到的屏幕 x)。每趟经过端点各校一次，漂移不累积。
有地标时坐标系由地标定义（标定时 cam_x=0 的那一刻为原点），边界归零自动关闭以免两套原点打架。
"""
from collections import deque

import cv2
import numpy as np


def pick_landmark(gray, px, py, half_w=150, boxes=((250, 100), (350, 200), (450, 300), (300, 100)), search=320, min_tex=60):
    """在玩家头顶上方试几个候选框，挑「自相似度最低」的一块做地标。
    地图是瓦片拼的，泥墙/草地花纹每 ~150 px 重复一次；只看纹理强弱选出来的块，运行时会错配到相邻瓦片（回放里修正量 ±150 px）。
    所以对每个候选：在它周围 ±search 内做模板匹配，压掉自身后的次高峰越低越好（越独一无二）。
    返回 dict(x0, y0, gray, tex, second) 或 None。boxes 里每项是 (上边距, 下边距)，相对玩家脚底。"""
    H, W = gray.shape[:2]
    best = None
    for up, dn in boxes:
        x0, x1 = max(0, px - half_w), min(W, px + half_w)
        y0, y1 = max(0, py - up), max(0, py - dn)
        if y1 - y0 < 40 or x1 - x0 < 100:
            continue
        patch = gray[y0:y1, x0:x1]
        tex = float(cv2.Laplacian(patch, cv2.CV_64F).var())
        if tex < min_tex:
            continue
        sx0, sx1 = max(0, x0 - search), min(W, x1 + search)
        res = cv2.matchTemplate(gray[y0:y1, sx0:sx1], patch, cv2.TM_CCOEFF_NORMED)
        _, mx, _, (lx, ly) = cv2.minMaxLoc(res)
        th, tw = patch.shape
        res[:, max(0, lx - tw // 4):lx + tw // 4 + 1] = -1
        second = float(cv2.minMaxLoc(res)[1])
        cand = dict(x0=x0, y0=y0, gray=patch.copy(), tex=tex, second=second)
        if best is None or second < best["second"]:
            best = cand
    return best


class WorldTracker:
    def __init__(self, screen_w=1280, lock_frames=20, move_px=50.0, scroll_px=6.0, cam_w=None,
                 left_zone=0.2, right_zone=0.8, min_cam_w=300.0, deadband=1.0):
        self.screen_w = screen_w
        self.deadband = deadband
        self.left_zone, self.right_zone, self.min_cam_w = left_zone * screen_w, right_zone * screen_w, min_cam_w
        self.cam_x = 0.0
        self.cam_w = cam_w            # 镜头总行程；None=还没学到（可从 settings 预置）
        self.zeroed = False
        self.localized = False        # 至少校准过一次（边界或地标）：之前的 world_x 原点是任意的，端点逻辑不能用
        self.lock = None              # 'left' / 'right' / None
        self.lock_frames, self.move_px, self.scroll_px = lock_frames, move_px, scroll_px
        self._hist = deque(maxlen=lock_frames)   # (玩家屏幕位移, 背景滚动)
        self._prev_px = None
        self.corrections = []         # 每次校准的修正量（评估漂移用；第一次校零不算）
        self.world_x = None
        self.landmarks = []           # [dict(name, gray, world_x, y, thr)]
        self.lm_fixes = 0             # 地标校准次数
        self.last_fix = None          # (name, score, correction)
        # 已校准后，单次修正的合理上限 = base + rate × 上次校准以来累计的镜头滚动量（航位推算每趟只漂 1~2 px，
        # 一次修正 150 px 几乎必然是错配到了同样花纹的相邻瓦片）。连续 relocalize_n 次看到同一个「错」位置才接受（真的跑偏了）。
        self.max_jump_base, self.max_jump_rate, self.relocalize_n = 20.0, 0.05, 20
        self._scroll_since_fix = 0.0
        self._reject = None           # (new_cam, count)

    # ---- 地标 ----
    def add_landmark(self, name, gray_patch, world_x, y, thr=0.8):
        """gray_patch: 灰度小图；world_x: 小图左上角的地图 x；y: 小图左上角屏幕 y（镜头不上下动时固定）。"""
        self.landmarks.append(dict(name=name, gray=gray_patch, world_x=float(world_x), y=int(y), thr=thr))
        self.left_zone, self.right_zone = -1, self.screen_w + 1     # 有地标 -> 关闭边界归零
        self.zeroed = True                                          # 坐标系由地标定义

    def fix_with_landmarks(self, gray_frame, win=160, y_slack=20, min_margin=0.08):
        """在每个地标的预测位置附近找它；找到就校准 cam_x。返回 (name, score, correction) 或 None。
        地图里泥墙/草地纹理是周期重复的，同一窗口里可能有第二个几乎一样高的峰（差一个周期），
        所以除了分数 ≥ thr，还要求最高峰比次高峰（压掉最高峰 ±半个模板宽后）高出 min_margin，否则不校准。"""
        H, W = gray_frame.shape[:2]
        best = None
        for lm in self.landmarks:
            g = lm["gray"]; th, tw = g.shape
            pred_x = int(round(lm["world_x"] - self.cam_x))
            x1, x2 = max(0, pred_x - win), min(W, pred_x + tw + win)
            y1, y2 = max(0, lm["y"] - y_slack), min(H, lm["y"] + th + y_slack)
            if x2 - x1 < tw or y2 - y1 < th:
                continue
            res = cv2.matchTemplate(gray_frame[y1:y2, x1:x2], g, cv2.TM_CCOEFF_NORMED)
            _, mx, _, (lx, ly) = cv2.minMaxLoc(res)
            if mx < lm["thr"]:
                continue
            res[max(0, ly - th // 2):ly + th // 2 + 1, max(0, lx - tw // 4):lx + tw // 4 + 1] = -1
            second = float(cv2.minMaxLoc(res)[1])
            if mx - second < min_margin:
                continue                                    # 有歧义（周期纹理），这次不校
            if best is None or mx > best[1]:
                best = (lm["name"], float(mx), lm["world_x"] - (x1 + lx))
        if best is not None:
            name, score, new_cam = best
            corr = new_cam - self.cam_x
            allowed = self.max_jump_base + self.max_jump_rate * self._scroll_since_fix
            if self.localized and abs(corr) > allowed:
                if self._reject is not None and abs(self._reject[0] - new_cam) <= 10:
                    self._reject = (new_cam, self._reject[1] + 1)
                else:
                    self._reject = (new_cam, 1)
                if self._reject[1] < self.relocalize_n:
                    return None
                self._reject = None                          # 连续多次一致：接受重定位
            else:
                self._reject = None
            self._scroll_since_fix = 0.0
            self.cam_x = new_cam
            self.lm_fixes += 1
            self.localized = True
            self.last_fix = (name, round(score, 3), round(corr, 1))
            if self.landmarks:
                self.corrections.append(corr)
            return self.last_fix
        return None

    def update(self, px, bg_dx):
        """px: 玩家屏幕 x（None=本帧没定位）；bg_dx: 本帧背景滚动量。返回 world_x（或 None）。"""
        if abs(bg_dx) >= self.deadband:
            self.cam_x -= bg_dx       # 背景向右移(dx>0) = 镜头向左移；小于死区的是噪声不累加
            self._scroll_since_fix += abs(bg_dx)
        if px is not None and self._prev_px is not None:
            self._hist.append((px - self._prev_px, bg_dx))
        else:
            self._hist.clear()
        self._prev_px = px
        prev_lock = self.lock
        self.lock = None
        if px is not None and len(self._hist) == self.lock_frames:
            moved = abs(sum(d for d, _ in self._hist))      # 净位移（来回抖动不算走）
            scrolled = sum(abs(b) for _, b in self._hist)
            if moved >= self.move_px and scrolled < self.scroll_px:
                if px < self.left_zone and (self.cam_w is None or self.cam_x < 0.5 * self.cam_w):
                    if self.zeroed and prev_lock != "left":
                        self.corrections.append(self.cam_x - 0.0)
                    self.cam_x, self.zeroed, self.lock = 0.0, True, "left"
                    self.localized = True
                elif px > self.right_zone and self.zeroed and self.cam_x > (self.min_cam_w if self.cam_w is None else 0.5 * self.cam_w):
                    if self.cam_w is None:
                        self.cam_w = self.cam_x
                    elif prev_lock != "right":
                        self.corrections.append(self.cam_x - self.cam_w)
                        self.cam_w = 0.8 * self.cam_w + 0.2 * self.cam_x
                    self.cam_x, self.lock = self.cam_w, "right"
        self.world_x = None if px is None else px + self.cam_x
        return self.world_x

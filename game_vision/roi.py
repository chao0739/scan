"""生成玩家前方搜索 ROI：固定矩形，或先用名牌模板定位玩家。

facing 四种取值：
  right/left  固定朝一侧      both  两侧都检（耗时翻倍）
  auto        用「角色屏幕位移 − 背景滚动」估计前进方向（原地转身不更新，已知弱点）
  key         直接用决策模块按住的方向键（set_facing_hint）——最可靠，且怪只在前进方向被检测，
              不会出现「背后有怪、点一下方向键转身失败、结果背对着怪挥刀」
"""
import cv2
import numpy as np


def clamp_rect(x1, y1, x2, y2, w, h):
    x1, x2 = max(0, int(x1)), min(w, int(x2))
    y1, y2 = max(0, int(y1)), min(h, int(y2))
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2)


class ROIProvider:
    def __init__(self, cfg, frame_w, frame_h):
        self.cfg = cfg
        self.w, self.h = frame_w, frame_h
        self.mode = cfg.get("mode", "fixed")
        self.facing = cfg.get("facing", "right")
        self.last_player = None
        self.player_score = 0.0
        self.lost_frames = 0
        self._tpl_xy = None  # 名牌左上角（跟踪用）
        # facing=auto：用 角色屏幕位移 - 背景滚动位移 = 世界位移 的符号判断前进方向
        fc = cfg.get("facing_auto", {})
        self.fa_scale = fc.get("scale", 0.25)
        self.fa_band = fc.get("band", [0, 100, frame_w, 600])
        self.fa_window = fc.get("window", 6)
        self.fa_min_move = fc.get("min_move", 6)
        self._prev_small = None
        self._prev_px = None
        self._moves = []
        self.current_facing = None  # None=未知（两侧都检）
        self.bg_dx = 0.0
        # measure_scroll：每帧背景水平滚动量（相位相关，平台/树这一层，避开 HUD 和视差天空），P9 卡住检测用
        self.scroll_band = cfg.get("scroll_band", [0, 350, frame_w, 650])
        self._prev_scroll = None
        if self.mode == "player":
            pc = cfg["player"]
            self.tpl = cv2.imread(pc["template"], cv2.IMREAD_GRAYSCALE)
            if self.tpl is None:
                raise RuntimeError(f"玩家名牌模板不存在: {pc['template']}")
            self.band = pc.get("search_band", [0, 0, frame_w, frame_h])
            self.pthr = pc.get("match_threshold", 0.6)        # 全局搜索阈值
            self.local_thr = pc.get("local_threshold", 0.5)   # 局部跟踪阈值
            self.win = pc.get("track_window", 120)
            self.lost_max = pc.get("lost_max", 5)
            # 中间层：小窗跟丢后，先在上一位置附近一个更大的范围里找（靠「位置连续性」区分真假名牌）。
            # 真名牌在屏幕边缘只有 0.67~0.76 分，低于全局阈值 0.82 锁不上；而别人的名牌/梯子也在 0.73~0.75，
            # 光靠分数分不开 -> 只有「它出现在你上一帧的位置附近」这一条能分。mid_threshold=0 关闭。
            self.mid_win = pc.get("mid_window", [300, 120])
            self.mid_thr = pc.get("mid_threshold", 0.70)
            self.adx, self.ady = pc.get("anchor_dx", 0), pc.get("anchor_dy", 0)

    def _match(self, gray, x1, y1, x2, y2):
        th, tw = self.tpl.shape
        sub = gray[y1:y2, x1:x2]
        if sub.shape[0] < th or sub.shape[1] < tw:
            return 0.0, None
        res = cv2.matchTemplate(sub, self.tpl, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        return float(mx), (x1 + loc[0], y1 + loc[1])

    def locate_player(self, game_frame):
        """先在上一位置附近跟踪，连续丢失后再全局搜索；丢失期间沿用上一位置。"""
        gray = cv2.cvtColor(game_frame, cv2.COLOR_BGR2GRAY)
        th, tw = self.tpl.shape
        found = None
        if self._tpl_xy is not None:
            x, y = self._tpl_xy
            mx, loc = self._match(gray, max(0, x - self.win), max(0, y - self.win),
                                  min(self.w, x + self.win + tw), min(self.h, y + self.win + th))
            self.player_score = mx
            if mx >= self.local_thr:
                found = loc
            elif self.mid_thr:
                mw, mh = self.mid_win
                mx2, loc2 = self._match(gray, max(0, x - mw), max(0, y - mh),
                                        min(self.w, x + mw + tw), min(self.h, y + mh + th))
                if mx2 >= self.mid_thr:
                    self.player_score = mx2
                    found = loc2
        if found is None:
            self.lost_frames += 1
            if self._tpl_xy is None or self.lost_frames > self.lost_max:
                bx1, by1, bx2, by2 = self.band
                mx, loc = self._match(gray, bx1, by1, bx2, by2)
                self.player_score = mx
                if mx >= self.pthr:
                    found = loc
        if found is not None:
            self.lost_frames = 0
            self._tpl_xy = found
            self.last_player = (found[0] + self.adx, found[1] + self.ady)
        return self.last_player

    def update_facing(self, game_frame, px):
        """估计玩家世界位移方向。背景位移用相位相关法（对缩小后的灰度图）。"""
        bx1, by1, bx2, by2 = self.fa_band
        small = cv2.cvtColor(game_frame[by1:by2, bx1:bx2], cv2.COLOR_BGR2GRAY)
        small = cv2.resize(small, None, fx=self.fa_scale, fy=self.fa_scale, interpolation=cv2.INTER_AREA)
        small = np.float32(small)
        if self._prev_small is not None and self._prev_px is not None:
            (dx, dy), resp = cv2.phaseCorrelate(self._prev_small, small)
            self.bg_dx = dx / self.fa_scale if resp > 0.05 else 0.0
            world_dx = (px - self._prev_px) - self.bg_dx
            self._moves.append(world_dx)
            self._moves = self._moves[-self.fa_window:]
            total = sum(self._moves)
            if total > self.fa_min_move:
                self.current_facing = "right"
            elif total < -self.fa_min_move:
                self.current_facing = "left"
        self._prev_small = small
        self._prev_px = px

    def measure_scroll(self, game_frame):
        """估计本帧相对上一帧的背景水平滚动量(px)，存到 self.bg_dx 并返回。
        镜头跟着人走时人在屏幕上不动但背景在滚；卡住时两者都不动 —— 这是区分「在走」和「卡住」的唯一依据。
        1280x720 上 0.25 缩放的相位相关约 1 ms。实测（本机位录像）响应 0.97~0.99，没有失败帧。"""
        bx1, by1, bx2, by2 = self.scroll_band
        small = cv2.cvtColor(game_frame[by1:by2, bx1:bx2], cv2.COLOR_BGR2GRAY)
        small = np.float32(cv2.resize(small, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA))
        if self._prev_scroll is not None:
            (dx, _dy), resp = cv2.phaseCorrelate(self._prev_scroll, small)
            self.bg_dx = dx * 4 if resp > 0.05 else 0.0
        self._prev_scroll = small
        return self.bg_dx

    def set_facing_hint(self, facing):
        """由决策模块的按键状态直接给出朝向（比位移估计更可靠）。"""
        if facing in ("left", "right") and facing != self.current_facing:
            self.current_facing = facing
            self._moves = []

    def rois(self, game_frame):
        """返回 [(name, (x1,y1,x2,y2)), ...]"""
        if self.mode == "fixed":
            r = clamp_rect(*self.cfg["fixed"], self.w, self.h)
            return [("fixed", r)] if r else []
        p = self.locate_player(game_frame)
        if p is None:
            return []
        px, py = p
        c = self.cfg
        facing = self.facing
        if facing == "auto":
            self.update_facing(game_frame, px)
            facing = self.current_facing or "both"
        elif facing == "key":
            # 朝向完全由 set_facing_hint（决策按住的方向键）给；还没按过键时先两侧都检
            facing = self.current_facing or "both"
        out = []
        if facing in ("right", "both"):
            r = clamp_rect(px + c["near_offset"], py - c["up"], px + c["far_offset"], py + c["down"], self.w, self.h)
            if r:
                out.append(("right", r))
        if facing in ("left", "both"):
            r = clamp_rect(px - c["far_offset"], py - c["up"], px - c["near_offset"], py + c["down"], self.w, self.h)
            if r:
                out.append(("left", r))
        return out

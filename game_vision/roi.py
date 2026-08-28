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


def tighten_nameplate(img, margin=2, white=170):
    """把用户框的名牌区域自动收紧到「名字文字」四周（文字外扩 margin 像素）。返回 (x0, y0, x1, y1)，相对 img。

    为什么：名牌条是半透明的，条里除了白字都是透过来的背景，框得越宽背景占比越大、换个地方分数越低
    （实测整条 40×16 在暗背景 0.76，收紧到 32×12 后 0.80~0.93）；连勋章一起框（100×33）更是只有 0.57。
    做法：白色(>white)连通块里挑「字符块」——高 4~14、宽 1~14，且块的上方/下方 3px 内多数是深色(<dark)（名字写在深色条上；
    勋章牌是蓝色、沙地/白宠物周围是亮色，都不算）。字符块按行分组（同一行且相邻间隔≤6px），≥3 个字符、总宽 12~90 的行才算名字行
    （贴着的宠物名牌隔着 ≥7px，是另一行；勋章不满足深色底），多行取最靠上的（勋章在名字下面）。找不到就原样返回。"""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    h, w = g.shape[:2]
    dark = 90
    n, _lab, st, _cen = cv2.connectedComponentsWithStats((g > white).astype(np.uint8), 8)
    glyphs = []
    for i in range(1, n):
        x, y, gw, gh, area = (int(v) for v in st[i])
        if not (4 <= gh <= 14 and 1 <= gw <= 14 and area >= 4):
            continue
        nb = np.concatenate([g[max(0, y - 3):y, x:x + gw].ravel(), g[y + gh:y + gh + 3, x:x + gw].ravel()])
        if nb.size == 0 or (nb < dark).mean() < 0.5:
            continue
        glyphs.append((x, y, x + gw, y + gh))
    if not glyphs:
        return 0, 0, w, h
    glyphs.sort()
    lines = []                                  # 每行: [x0, y0, x1, y1, count]
    for x0, y0, x1, y1 in glyphs:
        cy = (y0 + y1) / 2
        for L in lines:
            if abs((L[1] + L[3]) / 2 - cy) <= 4 and x0 - L[2] <= 6:
                L[0], L[1], L[2], L[3], L[4] = min(L[0], x0), min(L[1], y0), max(L[2], x1), max(L[3], y1), L[4] + 1
                break
        else:
            lines.append([x0, y0, x1, y1, 1])
    cands = [L for L in lines if L[4] >= 3 and 12 <= L[2] - L[0] <= 90 and 5 <= L[3] - L[1] <= 16]
    if not cands:
        return 0, 0, w, h
    x0, y0, x1, y1, _ = min(cands, key=lambda L: L[1])
    return max(0, x0 - margin), max(0, y0 - margin), min(w, x1 + margin), min(h, y1 + margin)


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
            # 边缘层：角色贴在屏幕最左/最右时名牌被画面切掉一半，整张模板永远匹配不上（真机曾因此 LOST 150 s 一动不动）。
            # 全局搜索失败后，在左边缘窄条里用模板的右半、右边缘窄条里用左半再试一次。edge_threshold=0 关闭。
            self.edge_thr = pc.get("edge_threshold", 0.72)
            th, tw = self.tpl.shape
            self._tpl_r, self._tpl_l = self.tpl[:, tw // 2:], self.tpl[:, :tw - tw // 2]
            self.adx, self.ady = pc.get("anchor_dx", 0), pc.get("anchor_dy", 0)

    def _match(self, gray, x1, y1, x2, y2):
        th, tw = self.tpl.shape
        # 任何搜索窗都裁到 search_band 内：名牌只可能出现在游戏区，HUD（聊天栏/按钮文字）里的黑底白字能匹配到 0.7，
        # 真机曾因此锁死在 y=646 的 HUD 文字上 35 s（run_20260827_130712）
        bx1, by1, bx2, by2 = self.band
        x1, y1, x2, y2 = max(x1, bx1), max(y1, by1), min(x2, bx2), min(y2, by2)
        if x2 - x1 < tw or y2 - y1 < th:
            return 0.0, None
        sub = gray[y1:y2, x1:x2]
        if sub.shape[0] < th or sub.shape[1] < tw:
            return 0.0, None
        res = cv2.matchTemplate(sub, self.tpl, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        return float(mx), (x1 + loc[0], y1 + loc[1])

    def _edge_search(self, gray):
        """屏幕左右边缘窄条里匹配半张模板。返回等效的整张模板左上角（可能在画面外），找不到 None。"""
        th, tw = self.tpl.shape
        bx1, by1, bx2, by2 = self.band
        best, best_xy = 0.0, None
        for half, x1, x2, dx in ((self._tpl_r, 0, tw + 10, -(tw // 2)),          # 左边缘：只看得见名牌右半
                                 (self._tpl_l, self.w - tw - 10, self.w, 0)):    # 右边缘：只看得见左半
            sub = gray[by1:by2, max(0, x1):min(self.w, x2)]
            if sub.shape[0] < th or sub.shape[1] < half.shape[1]:
                continue
            _, mx, _, loc = cv2.minMaxLoc(cv2.matchTemplate(sub, half, cv2.TM_CCOEFF_NORMED))
            if mx > best:
                best, best_xy = float(mx), (max(0, x1) + loc[0] + dx, by1 + loc[1])
        if best >= self.edge_thr:
            self.player_score = best
            return best_xy
        return None

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
        if found is None and self.edge_thr and self._tpl_xy is not None and (self._tpl_xy[0] < tw or self._tpl_xy[0] > self.w - 2 * tw):
            found = self._edge_search(gray)      # 上一位置本来就贴边：整张模板必然匹配不上，直接用半模板
        if found is None:
            self.lost_frames += 1
            if self._tpl_xy is None or self.lost_frames > self.lost_max:
                bx1, by1, bx2, by2 = self.band
                mx, loc = self._match(gray, bx1, by1, bx2, by2)
                self.player_score = mx
                if mx >= self.pthr:
                    found = loc
                elif self.edge_thr:
                    found = self._edge_search(gray)
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

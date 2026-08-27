"""P5 决策状态机：检测结果 -> 按键动作（识别 → 移动 → 攻击）。

输入（每帧）：玩家是否定位到、是否检测到怪、怪在哪一侧、怪离玩家多远、当前朝向。
输出：通过 Actuator 发按键（Pico）或只打日志（dry-run）。

策略 v1（参数全部在 config.yaml 的 control 段）：
  有怪：朝向不对 -> 点一下方向键转身；距离 > attack_range_px -> 按住方向键接近；否则按 attack_interval_ms 节奏按攻击键，
        拾取键 pickup_key（z）：控制期间每隔 pickup_interval_ms=[min,max] 内随机的时间点按一次，与状态无关
  没怪：巡逻（patrol=false 则原地等待）。两种模式：
        位置巡逻(P7)——设了 patrol_left_x/right_x 时，按住方向键朝目标端点走，玩家 x 进到端点 tolerance 内就换另一端；
        时间巡逻——没设端点时退化为原来的「走 walk_max_ms 后掉头」
P9 卡住检测（只报警，不动作）：按着方向键 stuck_ms 内玩家屏幕 x 没动(<=stuck_move_px) 且背景没滚(<=stuck_scroll_px)
  -> self.stuck=True（日志 stuck 字段 / 画面 STUCK）。镜头跟随时人不动但背景在滚，不算卡住；没按方向键不算卡住。
P10 跳跃恢复（jump_on_stuck=true 时）：巡逻中卡住 -> 方向键继续按着 + 点一下 jump_key -> jump_recover_ms 内不再判定；
  仍卡住则重试，连续 jump_max_retry 次无效 -> 放弃：掉头朝另一端走（重试计数在人动起来后清零）。
安全：
  暂停(p) / 玩家丢失 lost_release_frames 帧 -> 松开全部按键
  每 sync_interval_ms 重发一次期望的按键状态（UDP 丢包导致的卡键最多持续这么久）
  退出时 actuator.close() -> RELEASE_ALL
"""
import random
import time


class DryRunActuator:
    """只打日志不发命令。"""

    def __init__(self, log=print):
        self.log = log
        self.sent = []
        self.frame_cmds = []          # 本帧发出的命令（app.py 写进日志后清空）

    def _emit(self, cmd):
        self.sent.append(cmd)
        self.frame_cmds.append(cmd)
        self.log(f"[dry] {cmd}")

    def key_down(self, key):
        self._emit(f"KEY_DOWN {key}")

    def key_up(self, key):
        self._emit(f"KEY_UP {key}")

    def key_press(self, key, ms):
        self._emit(f"KEY_PRESS {key} {ms}")

    def release_all(self):
        self._emit("RELEASE_ALL")

    def close(self):
        self.release_all()


class PicoActuator:
    """把动作转成 Pico UDP 命令。控制命令不等回复、走 PicoClient 的节拍队列（Pico 收不了连发包，见 pico_client.py）。"""

    def __init__(self, client):
        self.pc = client
        self.pc.start_heartbeat()
        self.frame_cmds = []          # 本帧发出的命令（app.py 写进日志后清空）

    def _send(self, cmd):
        self.frame_cmds.append(cmd)
        self.pc.send(cmd, wait_reply=False)

    def key_down(self, key):
        self._send(f"KEY_DOWN {key}")

    def key_up(self, key):
        self._send(f"KEY_UP {key}")

    def key_press(self, key, ms):
        self._send(f"KEY_PRESS {key} {ms}")

    def release_all(self):
        self._send("RELEASE_ALL")

    def close(self):
        self.pc.close()


DEFAULTS = dict(attack_key="ctrl", attack_press_ms=60, attack_interval_ms=700, attack_range_px=130,
                pickup_key="z", pickup_press_ms=60, pickup_interval_ms=(500, 1000),
                range_hysteresis_px=40, attack_linger_ms=800,
                approach=True, patrol=True, walk_max_ms=3000, turn_press_ms=60,
                patrol_left_x=None, patrol_right_x=None, patrol_tolerance=30,
                stuck_ms=1500, stuck_move_px=3, stuck_scroll_px=1.5,
                jump_on_stuck=False, jump_key="alt", jump_press_ms=80, jump_recover_ms=900, jump_max_retry=3,
                jump_clear_px=40, giveup_walk_ms=4000,
                lost_release_frames=10, sync_interval_ms=1000, start_dir="right")


class Decision:
    def __init__(self, cfg, actuator, now=None):
        c = dict(DEFAULTS)
        c.update({k: v for k, v in (cfg or {}).items() if v is not None})
        self.c = c
        self.act = actuator
        self.paused = False
        self.state = "IDLE"
        self.facing = c["start_dir"]      # 我们认为角色现在朝哪（按键状态推得）
        self.held = None                  # 当前按住的方向键：None / "left" / "right"
        self.next_pickup = 0.0            # 下次按拾取键的时间
        self.walk_start = None
        self.patrol_target = None         # 位置巡逻当前朝哪个端点走："left"/"right"/None(未设端点)
        self.stuck = False                # P9：按着方向键但人和背景都没动
        self.stuck_count = 0              # 进入卡住状态的次数（日志/验收用）
        self._still_since = None          # 上次「有位移」的时间
        self._still_x = None
        self.jump_count = 0               # P10：跳跃恢复次数
        self.giveup_count = 0             # P10：重试用尽掉头次数
        self._retry = 0                   # 当前这次卡住已连续跳了几次
        self._recover_until = 0.0         # 跳完等待观察的截止时间
        self._stuck_x = None              # 这一串卡住开始时的 x；离开它 jump_clear_px 以上才算脱困
        self._stuck_scroll = 0.0          # 卡住以来累计的背景滚动（镜头跟随时人不动但在走）
        self._no_flip_until = 0.0         # 放弃掉头后这段时间内端点逻辑不许再翻转（否则卡在端点外侧时会 0 秒来回翻）
        self.last_attack = 0.0
        self.last_seen = None            # (time, side) 最近一次看到怪
        self.lost_frames = 0
        self.last_sync = now if now is not None else time.monotonic()

    # ---- 底层按键，带状态去重 ----
    def _hold(self, d, now):
        if self.held == d:
            return
        if self.held is not None:
            self.act.key_up(self.held)
        self.act.key_down(d)
        self.held = d
        self.facing = d
        self.walk_start = now

    def _release_dir(self):
        if self.held is not None:
            self.act.key_up(self.held)
            self.held = None

    def _turn_to(self, d):
        if self.facing != d:
            self.act.key_press(d, self.c["turn_press_ms"])
            self.facing = d

    def release_all(self):
        self.held = None
        self.act.release_all()

    def _maybe_pickup(self, now):
        """每 0.5~1 s 随机点按一次拾取键。"""
        if self.c.get("pickup_key") and now >= self.next_pickup:
            self.act.key_press(self.c["pickup_key"], self.c["pickup_press_ms"])
            lo, hi = self.c["pickup_interval_ms"]
            self.next_pickup = now + random.uniform(lo, hi) / 1000

    def patrol_bounds(self):
        """返回 (left_x, right_x, tolerance)；没设端点则 (None, None, tol)。左右写反了也认。"""
        l, r = self.c["patrol_left_x"], self.c["patrol_right_x"]
        if l is None or r is None:
            return None, None, self.c["patrol_tolerance"]
        l, r = float(min(l, r)), float(max(l, r))
        return l, r, self.c["patrol_tolerance"]

    def _patrol(self, now, player_x):
        """P7 位置巡逻：朝当前目标端点走，进到端点 tolerance 内就换另一端。
        端点未标定（或这一帧不知道 player_x）时退回原来的「走 walk_max_ms 后掉头」。"""
        l, r, tol = self.patrol_bounds()
        if l is None or player_x is None:
            self.patrol_target = None
            if self.held is None:
                self._hold(self.facing or self.c["start_dir"], now)
            elif (now - self.walk_start) * 1000 >= self.c["walk_max_ms"]:
                self._hold("left" if self.held == "right" else "right", now)
            return
        if self.patrol_target is None:                      # 第一帧：朝更远的那一端走
            self.patrol_target = "left" if abs(player_x - l) > abs(player_x - r) else "right"
        # 只在「到达目标端」时翻转，所以坐标抖动不会来回换向（另一端还远着）
        if now < self._no_flip_until:
            pass                                             # 刚放弃掉头：先走开一段再让端点逻辑说话
        elif self.patrol_target == "right" and player_x >= r - tol:
            self.patrol_target = "left"
        elif self.patrol_target == "left" and player_x <= l + tol:
            self.patrol_target = "right"
        self._hold(self.patrol_target, now)

    def _update_stuck(self, now, player_x, bg_dx):
        """P9：只判定、只报警。"""
        c = self.c
        if self.held is None or player_x is None:
            self._still_since, self._still_x, self.stuck = None, None, False
            return
        if (self._still_x is None or abs(player_x - self._still_x) > c["stuck_move_px"]
                or abs(bg_dx) > c["stuck_scroll_px"]):
            self._still_since, self._still_x = now, player_x   # 人动了 或 镜头在滚 -> 重新计时
        if self._retry:
            # 跳过一次之后：只有真正离开卡住点（位移+镜头滚动 > jump_clear_px）才算脱困、清零重试；
            # 顶着墙跳会弹回原地、位移只有十几像素，不能算脱困，否则会在墙前无限跳
            if abs(bg_dx) > c["stuck_scroll_px"]:          # 只累计真实滚动，不累计每帧 0.1~0.3 的噪声
                self._stuck_scroll += abs(bg_dx)
            if abs(player_x - self._stuck_x) + self._stuck_scroll > c["jump_clear_px"]:
                self._retry = 0
        was = self.stuck
        self.stuck = (now - self._still_since) * 1000 >= c["stuck_ms"]
        if self.stuck and not was:
            self.stuck_count += 1

    def set_paused(self, paused):
        self.paused = paused
        if paused:
            self.release_all()
            self.state = "PAUSED"

    # ---- 主入口 ----
    def update(self, player_found, detected, side, dist, now=None, player_x=None, bg_dx=0.0):
        """返回本帧的状态名。side: 'left'/'right'/None；dist: 怪到玩家的水平像素距离（未知给 None）；
        player_x: 玩家在矫正后画面里的 x（位置巡逻用；给 None 则退回时间巡逻）；bg_dx: 本帧背景滚动量(px)。"""
        now = time.monotonic() if now is None else now
        c = self.c
        if self.paused:
            return "PAUSED"

        # 玩家丢失 -> 松开
        if not player_found:
            self.lost_frames += 1
            if self.lost_frames >= c["lost_release_frames"] and (self.held is not None or self.state != "LOST"):
                self.release_all()
                self.state = "LOST"
            self._update_stuck(now, None, bg_dx)
            return self.state
        self.lost_frames = 0
        self._maybe_pickup(now)

        if detected and side in ("left", "right"):
            self.last_seen = (now, side)
        elif self.last_seen and (now - self.last_seen[0]) * 1000 < c["attack_linger_ms"] and self.state == "ATTACK":
            # 怪刚被打闪白/短暂遮挡时检测会闪断：攻击中就地多打一会儿，不要立刻走开
            detected, side, dist = True, self.last_seen[1], 0
        if detected and side in ("left", "right"):
            if self.held is not None and self.held != side:
                self._release_dir()
            self._turn_to(side)
            # 距离滞回：进入攻击范围后要远出 hysteresis 才重新接近，避免在边界上抖
            rng = c["attack_range_px"] + (c["range_hysteresis_px"] if self.state == "ATTACK" else 0)
            if c["approach"] and dist is not None and dist > rng:
                self._hold(side, now)
                self.state = "APPROACH"
            else:
                self._release_dir()
                # 间隔不能短于 KEY_PRESS 的执行时间，否则命令在 Pico 那边排队越积越多、动作越来越滞后
                if (now - self.last_attack) * 1000 >= max(c["attack_interval_ms"], c["attack_press_ms"] + 20):
                    self.act.key_press(c["attack_key"], c["attack_press_ms"])
                    self.last_attack = now
                self.state = "ATTACK"
        else:
            if c["patrol"]:
                self._patrol(now, player_x)
                self.state = "PATROL"
            else:
                self._release_dir()
                self.state = "IDLE"

        self._update_stuck(now, player_x, bg_dx)
        if self.stuck and self.state == "PATROL" and c["jump_on_stuck"] and now >= self._recover_until:
            if self._retry < c["jump_max_retry"]:
                if self._retry == 0:
                    self._stuck_x, self._stuck_scroll = player_x, 0.0
                # 方向键保持按着，点一下跳跃键 = 向前跳；之后给它 jump_recover_ms 观察有没有脱困
                self.act.key_press(c["jump_key"], c["jump_press_ms"])
                self._retry += 1
                self.jump_count += 1
                self._recover_until = now + c["jump_recover_ms"] / 1000
                self._still_since = now          # 重新计时：跳跃本身不算位移，落地后 stuck_ms 内没动才算仍卡住
                self.state = "RECOVER"
            else:
                # 跳了 N 次还卡着：放弃，掉头走。位置巡逻改目标端；时间巡逻直接换方向
                self.giveup_count += 1
                self._retry = 0
                self._no_flip_until = now + c["giveup_walk_ms"] / 1000
                if self.patrol_target:
                    self.patrol_target = "left" if self.patrol_target == "right" else "right"
                    self._hold(self.patrol_target, now)
                else:
                    self._hold("left" if self.held == "right" else "right", now)
                self._still_since = now
                self.state = "GIVEUP"

        # 定期同步按键状态：防止 UDP 丢包造成的卡键/漏按
        if (now - self.last_sync) * 1000 >= c["sync_interval_ms"]:
            self.last_sync = now
            if self.held is None:
                self.act.release_all()   # 清掉可能卡住的键
            else:
                # 先补发对侧 KEY_UP：万一换向时的 KEY_UP 丢包，Pico 会同时按着左右（P8 验收项），最多持续到这里
                self.act.key_up("left" if self.held == "right" else "right")
                self.act.key_down(self.held)
        return self.state

    def close(self):
        self.held = None
        self.act.close()

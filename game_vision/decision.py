"""P5 决策状态机：检测结果 -> 按键动作（识别 → 移动 → 攻击）。

输入（每帧）：玩家是否定位到、是否检测到怪、怪在哪一侧、怪离玩家多远、当前朝向。
输出：通过 Actuator 发按键（Pico）或只打日志（dry-run）。

策略 v1（参数全部在 config.yaml 的 control 段）：
  有怪：朝向不对 -> 点一下方向键转身；距离 > attack_range_px -> 按住方向键接近；否则按 attack_interval_ms 节奏按攻击键，
        拾取键 pickup_key（z）：控制期间每隔 pickup_interval_ms=[min,max] 内随机的时间点按一次，与状态无关
  没怪：按住方向键巡逻，走 walk_max_ms 没遇到怪就掉头（patrol=false 则原地等待）
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

    def _emit(self, cmd):
        self.sent.append(cmd)
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
    """把动作转成 Pico UDP 命令。控制命令不等回复（避免阻塞视觉循环）；KEY_UP/RELEASE_ALL 发两遍抗丢包。"""

    def __init__(self, client):
        self.pc = client
        self.pc.start_heartbeat()

    def key_down(self, key):
        self.pc.send(f"KEY_DOWN {key}", wait_reply=False)

    def key_up(self, key):
        for _ in range(2):
            self.pc.send(f"KEY_UP {key}", wait_reply=False)

    def key_press(self, key, ms):
        self.pc.send(f"KEY_PRESS {key} {ms}", wait_reply=False)

    def release_all(self):
        for _ in range(2):
            self.pc.send("RELEASE_ALL", wait_reply=False)

    def close(self):
        self.pc.close()


DEFAULTS = dict(attack_key="ctrl", attack_press_ms=60, attack_interval_ms=700, attack_range_px=130,
                pickup_key="z", pickup_press_ms=60, pickup_interval_ms=(500, 1000),
                range_hysteresis_px=40, attack_linger_ms=800,
                approach=True, patrol=True, walk_max_ms=3000, turn_press_ms=60,
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

    def set_paused(self, paused):
        self.paused = paused
        if paused:
            self.release_all()
            self.state = "PAUSED"

    # ---- 主入口 ----
    def update(self, player_found, detected, side, dist, now=None):
        """返回本帧的状态名。side: 'left'/'right'/None；dist: 怪到玩家的水平像素距离（未知给 None）。"""
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
                if self.held is None:
                    self._hold(self.facing or c["start_dir"], now)
                elif (now - self.walk_start) * 1000 >= c["walk_max_ms"]:
                    other = "left" if self.held == "right" else "right"
                    self._hold(other, now)
                self.state = "PATROL"
            else:
                self._release_dir()
                self.state = "IDLE"

        # 定期同步按键状态：防止 UDP 丢包造成的卡键/漏按
        if (now - self.last_sync) * 1000 >= c["sync_interval_ms"]:
            self.last_sync = now
            if self.held is None:
                self.act.release_all()   # 清掉可能卡住的键
            else:
                self.act.key_down(self.held)
        return self.state

    def close(self):
        self.held = None
        self.act.close()

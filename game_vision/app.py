"""主程序：摄像头/视频 -> 透视矫正 -> 玩家前方 ROI -> 模板匹配 -> 去抖 -> 输出。

用法：
  python app.py                         # 用 config.yaml 里的摄像头，启动时选择怪物
  python app.py --source ../shot.mp4 --calib calibration/homography_shot_mp4.json   # 离线回放
  python app.py --calibrate             # 重新标定四角
  python app.py --monster stump_map01   # 直接指定怪物（跳过选择菜单）
  python app.py --no-show               # 无窗口模式（只打日志）

运行窗口按键：
  q 退出   空格 暂停/继续   m 切换到下一个怪物   t 在当前画面拖框新增模板   c 重新标定
  r 开始/停止录制摄像头原始画面到 recordings/（用于离线采模板、调参）
"""
import argparse
import json
import os
import queue
import sys
import threading
import time

import cv2
import yaml

from camera import FrameSource, open_writer
from calibration import Rectifier, auto_corners, load_corners, run_calibration_ui, save_corners
from debounce import Debouncer
from detector import TemplateDetector
from roi import ROIProvider
from decision import Decision, DryRunActuator, PicoActuator
from worldpos import WorldTracker
from minimap import MinimapTracker

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_ROOT = "templates"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台中文输出


SETTINGS_FILE = os.path.join(HERE, "settings.yaml")


def _deep_merge(base, over):
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config(path):
    """config.yaml 为默认值，settings.yaml（菜单里保存的用户设置）覆盖其上。"""
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            _deep_merge(cfg, yaml.safe_load(f) or {})
    return cfg


def save_settings(settings):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(settings, f, allow_unicode=True, sort_keys=False)


def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def list_monsters():
    """templates/ 下每个含 png 的子目录算一个怪物模板集。"""
    out = []
    for d in sorted(os.listdir(TEMPLATE_ROOT)):
        p = os.path.join(TEMPLATE_ROOT, d)
        if d == "players":
            continue
        if os.path.isdir(p) and any(f.lower().endswith(".png") for f in os.listdir(p)):
            out.append(d)
    return out


def choose_monster(default):
    monsters = list_monsters()
    if not monsters:
        raise RuntimeError(f"{TEMPLATE_ROOT}/ 下没有任何模板目录")
    print("\n可用怪物模板集：")
    for i, m in enumerate(monsters, 1):
        n = len([f for f in os.listdir(os.path.join(TEMPLATE_ROOT, m)) if f.lower().endswith(".png")])
        mark = " (默认)" if m == default else ""
        print(f"  [{i}] {m}  ({n} 张模板){mark}")
    while True:
        try:
            s = input(f"选择编号或名称，回车用默认 [{default}]: ").strip()
        except EOFError:
            s = ""
        if not s:
            return default if default in monsters else monsters[0]
        if s.isdigit() and 1 <= int(s) <= len(monsters):
            return monsters[int(s) - 1]
        if s in monsters:
            return s
        print("无效输入，请重试")


def build_detector(monster, det_cfg):
    return TemplateDetector(os.path.join(TEMPLATE_ROOT, monster), det_cfg["threshold"],
                            det_cfg.get("scales", [1.0]), det_cfg.get("grayscale", True),
                            det_cfg.get("flip", True), det_cfg.get("color_verify"),
                            sure_score=det_cfg.get("sure_score", 0), motion_min=det_cfg.get("motion_min", 5))


class AsyncWriter:
    """VideoWriter 放到后台线程：主循环只把帧丢进队列（满了就丢帧），不被编码耗时拖慢。"""

    def __init__(self, writer):
        self.w = writer
        self.q = queue.Queue(maxsize=60)
        self.dropped = 0
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self):
        while True:
            f = self.q.get()
            if f is None:
                break
            self.w.write(f)

    def write(self, frame):
        try:
            self.q.put_nowait(frame)
        except queue.Full:
            self.dropped += 1

    def release(self):
        self.q.put(None)
        self._t.join(timeout=10)
        self.w.release()
        if self.dropped:
            print(f"[rec] 录像丢帧 {self.dropped}（编码跟不上）")


def crop_template_ui(game_frame, monster):
    """暂停在当前矫正画面上拖框保存一张模板。返回是否保存了。"""
    win = f"crop template -> {monster}  (drag box, s save, esc cancel)"
    box = {"p0": None, "p1": None}

    def on_mouse(ev, x, y, flags, _):
        if ev == cv2.EVENT_LBUTTONDOWN:
            box["p0"], box["p1"] = (x, y), (x, y)
        elif ev == cv2.EVENT_MOUSEMOVE and flags & cv2.EVENT_FLAG_LBUTTON:
            box["p1"] = (x, y)
        elif ev == cv2.EVENT_LBUTTONUP:
            box["p1"] = (x, y)

    cv2.namedWindow(win)
    cv2.setMouseCallback(win, on_mouse)
    saved = False
    while True:
        vis = game_frame.copy()
        if box["p0"] and box["p1"]:
            cv2.rectangle(vis, box["p0"], box["p1"], (0, 255, 0), 1)
        cv2.imshow(win, vis)
        k = cv2.waitKey(30) & 0xFF
        if k == 27:
            break
        if k == ord("s") and box["p0"] and box["p1"]:
            (x0, y0), (x1, y1) = box["p0"], box["p1"]
            x0, x1 = sorted((x0, x1))
            y0, y1 = sorted((y0, y1))
            if x1 - x0 > 4 and y1 - y0 > 4:
                d = os.path.join(TEMPLATE_ROOT, monster)
                os.makedirs(d, exist_ok=True)
                p = os.path.join(d, time.strftime("t_%Y%m%d_%H%M%S.png"))
                cv2.imwrite(p, game_frame[y0:y1, x0:x1])
                print(f"[template] 已保存 {p} 尺寸 {x1 - x0}x{y1 - y0}")
                saved = True
                break
    cv2.destroyWindow(win)
    return saved


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "config.yaml"))
    ap.add_argument("--source", default=None, help="摄像头编号或视频路径，覆盖配置")
    ap.add_argument("--calibrate", action="store_true", help="强制重新标定")
    ap.add_argument("--auto-calib", action="store_true",
                    help="自动标定：整幅画面就是游戏画面（NDI/采集卡），取非黑区域外接矩形当四角并保存")
    ap.add_argument("--calib", default=None, help="标定文件路径（默认 config 里的 screen.calibration_file）")
    ap.add_argument("--no-show", action="store_true")
    ap.add_argument("--monster", default=None, help="怪物模板集名称（templates/ 下的目录名）")
    ap.add_argument("--start", type=int, default=0, help="视频起始帧")
    ap.add_argument("--realtime", action="store_true", help="视频回放按原速播放")
    ap.add_argument("--control", default=None, choices=["off", "dry", "pico"], help="控制模式，覆盖 config.control.mode")
    ap.add_argument("--pico", default=None, help="Pico IP（覆盖 config.control.pico_host）")
    ap.add_argument("--seconds", type=float, default=0, help="运行这么多秒后自动停止（0=不限；自动化真机测试用）")
    ap.add_argument("--record", action="store_true", help="启动即录制摄像头原始画面到 recordings/（同窗口按 r）")
    ap.add_argument("--set", action="append", default=[], metavar="a.b.c=value",
                    help="临时覆盖任意配置项（不写入 settings），可多次。如 --set control.jump_on_stuck=true")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    for kv in args.set:
        k, _, v = kv.partition("=")
        d = cfg
        for part in k.split(".")[:-1]:
            d = d.setdefault(part, {})
        d[k.split(".")[-1]] = yaml.safe_load(v)   # 类型按 YAML 解析：true/3/0.5/字符串
        print(f"[cfg] 覆盖 {k} = {d[k.split('.')[-1]]!r}")
    # OpenCV 线程数：ROI 只有 300x90，matchTemplate 多线程切不开，8 线程(默认)只是线程池空转——实测帧率一样、CPU 241% vs 2 线程 148%
    cv2.setNumThreads(int((cfg.get("app") or {}).get("cv_threads", 2)))
    source = args.source if args.source is not None else cfg["camera"]["source"]
    if isinstance(source, str) and os.path.exists(source):
        source = os.path.abspath(source)  # 视频文件；摄像头编号/名称关键字原样交给 FrameSource
    os.chdir(HERE)  # 配置里的相对路径都相对于工程目录

    # ---- 人工初始化 1：选择怪物 ----
    mon_cfg = cfg.get("monster", {})
    monster = args.monster or mon_cfg.get("current", "")
    if args.monster is None and mon_cfg.get("ask_on_start", True):
        monster = choose_monster(monster)
    print(f"[app] 当前怪物模板集: {monster}")

    cam = cfg["camera"]
    src = FrameSource(str(source), cam["width"], cam["height"], cam.get("loop_video", False),
                      ndi_transport=cam.get("ndi_transport", "tcp"))
    if args.start and src.is_file:
        src.seek(args.start)

    # ---- 人工初始化 2：屏幕四角标定 ----
    sc = cfg["screen"]
    if args.calib:
        sc["calibration_file"] = os.path.abspath(args.calib) if os.path.isabs(args.calib) else args.calib
    out_w, out_h = sc["output_width"], sc["output_height"]
    print(f"[calib] 标定文件: {sc['calibration_file']}")
    corners = None if (args.calibrate or args.auto_calib) else load_corners(sc["calibration_file"])
    if corners is None and args.auto_calib:
        ok, frame = src.read()
        if not ok:
            raise RuntimeError("读不到画面，无法自动标定")
        corners, size = auto_corners(frame)
        save_corners(sc["calibration_file"], corners, size)
        print(f"[calib] 自动标定：画面 {size[0]}x{size[1]}，游戏区域 {corners.tolist()} -> 已保存")
    if corners is None:
        print("[calib] 未找到标定文件或要求重新标定，进入标定界面")
        corners = run_calibration_ui(src, sc["calibration_file"], out_w, out_h)
        if corners is None:
            print("[calib] 取消标定，退出")
            return
        if src.is_file:
            src.seek(args.start)
    rect = Rectifier(corners, out_w, out_h)

    det_cfg = cfg["detection"]
    detector = build_detector(monster, det_cfg)
    det_skip = max(1, int(det_cfg.get("idle_skip", 1)))   # 没怪时每 N 帧才跑一次模板匹配（主开销，21 ms/帧）；一有命中立刻恢复逐帧
    roi_provider = ROIProvider(cfg["roi"], out_w, out_h)
    mm_cfg = cfg.get("minimap") or {}
    minimap = MinimapTracker(mm_cfg, out_w, out_h) if mm_cfg.get("enabled", True) else None
    db = cfg["debounce"]
    debouncer = Debouncer(db["window_size"], db["enter_min_hits"], db["exit_min_misses"])

    log_dir = cfg["logging"]["dir"]
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, time.strftime("run_%Y%m%d_%H%M%S.jsonl"))
    log_f = open(log_path, "w", encoding="utf-8")
    every_n = cfg["logging"].get("console_every_n", 15)
    print(f"[app] source={source} 实际分辨率={src.width}x{src.height} fps={src.fps:.0f} monster={monster} "
          f"templates={len(detector.templates)} log={log_path}")
    # ---- 控制（P5/P6）----
    ctl_cfg = dict(cfg.get("control") or {})
    ctl_mode = args.control or ctl_cfg.get("mode", "off")
    # 地图 x 估计（worldpos.py）+ 本地图的地标
    wcfg = cfg["roi"].get("world") or {}
    pb = (cfg.get("patrol") or {}).get(monster) or {}
    patrol_mode = pb.get("mode", "screen")
    tracker = None
    if wcfg.get("enabled", True):
        tracker = WorldTracker(out_w, wcfg.get("lock_frames", 20), wcfg.get("move_px", 50), wcfg.get("scroll_px", 6),
                               cam_w=pb.get("cam_w"), deadband=wcfg.get("deadband", 1.0))
        for lm in pb.get("landmarks") or []:
            g = cv2.imread(lm["file"], cv2.IMREAD_GRAYSCALE)
            if g is None:
                print(f"[world] 地标文件不存在: {lm['file']}")
                continue
            tracker.add_landmark(lm["name"], g, lm["world_x"], lm["y"], wcfg.get("landmark_thr", 0.8))
        print(f"[world] 地图坐标估计已开，地标 {len(tracker.landmarks)} 个")
    # P7 巡逻端点按地图（=怪物模板集）存在 settings.yaml 的 patrol 段，这里取出当前这张的
    if patrol_mode == "minimap" and pb.get("left_mm") is not None and pb.get("right_mm") is not None and minimap is not None:
        ctl_cfg["patrol_left_x"], ctl_cfg["patrol_right_x"] = minimap.to_map_x(pb["left_mm"]), minimap.to_map_x(pb["right_mm"])
        print(f"[ctl] 位置巡逻端点({monster}, 小地图坐标): left_mm={pb['left_mm']} right_mm={pb['right_mm']}"
              f"（黄点在小地图缩略图里的画面 x，×{minimap.px_scale:g} 交给巡逻逻辑）")
    elif patrol_mode == "world" and pb.get("left_wx") is not None and pb.get("right_wx") is not None and tracker is not None:
        ctl_cfg["patrol_left_x"], ctl_cfg["patrol_right_x"] = pb["left_wx"], pb["right_wx"]
        print(f"[ctl] 位置巡逻端点({monster}, 地图坐标): left_wx={pb['left_wx']} right_wx={pb['right_wx']}"
              f"（先朝 {ctl_cfg.get('start_dir', 'right')} 走找地标）")
    elif pb.get("left_x") is not None and pb.get("right_x") is not None:
        patrol_mode = "screen"
        ctl_cfg["patrol_left_x"], ctl_cfg["patrol_right_x"] = pb["left_x"], pb["right_x"]
        print(f"[ctl] 位置巡逻端点({monster}, 屏幕坐标): left_x={pb['left_x']} right_x={pb['right_x']}")
    else:
        patrol_mode = "screen"
        print(f"[ctl] {monster} 还没标定巡逻端点 -> 巡逻退回「按时间掉头」（菜单「标定巡逻端点」可标）")
    decision = None
    if ctl_mode in ("dry", "pico"):
        actuator = None
        if ctl_mode == "pico":
            try:
                from pico_client import PicoClient
                actuator = PicoActuator(PicoClient(args.pico or ctl_cfg.get("pico_host")))
                print("[ctl] 已连接 Pico，真实发送按键。按 p 暂停/恢复")
            except Exception as e:
                print(f"[ctl] 连接 Pico 失败（{e}），退化为 dry-run")
        if actuator is None:
            actuator = DryRunActuator(log=lambda m: print(m) if src.is_file else None)
            print("[ctl] dry-run：只打日志不发按键。按 p 暂停/恢复")
        decision = Decision(ctl_cfg, actuator)
    if args.no_show:
        print("[app] 无窗口模式：Ctrl+C 停止（自动松开全部按键）" + (f"，或到 {args.seconds:.0f} s 自动停" if args.seconds else ""))
    else:
        print("[app] 按键: q 退出 | 空格 暂停 | m 切换怪物 | t 新增模板 | c 重新标定 | r 录制 | p 暂停/恢复控制")
    writer = None
    if args.record:
        os.makedirs("recordings", exist_ok=True)
        writer, rec_path = open_writer(os.path.join("recordings", time.strftime("rec_%Y%m%d_%H%M%S")),
                                       src.fps, (src.width, src.height))
        writer = AsyncWriter(writer)
        print(f"[rec] 开始录制 -> {rec_path}（与日志 {log_path} 同步：录像第 n 帧 = 日志 frame n）")

    show = not args.no_show
    last_state = None
    was_stuck = False
    frame_period = 1.0 / src.fps if args.realtime else 0
    t_start = time.perf_counter()
    try:
        while True:
            t_loop = time.perf_counter()
            if args.seconds and t_loop - t_start >= args.seconds:
                print(f"[app] 到时 {args.seconds:.0f}s，停止")
                break
            ok, frame = src.read()
            if not ok:
                print("[app] 视频源结束")
                break
            if writer is not None:
                writer.write(frame)
            t0 = time.perf_counter()
            game = rect(frame)
            rois = roi_provider.rois(game)
            bg_dx = roi_provider.measure_scroll(game)   # 背景滚动量（P9 卡住检测：人不动+背景不动 才算卡）
            diff = roi_provider.motion_diff(game)       # 本帧-上一帧（按 bg_dx 对齐）灰度差：中分候选的运动门槛
            mm = minimap.locate(game) if minimap is not None else None   # 小地图黄点（相对缩略图左上角）
            world_x = lm_fix = None
            if tracker is not None:
                px_t = roi_provider.last_player[0] if (roi_provider.last_player and roi_provider.lost_frames == 0) else None
                world_x = tracker.update(px_t, bg_dx)
                if tracker.landmarks and src.frame_index % max(1, wcfg.get("fix_every", 5)) == 0:
                    lm_fix = tracker.fix_with_landmarks(cv2.cvtColor(game, cv2.COLOR_BGR2GRAY))
                    if lm_fix is not None:
                        world_x = tracker.world_x
            best = {"score": -1.0, "loc": None, "template": None, "raw": False, "color_dist": None, "edge_score": None, "motion": None, "verified": False}
            best_roi = None
            # 空闲跳帧：去抖窗口里一次都没命中（附近没怪）时每 det_skip 帧检测一次；窗口里有命中就逐帧，首次发现最多晚 1 帧
            det_skipped = det_skip > 1 and not debouncer.state and not any(debouncer.window) and src.frame_index % det_skip != 0
            if not det_skipped:
                for name, (x1, y1, x2, y2) in rois:
                    r = detector.detect(game[y1:y2, x1:x2], diff=None if diff is None else diff[y1:y2, x1:x2])
                    if r["score"] > best["score"]:
                        best, best_roi = r, (name, (x1, y1, x2, y2))
                detected = debouncer.update(best["raw"])
            else:
                detected = debouncer.state
            # 怪到玩家的水平距离（矫正后像素）：用于判断先接近还是直接攻击
            dist = None
            side = best_roi[0] if best_roi else None
            if best_roi and best["loc"] and roi_provider.last_player:
                (x1, _, x2, _), (lx, _, lw, _) = best_roi[1], best["loc"]
                dist = abs((x1 + lx + lw / 2) - roi_provider.last_player[0])
                if side == "both":   # 两侧 ROI 合并过：按候选位置定它在哪一侧
                    side = "right" if (x1 + lx + lw / 2) >= roi_provider.last_player[0] else "left"
            ctl_state = None
            if decision is not None:
                px_now = roi_provider.last_player[0] if roi_provider.last_player else None
                player_ok = roi_provider.last_player is not None and roi_provider.lost_frames == 0
                if patrol_mode == "minimap":
                    # 小地图坐标模式：黄点在就继续巡逻——名牌被宠物/怪挡住时不必松键（攻击靠名牌 ROI，名牌丢了自然不会打）
                    x_for_patrol = minimap.to_map_x(mm[0]) if mm is not None else None
                    player_ok = player_ok or mm is not None
                elif patrol_mode == "world":
                    # 地图坐标模式：校准过才把位置交给巡逻逻辑；没校准前给 None -> 一直走去找地标
                    x_for_patrol = world_x if (tracker is not None and tracker.localized) else None
                else:
                    x_for_patrol = px_now
                ctl_state = decision.update(player_ok, detected, side, dist,
                                            player_x=x_for_patrol, bg_dx=bg_dx)
                if decision.stuck and not was_stuck:
                    print(f"[ctl] STUCK #{decision.stuck_count}: 按着 {decision.held} 但 x={px_now} 不动、背景不滚（第 {src.frame_index} 帧）")
                was_stuck = decision.stuck
                roi_provider.set_facing_hint(decision.facing)
            latency_ms = (time.perf_counter() - t0) * 1000

            rec = {"timestamp": round(time.time(), 3), "frame": src.frame_index, "monster": monster,
                   "detected": detected, "raw": best["raw"], "score": round(best["score"], 4),
                   "template": best["template"], "color_dist": best.get("color_dist"), "edge_score": best.get("edge_score"), "verified": best.get("verified"),
                   "roi": best_roi[1] if best_roi else None, "motion": best.get("motion"),
                   "side": side, "facing": roi_provider.current_facing,
                   "player": roi_provider.last_player, "player_score": round(roi_provider.player_score, 3),
                   "dist": None if dist is None else round(dist), "ctl": ctl_state,
                   "held": decision.held if decision else None,
                   "patrol_target": decision.patrol_target if decision else None,
                   "bg_dx": round(bg_dx, 1), "stuck": decision.stuck if decision else None,
                   "world_x": None if world_x is None else round(world_x), "cam_x": None if tracker is None else round(tracker.cam_x),
                   "lm_fix": lm_fix, "localized": tracker.localized if tracker else None, "mm": mm,
                   "cmds": (decision.act.frame_cmds[:] or None) if decision else None,
                   "latency_ms": round(latency_ms, 2)}
            if det_skipped:
                rec["det_skipped"] = True   # 本帧没跑模板匹配（空闲跳帧），score/raw 不代表画面里没怪
            log_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if decision is not None:
                decision.act.frame_cmds.clear()
            if detected != last_state or src.frame_index % every_n == 0:
                print(json.dumps(rec, ensure_ascii=False))
                last_state = detected

            if show:
                vis = game.copy()
                if roi_provider.last_player:
                    px, py = map(int, roi_provider.last_player)
                    cv2.drawMarker(vis, (px, py), (255, 0, 255), cv2.MARKER_CROSS, 20, 2)
                if minimap is not None:
                    minimap.draw(vis)
                for name, (x1, y1, x2, y2) in rois:
                    cv2.rectangle(vis, (x1, y1), (x2, y2), (255, 255, 0), 1)
                if decision is not None:
                    pl, pr, ptol = decision.patrol_bounds()
                    if pl is not None and patrol_mode == "world" and tracker is not None:
                        pl, pr = pl - tracker.cam_x, pr - tracker.cam_x     # 地图坐标 -> 当前屏幕位置
                    if pl is not None:
                        for ex, lbl in ((pl, "L"), (pr, "R")):
                            hot = decision.patrol_target == ("left" if lbl == "L" else "right")
                            cv2.line(vis, (int(ex), 0), (int(ex), out_h), (0, 165, 255) if hot else (120, 120, 120),
                                     2 if hot else 1)
                            cv2.putText(vis, lbl, (int(ex) + 4, out_h - 10), 0, 0.7,
                                        (0, 165, 255) if hot else (120, 120, 120), 2)
                if best_roi and best["loc"]:
                    (x1, y1, _, _), (lx, ly, lw, lh) = best_roi[1], best["loc"]
                    color = (0, 255, 0) if best["raw"] else (0, 0, 255)
                    cv2.rectangle(vis, (x1 + lx, y1 + ly), (x1 + lx + lw, y1 + ly + lh), color, 2)
                state_txt = "DETECTED" if detected else "none"
                side = f"[{best_roi[0]}]" if best_roi else ""
                cd = f" cd={best['color_dist']:.2f}" if best.get("color_dist") is not None else ""
                cd += f" eg={best['edge_score']:.2f}" if best.get("edge_score") is not None else ""
                ctl_txt = f" ctl={ctl_state}{'/' + decision.held if decision and decision.held else ''}" if decision else ""
                if decision is not None and decision.patrol_target:
                    ctl_txt += f"->{decision.patrol_target[0].upper()}"
                if tracker is not None:
                    ctl_txt += f" wx={'?' if world_x is None else int(world_x)}{'' if tracker.localized else '(未校准)'}"
                if decision is not None and decision.stuck:
                    ctl_txt += " STUCK"
                    cv2.putText(vis, "STUCK", (out_w // 2 - 60, 70), 0, 1.4, (0, 0, 255), 3)
                txt = (f"{state_txt}{side}{ctl_txt} score={best['score']:.2f}{cd} raw={int(best['raw'])} "
                       f"p={roi_provider.player_score:.2f} {latency_ms:.1f}ms fps={src.measured_fps:.1f} "
                       f"f={src.frame_index} face={roi_provider.current_facing} monster={monster}")
                cv2.rectangle(vis, (0, 0), (out_w, 28), (0, 0, 0), -1)
                cv2.putText(vis, txt, (8, 20), 0, 0.55, (0, 255, 0) if detected else (200, 200, 200), 2)
                if writer is not None:  # 录制指示：必须画在状态栏之后，否则被黑条盖住
                    cv2.circle(vis, (out_w - 60, 14), 8, (0, 0, 255), -1)
                    cv2.putText(vis, "REC", (out_w - 48, 20), 0, 0.55, (0, 0, 255), 2)
                cv2.imshow("game_vision", vis)
                k = cv2.waitKey(1) & 0xFF
                if k == ord("q"):
                    break
                elif k == ord(" "):
                    if decision is not None:
                        decision.release_all()
                    cv2.waitKey(0)
                elif k == ord("p") and decision is not None:
                    decision.set_paused(not decision.paused)
                    print("[ctl] 已暂停控制（按 p 恢复）" if decision.paused else "[ctl] 已恢复控制")
                elif k == ord("m"):
                    ms = list_monsters()
                    monster = ms[(ms.index(monster) + 1) % len(ms)] if monster in ms else ms[0]
                    detector = build_detector(monster, det_cfg)
                    debouncer.reset()
                    print(f"[app] 切换怪物 -> {monster} ({len(detector.templates)} 张模板)")
                elif k == ord("t"):
                    if crop_template_ui(game, monster):
                        detector = build_detector(monster, det_cfg)
                elif k == ord("r"):
                    if writer is None:
                        os.makedirs("recordings", exist_ok=True)
                        h, w = frame.shape[:2]
                        writer, rec_path = open_writer(os.path.join("recordings", time.strftime("rec_%Y%m%d_%H%M%S")),
                                                       src.fps, (w, h))
                        writer = AsyncWriter(writer)
                        print(f"[rec] 开始录制 -> {rec_path}")
                    else:
                        writer.release()
                        writer = None
                        print("[rec] 停止录制")
                elif k == ord("c"):
                    c = run_calibration_ui(src, sc["calibration_file"], out_w, out_h)
                    if c is not None:
                        rect = Rectifier(c, out_w, out_h)
                        roi_provider = ROIProvider(cfg["roi"], out_w, out_h)
                        debouncer.reset()
                        print("[calib] 已更新标定")
            if frame_period:
                rest = frame_period - (time.perf_counter() - t_loop)
                if rest > 0:
                    time.sleep(rest)
    except KeyboardInterrupt:
        print("\n[app] Ctrl+C，停止（松开全部按键）")   # 无窗口模式的正常退出方式
    finally:
        if decision is not None:
            decision.close()  # RELEASE_ALL
        if writer is not None:
            writer.release()
        log_f.close()
        src.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()

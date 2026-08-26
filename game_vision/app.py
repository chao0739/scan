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
import sys
import time

import cv2
import yaml

from camera import FrameSource, open_writer
from calibration import Rectifier, load_corners, run_calibration_ui
from debounce import Debouncer
from detector import TemplateDetector
from roi import ROIProvider

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
                            det_cfg.get("flip", True))


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
    ap.add_argument("--calib", default=None, help="标定文件路径（默认 config 里的 screen.calibration_file）")
    ap.add_argument("--no-show", action="store_true")
    ap.add_argument("--monster", default=None, help="怪物模板集名称（templates/ 下的目录名）")
    ap.add_argument("--start", type=int, default=0, help="视频起始帧")
    ap.add_argument("--realtime", action="store_true", help="视频回放按原速播放")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    source = args.source if args.source is not None else cfg["camera"]["source"]
    if isinstance(source, str) and not source.isdigit():
        source = os.path.abspath(source)
    os.chdir(HERE)  # 配置里的相对路径都相对于工程目录

    # ---- 人工初始化 1：选择怪物 ----
    mon_cfg = cfg.get("monster", {})
    monster = args.monster or mon_cfg.get("current", "")
    if args.monster is None and mon_cfg.get("ask_on_start", True):
        monster = choose_monster(monster)
    print(f"[app] 当前怪物模板集: {monster}")

    cam = cfg["camera"]
    src = FrameSource(str(source), cam["width"], cam["height"], cam.get("loop_video", False))
    if args.start and src.is_file:
        src.seek(args.start)

    # ---- 人工初始化 2：屏幕四角标定 ----
    sc = cfg["screen"]
    if args.calib:
        sc["calibration_file"] = os.path.abspath(args.calib) if os.path.isabs(args.calib) else args.calib
    out_w, out_h = sc["output_width"], sc["output_height"]
    print(f"[calib] 标定文件: {sc['calibration_file']}")
    corners = None if args.calibrate else load_corners(sc["calibration_file"])
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
    roi_provider = ROIProvider(cfg["roi"], out_w, out_h)
    db = cfg["debounce"]
    debouncer = Debouncer(db["window_size"], db["enter_min_hits"], db["exit_min_misses"])

    log_dir = cfg["logging"]["dir"]
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, time.strftime("run_%Y%m%d_%H%M%S.jsonl"))
    log_f = open(log_path, "w", encoding="utf-8")
    every_n = cfg["logging"].get("console_every_n", 15)
    print(f"[app] source={source} 实际分辨率={src.width}x{src.height} fps={src.fps:.0f} monster={monster} "
          f"templates={len(detector.templates)} log={log_path}")
    print("[app] 按键: q 退出 | 空格 暂停 | m 切换怪物 | t 新增模板 | c 重新标定 | r 录制")
    writer = None

    show = not args.no_show
    last_state = None
    frame_period = 1.0 / src.fps if args.realtime else 0
    try:
        while True:
            t_loop = time.perf_counter()
            ok, frame = src.read()
            if not ok:
                print("[app] 视频源结束")
                break
            if writer is not None:
                writer.write(frame)
            t0 = time.perf_counter()
            game = rect(frame)
            rois = roi_provider.rois(game)
            best = {"score": -1.0, "loc": None, "template": None, "raw": False}
            best_roi = None
            for name, (x1, y1, x2, y2) in rois:
                r = detector.detect(game[y1:y2, x1:x2])
                if r["score"] > best["score"]:
                    best, best_roi = r, (name, (x1, y1, x2, y2))
            detected = debouncer.update(best["raw"])
            latency_ms = (time.perf_counter() - t0) * 1000

            rec = {"timestamp": round(time.time(), 3), "frame": src.frame_index, "monster": monster,
                   "detected": detected, "raw": best["raw"], "score": round(best["score"], 4),
                   "template": best["template"], "roi": best_roi[1] if best_roi else None,
                   "side": best_roi[0] if best_roi else None, "facing": roi_provider.current_facing,
                   "player": roi_provider.last_player, "player_score": round(roi_provider.player_score, 3),
                   "latency_ms": round(latency_ms, 2)}
            log_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if detected != last_state or src.frame_index % every_n == 0:
                print(json.dumps(rec, ensure_ascii=False))
                last_state = detected

            if show:
                vis = game.copy()
                if roi_provider.last_player:
                    px, py = map(int, roi_provider.last_player)
                    cv2.drawMarker(vis, (px, py), (255, 0, 255), cv2.MARKER_CROSS, 20, 2)
                for name, (x1, y1, x2, y2) in rois:
                    cv2.rectangle(vis, (x1, y1), (x2, y2), (255, 255, 0), 1)
                if best_roi and best["loc"]:
                    (x1, y1, _, _), (lx, ly, lw, lh) = best_roi[1], best["loc"]
                    color = (0, 255, 0) if best["raw"] else (0, 0, 255)
                    cv2.rectangle(vis, (x1 + lx, y1 + ly), (x1 + lx + lw, y1 + ly + lh), color, 2)
                state_txt = "DETECTED" if detected else "none"
                side = f"[{best_roi[0]}]" if best_roi else ""
                txt = (f"{state_txt}{side} score={best['score']:.2f} raw={int(best['raw'])} "
                       f"p={roi_provider.player_score:.2f} {latency_ms:.1f}ms fps={src.measured_fps:.1f} "
                       f"f={src.frame_index} face={roi_provider.current_facing} monster={monster}")
                if writer is not None:
                    cv2.circle(vis, (out_w - 20, 14), 8, (0, 0, 255), -1)
                cv2.rectangle(vis, (0, 0), (out_w, 28), (0, 0, 0), -1)
                cv2.putText(vis, txt, (8, 20), 0, 0.55, (0, 255, 0) if detected else (200, 200, 200), 2)
                cv2.imshow("game_vision", vis)
                k = cv2.waitKey(1) & 0xFF
                if k == ord("q"):
                    break
                elif k == ord(" "):
                    cv2.waitKey(0)
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
    finally:
        if writer is not None:
            writer.release()
        log_f.close()
        src.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()

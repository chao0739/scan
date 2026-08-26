"""菜单式入口：python menu.py
数字选功能，不需要记命令。用户设置保存在 settings.yaml，下次自动沿用。
"""
import glob
import os
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

import cv2
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import app  # noqa: E402
from camera import FrameSource, open_writer  # noqa: E402
from calibration import Rectifier, load_corners, run_calibration_ui  # noqa: E402
import harvest_templates as hv  # noqa: E402

def open_file(path):
    """跨平台打开文件（图片等）。"""
    path = os.path.abspath(path)
    try:
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        print(f"(无法自动打开 {path}: {e}，请手动查看)")


PLAYER_DIR = os.path.join("templates", "players")
TEMPLATE_ROOT = "templates"


# ---------------- 通用 ----------------
def cfg():
    return app.load_config(os.path.join(HERE, "config.yaml"))


def settings():
    return app.load_settings()


def set_setting(path, value):
    """path 形如 'camera.source'"""
    st = settings()
    d = st
    keys = path.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value
    app.save_settings(st)


def ask(prompt, default=None):
    try:
        s = input(f"{prompt}{'' if default is None else f' [{default}]'}: ").strip()
    except EOFError:
        return default
    return s if s else default


def choose(title, items, allow_back=True, back_label="返回"):
    """items: list of (label, value)。返回 value；选返回时为 None。"""
    while True:
        print(f"\n=== {title} ===")
        for i, (label, _) in enumerate(items, 1):
            print(f"  {i}. {label}")
        if allow_back:
            print(f"  0. {back_label}")
        s = ask("请选择")
        if s is None or (allow_back and s == "0"):
            return None
        if s and s.isdigit() and 1 <= int(s) <= len(items):
            return items[int(s) - 1][1]
        print("无效输入")


def open_source():
    c = cfg()
    src = str(c["camera"]["source"])
    return FrameSource(src, c["camera"]["width"], c["camera"]["height"])


def rectifier():
    c = cfg()
    sc = c["screen"]
    corners = load_corners(sc["calibration_file"])
    if corners is None:
        print("还没有标定，请先执行「标定屏幕四角」")
        return None
    return Rectifier(corners, sc["output_width"], sc["output_height"])


def live_freeze_and_drag(title, hint, rect=None):
    """打开摄像头实时画面（矫正后），空格定格，拖框，s 保存。返回 (frame, (x0,y0,x1,y1)) 或 None。"""
    try:
        src = open_source()
    except Exception as e:
        print("打开摄像头失败:", e)
        return None
    if rect is None:
        rect = rectifier()
        if rect is None:
            src.release()
            return None
    win = title
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
    frozen = None
    result = None
    while True:
        if frozen is None:
            ok, f = src.read()
            if not ok:
                break
            frame = rect(f)
        else:
            frame = frozen
        vis = frame.copy()
        if box["p0"] and box["p1"]:
            cv2.rectangle(vis, box["p0"], box["p1"], (0, 255, 0), 1)
        msg = hint + ("  [已定格: 拖框后 s 保存, 空格 继续实时]" if frozen is not None else "  [空格 定格]") + "  q 取消"
        cv2.rectangle(vis, (0, 0), (vis.shape[1], 26), (0, 0, 0), -1)
        cv2.putText(vis, msg, (6, 18), 0, 0.55, (0, 255, 255), 1)
        cv2.imshow(win, vis)
        k = cv2.waitKey(20) & 0xFF
        if k == ord("q"):
            break
        if k == ord(" "):
            frozen = None if frozen is not None else frame.copy()
            box["p0"] = box["p1"] = None
        if k == ord("s") and frozen is not None and box["p0"] and box["p1"]:
            (x0, y0), (x1, y1) = box["p0"], box["p1"]
            x0, x1 = sorted((x0, x1))
            y0, y1 = sorted((y0, y1))
            if x1 - x0 > 4 and y1 - y0 > 4:
                result = (frozen, (x0, y0, x1, y1))
                break
    src.release()
    cv2.destroyWindow(win)
    return result


# ---------------- 功能 ----------------
def do_calibrate():
    c = cfg()
    sc = c["screen"]
    try:
        src = open_source()
    except Exception as e:
        print("打开摄像头失败:", e)
        return
    print("请依次点击游戏画面的 左上 → 右上 → 右下 → 左下，满意后按 s 保存")
    r = run_calibration_ui(src, sc["calibration_file"], sc["output_width"], sc["output_height"])
    src.release()
    print("标定已保存" if r is not None else "已取消")


def list_players():
    return [os.path.splitext(os.path.basename(p))[0] for p in sorted(glob.glob(os.path.join(PLAYER_DIR, "*.png")))]


def do_player_add():
    pid = ask("输入玩家 ID（游戏里显示的名字，仅用于命名档案）")
    if not pid:
        return
    print("接下来在摄像头画面里：等玩家名牌清晰可见时按 空格 定格，拖框框住【名字 + 下面的公会牌】，按 s 保存")
    res = live_freeze_and_drag("set player", f"player={pid}: drag the NAMEPLATE")
    if res is None:
        print("已取消")
        return
    frame, (x0, y0, x1, y1) = res
    os.makedirs(PLAYER_DIR, exist_ok=True)
    path = os.path.join(PLAYER_DIR, f"{pid}.png")
    cv2.imwrite(path, frame[y0:y1, x0:x1])
    # 脚底中心 ≈ 名牌上沿中点（名牌紧贴角色脚下）
    set_setting("roi.player.template", path.replace("\\", "/"))
    set_setting("roi.player.anchor_dx", (x1 - x0) // 2)
    set_setting("roi.player.anchor_dy", 12)
    set_setting("player.current", pid)
    print(f"已保存玩家 {pid} 的名牌 ({x1 - x0}x{y1 - y0}) -> {path}，并设为当前玩家")


def do_player_select():
    ps = list_players()
    if not ps:
        print("还没有玩家档案，请先「新增玩家」")
        return
    pid = choose("选择玩家", [(p + ("  (当前)" if p == settings().get("player", {}).get("current") else ""), p) for p in ps])
    if pid:
        path = os.path.join(PLAYER_DIR, f"{pid}.png")
        w = cv2.imread(path).shape[1]
        set_setting("roi.player.template", path.replace("\\", "/"))
        set_setting("roi.player.anchor_dx", w // 2)
        set_setting("roi.player.anchor_dy", 12)
        set_setting("player.current", pid)
        print(f"当前玩家 -> {pid}")


def menu_player():
    while True:
        cur = cfg().get("player", {}).get("current", "(未设置)")
        act = choose(f"玩家设置  当前: {cur}", [
            ("新增玩家（输入 ID + 框选名牌）", do_player_add),
            ("切换玩家", do_player_select),
            ("删除玩家档案", do_player_delete),
        ])
        if act is None:
            return
        act()


def do_player_delete():
    ps = list_players()
    pid = choose("删除哪个玩家档案", [(p, p) for p in ps]) if ps else None
    if pid and ask(f"确认删除 {pid}? (y/n)", "n").lower() == "y":
        os.remove(os.path.join(PLAYER_DIR, f"{pid}.png"))
        print("已删除")


def monsters():
    return app.list_monsters()


def monster_dir(m):
    return os.path.join(TEMPLATE_ROOT, m)


def do_monster_new():
    name = ask("新怪物名称（英文/拼音，如 pig_map02）")
    if not name:
        return
    d = monster_dir(name)
    os.makedirs(d, exist_ok=True)
    set_setting("monster.current", name)
    print(f"已创建 {d}，并设为当前怪物。接下来请「手动抠模板」采 2–3 张种子")


def do_monster_select():
    ms = monsters() + [d for d in os.listdir(TEMPLATE_ROOT)
                       if os.path.isdir(monster_dir(d)) and d != "players" and d not in monsters()]
    if not ms:
        print("还没有怪物，请先「新建怪物」")
        return
    cur = settings().get("monster", {}).get("current")
    m = choose("选择怪物", [(f"{x}  ({len(glob.glob(os.path.join(monster_dir(x), '*.png')))} 张模板)" + ("  (当前)" if x == cur else ""), x) for x in ms])
    if m:
        set_setting("monster.current", m)
        print(f"当前怪物 -> {m}")


def current_monster():
    m = cfg().get("monster", {}).get("current")
    if not m or not os.path.isdir(monster_dir(m)):
        print("当前怪物未设置或目录不存在，请先「新建/选择怪物」")
        return None
    return m


def do_monster_crop():
    m = current_monster()
    if not m:
        return
    n = 0
    while True:
        res = live_freeze_and_drag("crop template", f"monster={m}: drag the MONSTER")
        if res is None:
            break
        frame, (x0, y0, x1, y1) = res
        p = os.path.join(monster_dir(m), time.strftime("t_%Y%m%d_%H%M%S.png"))
        cv2.imwrite(p, frame[y0:y1, x0:x1])
        n += 1
        print(f"已保存 {p} ({x1 - x0}x{y1 - y0})")
        if ask("继续抠下一张? (y/n)", "y").lower() != "y":
            break
    print(f"本次共保存 {n} 张")


def do_monster_harvest():
    m = current_monster()
    if not m:
        return
    if not glob.glob(os.path.join(monster_dir(m), "*.png")):
        print("该怪物还没有种子模板，请先「手动抠模板」2–3 张")
        return
    c = cfg()
    secs = int(ask("录制多少秒（期间让角色在怪物附近活动）", 120))
    args = SimpleNamespace(source=str(c["camera"]["source"]), seconds=secs, seeds=os.path.abspath(monster_dir(m)),
                           name=m, step=20, thr=0.55, per_frame=4, band=None, tile=120, cols=10, calib=None)
    try:
        hv.scan(args)
    except SystemExit as e:
        print(e)
        return
    sheet = os.path.join("harvest", m, "sheet.jpg")
    if os.path.exists(sheet):
        print(f"正在打开候选缩略图 {sheet} ……请记下【是怪物】的编号")
        open_file(sheet)
    ids = ask("输入要保存的编号（如 0,3,5-9；留空跳过）", "")
    if ids:
        hv.pick(SimpleNamespace(name=m, ids=ids, out=os.path.abspath(monster_dir(m)), prefix="auto", source=None, calib=None))


def do_monster_pick_again():
    m = current_monster()
    if not m:
        return
    sheet = os.path.join("harvest", m, "sheet.jpg")
    if not os.path.exists(sheet):
        print("没有找到上次的候选，请先「半自动采集」")
        return
    open_file(sheet)
    ids = ask("输入要保存的编号（如 0,3,5-9）", "")
    if ids:
        hv.pick(SimpleNamespace(name=m, ids=ids, out=os.path.abspath(monster_dir(m)), prefix="auto", source=None, calib=None))


def do_monster_view():
    m = current_monster()
    if not m:
        return
    files = sorted(glob.glob(os.path.join(monster_dir(m), "*.png")))
    if not files:
        print("没有模板")
        return
    import numpy as np
    tiles = []
    for i, p in enumerate(files):
        im = cv2.imread(p)
        t = cv2.resize(im, (120, int(120 * im.shape[0] / im.shape[1])))
        t = cv2.copyMakeBorder(t, 0, max(0, 140 - t.shape[0]), 0, 0, cv2.BORDER_CONSTANT)[:140]
        cv2.putText(t, str(i + 1), (2, 14), 0, 0.5, (0, 255, 255), 2)
        tiles.append(t)
    while len(tiles) % 8:
        tiles.append(np.zeros_like(tiles[0]))
    sheet = np.vstack([np.hstack(tiles[k:k + 8]) for k in range(0, len(tiles), 8)])
    cv2.imshow(f"templates of {m} (press any key to close)", sheet)
    cv2.waitKey(0)
    cv2.destroyAllWindows()
    s = ask("要删除的编号（如 2,5；留空不删）", "")
    if s:
        for i in sorted(hv.parse_ids(s), reverse=True):
            if 1 <= i <= len(files):
                os.remove(files[i - 1])
                print("已删除", os.path.basename(files[i - 1]))


def do_monster_delete():
    ms = monsters()
    m = choose("删除哪个怪物（整个目录）", [(x, x) for x in ms]) if ms else None
    if m and ask(f"确认删除 {m} 及其全部模板? (y/n)", "n").lower() == "y":
        shutil.rmtree(monster_dir(m))
        print("已删除")


def menu_monster():
    while True:
        cur = cfg().get("monster", {}).get("current", "(未设置)")
        act = choose(f"怪物模板  当前: {cur}", [
            ("新建怪物", do_monster_new),
            ("选择怪物", do_monster_select),
            ("手动抠模板（摄像头定格拖框）", do_monster_crop),
            ("半自动采集（录制 → 扫描 → 挑选）", do_monster_harvest),
            ("重新挑选上次扫描的候选", do_monster_pick_again),
            ("查看 / 删除模板", do_monster_view),
            ("删除怪物", do_monster_delete),
        ])
        if act is None:
            return
        act()


def menu_settings():
    while True:
        c = cfg()
        act = choose("设置", [
            (f"摄像头编号  当前: {c['camera']['source']}", "cam"),
            (f"朝向模式    当前: {c['roi']['facing']}  (auto=按前进方向 / left / right / both)", "facing"),
            (f"匹配阈值    当前: {c['detection']['threshold']}", "thr"),
            (f"ROI 前方距离 当前: {c['roi']['near_offset']}~{c['roi']['far_offset']} px", "roi"),
        ])
        if act is None:
            return
        if act == "cam":
            v = ask("摄像头编号（0,1,2,3…）", c["camera"]["source"])
            set_setting("camera.source", int(v) if str(v).isdigit() else v)
        elif act == "facing":
            v = choose("朝向模式", [("auto（按前进方向）", "auto"), ("right", "right"), ("left", "left"), ("both（两侧）", "both")])
            if v:
                set_setting("roi.facing", v)
        elif act == "thr":
            v = ask("阈值 0~1，越高越严格", c["detection"]["threshold"])
            set_setting("detection.threshold", float(v))
        elif act == "roi":
            a = ask("近端(px)", c["roi"]["near_offset"])
            b = ask("远端(px)", c["roi"]["far_offset"])
            set_setting("roi.near_offset", int(a))
            set_setting("roi.far_offset", int(b))
        print("已保存")


def do_run():
    c = cfg()
    m = c.get("monster", {}).get("current")
    if not m or not glob.glob(os.path.join(monster_dir(m or ""), "*.png")):
        print("当前怪物没有模板，请先到「怪物模板」采集")
        return
    if load_corners(c["screen"]["calibration_file"]) is None:
        print("还没有标定，请先「标定屏幕四角」")
        return
    if not os.path.exists(c["roi"]["player"]["template"]):
        print("玩家名牌模板不存在，请先到「玩家设置」新增玩家")
        return
    print("窗口按键: q 退出 | 空格 暂停 | t 抠模板 | r 录制 | c 重新标定")
    app.main(["--source", str(c["camera"]["source"]), "--monster", m])


def do_review():
    logs = sorted(glob.glob("logs/*.jsonl"))
    recs = sorted(glob.glob("recordings/*.mp4") + glob.glob("recordings/*.avi"))
    if not logs or not recs:
        print("需要有日志和录像（运行检测时按 r 录制）")
        return
    log = choose("选择日志", [(os.path.basename(x), x) for x in logs[-10:]])
    rec = choose("选择对应录像", [(os.path.basename(x), x) for x in recs[-10:]]) if log else None
    if rec:
        out = os.path.abspath(os.path.join("harvest", "review.jpg"))
        os.makedirs("harvest", exist_ok=True)
        subprocess.run([sys.executable, "tools/review_log.py", "--source", rec, "--log", log, "--out", out])
        open_file(out)


def main():
    while True:
        c = cfg()
        status = (f"摄像头={c['camera']['source']}  玩家={c.get('player', {}).get('current', '未设置')}  "
                  f"怪物={c.get('monster', {}).get('current', '未设置')}  朝向={c['roi']['facing']}  "
                  f"标定={'已' if load_corners(c['screen']['calibration_file']) is not None else '未'}")
        act = choose("游戏目标检测  " + status, [
            ("开始检测", do_run),
            ("标定屏幕四角（首次 / 摄像头挪动后）", do_calibrate),
            ("玩家设置", menu_player),
            ("怪物模板", menu_monster),
            ("设置（摄像头 / 朝向 / 阈值）", menu_settings),
            ("复盘上次运行（日志叠加到录像）", do_review),
        ], back_label="退出")
        if act is None:
            print("再见")
            return
        try:
            act()
        except KeyboardInterrupt:
            print("\n已中断")
        except Exception as e:
            print("出错:", e)


if __name__ == "__main__":
    main()

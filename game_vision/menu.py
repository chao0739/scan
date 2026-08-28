"""菜单式入口：python menu.py
数字选功能，不需要记命令。用户设置保存在 settings.yaml，下次自动沿用。
"""
import glob
import json
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
from camera import FrameSource, open_writer, list_cameras, list_ndi_sources, is_ndi_source  # noqa: E402
from calibration import Rectifier, auto_corners, load_corners, run_calibration_ui, save_corners  # noqa: E402
from roi import ROIProvider, tighten_nameplate  # noqa: E402
from worldpos import WorldTracker, pick_landmark  # noqa: E402
from minimap import MinimapTracker  # noqa: E402
import harvest_templates as hv  # noqa: E402
import dedupe_templates as dd  # noqa: E402
import wz_sprites  # noqa: E402

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
    return FrameSource(src, c["camera"]["width"], c["camera"]["height"],
                       ndi_transport=c["camera"].get("ndi_transport", "tcp"))


def rectifier():
    c = cfg()
    sc = c["screen"]
    corners = load_corners(sc["calibration_file"])
    if corners is None:
        print("还没有标定，请先执行「标定屏幕四角」")
        return None
    return Rectifier(corners, sc["output_width"], sc["output_height"])


def live_freeze_and_drag(title, hint, rect=None, overlay=None):
    """打开摄像头实时画面（矫正后），空格定格 → 拖框 → s 保存。返回 (frame, (x0,y0,x1,y1)) 或 None。
    overlay(frame, vis)：可选，每次刷新时在 vis 上叠加额外标记（如玩家位置、当前范围）。"""
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
    state = {"frozen": None, "frame": None}

    def on_mouse(ev, x, y, flags, _):
        if state["frozen"] is None:
            return  # 实时状态下鼠标不画框（点击窗口只是取焦点）；先按空格定格
        if ev == cv2.EVENT_LBUTTONDOWN:
            box["p0"], box["p1"] = (x, y), (x, y)
        elif ev == cv2.EVENT_MOUSEMOVE and flags & cv2.EVENT_FLAG_LBUTTON:
            box["p1"] = (x, y)
        elif ev == cv2.EVENT_LBUTTONUP:
            box["p1"] = (x, y)

    def box_rect():
        if not (box["p0"] and box["p1"]):
            return None
        (x0, y0), (x1, y1) = box["p0"], box["p1"]
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        return (x0, y0, x1, y1) if (x1 - x0 > 4 and y1 - y0 > 4) else None

    cv2.namedWindow(win)
    cv2.setMouseCallback(win, on_mouse)
    result = None
    print("窗口操作：① 先点一下窗口取焦点 → ② 按 空格 定格 → ③ 按住左键拖框 → ④ 按 s 保存。再按空格恢复实时；q 取消")
    print("提示：按键前先点一下窗口让它获得焦点；若有中文输入法，先切到英文")
    while True:
        if state["frozen"] is None:
            ok, f = src.read()
            if not ok:
                break
            state["frame"] = rect(f)
        frame = state["frozen"] if state["frozen"] is not None else state["frame"]
        vis = frame.copy()
        if overlay is not None:
            overlay(frame, vis)
        r = box_rect()
        if box["p0"] and box["p1"]:
            cv2.rectangle(vis, box["p0"], box["p1"], (0, 255, 0) if r else (0, 0, 255), 1)
        # OpenCV 自带字体不支持中文，窗口内提示只能用英文（中文会显示成 ????）
        if state["frozen"] is None:
            status = "LIVE: press SPACE to freeze first"
        elif r:
            status = f"FROZEN box={r[2] - r[0]}x{r[3] - r[1]}  press s / Enter to SAVE   space=resume"
        else:
            status = "FROZEN: drag a box around the target, then press s   space=resume"
        cv2.rectangle(vis, (0, 0), (vis.shape[1], 26), (0, 0, 0), -1)
        cv2.putText(vis, f"{hint}  |  {status}  |  q=cancel", (6, 18), 0, 0.5, (0, 255, 255), 1)
        cv2.imshow(win, vis)
        k = cv2.waitKey(20) & 0xFF
        if k == ord("q") or k == 27:
            break
        elif k == ord(" "):
            state["frozen"] = None if state["frozen"] is not None else frame.copy()
            box["p0"] = box["p1"] = None
        elif k in (ord("s"), ord("S"), 13, 10):
            if state["frozen"] is None:
                print("[s] 还没有定格：先按 空格 定格，再拖框，再按 s")
            elif r is None:
                print("[s] 还没有画框（或框太小）：在定格画面上按住鼠标左键拖出一个框")
            else:
                result = (state["frozen"], r)
                break
        elif k != 255:
            print(f"[key] 收到按键码 {k}（不是 s/空格/q）")
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
    ndi = is_ndi_source(str(c["camera"]["source"]))
    how = choose("标定方式", [
        ("自动：整幅画面就是游戏画面（NDI / 采集卡 / OBS 虚拟摄像头），取非黑区域当四角" + ("（当前是 NDI，推荐）" if ndi else ""), "auto"),
        ("手动：摄像头拍显示器，点游戏画面四角", "manual"),
    ])
    if how is None:
        src.release()
        return
    if how == "auto":
        ok, frame = src.read()
        src.release()
        if not ok:
            print("读不到画面")
            return
        corners, size = auto_corners(frame)
        save_corners(sc["calibration_file"], corners, size)
        print(f"自动标定完成：画面 {size[0]}x{size[1]}，游戏区域四角 {corners.astype(int).tolist()}，已保存")
        return
    existing = load_corners(sc["calibration_file"])
    if existing is not None:
        print("已载入上次的四角作为起点：满意直接按 s 保存；不满意按 r 清空后重新点")
    print("请依次点击游戏画面的 左上 → 右上 → 右下 → 左下，满意后按 s 保存")
    r = run_calibration_ui(src, sc["calibration_file"], sc["output_width"], sc["output_height"], existing=existing)
    src.release()
    print("标定已保存" if r is not None else "已取消")


def list_players():
    return [os.path.splitext(os.path.basename(p))[0] for p in sorted(glob.glob(os.path.join(PLAYER_DIR, "*.png")))]


def do_player_add():
    pid = ask("输入玩家 ID（游戏里显示的名字，仅用于命名档案）")
    if not pid:
        return
    print("接下来在画面里：等玩家名牌清晰可见时按 空格 定格，拖框，按 s 保存")
    print("【框选要点】框住角色脚下的名字条即可（大一点没关系，程序会自动收紧到名字文字四周；勋章不要）。"
          "名牌是半透明的，框里背景越多、换个地方分数越低，所以最终模板只保留名字文字±2px")
    res = live_freeze_and_drag("set player", f"player={pid}: drag around the NAMEPLATE (auto-tightened to the name text)")
    if res is None:
        print("已取消")
        return
    frame, (x0, y0, x1, y1) = res
    crop = frame[y0:y1, x0:x1]
    tx0, ty0, tx1, ty1 = tighten_nameplate(crop)
    tight = crop[ty0:ty1, tx0:tx1]
    os.makedirs(PLAYER_DIR, exist_ok=True)
    path = os.path.join(PLAYER_DIR, f"{pid}.png")
    cv2.imwrite(path, tight)
    tw, th = tight.shape[1], tight.shape[0]
    # 模板左上角 -> 角色脚底中心：名字文字紧贴脚下（脚底 ≈ 文字上沿再往上几像素），水平取中
    set_setting("roi.player.template", path.replace("\\", "/"))
    set_setting("roi.player.anchor_dx", tw // 2)
    set_setting("roi.player.anchor_dy", 2)
    set_setting("player.current", pid)
    print(f"已保存玩家 {pid} 的名牌 -> {path}：你框的 {x1 - x0}x{y1 - y0}，自动收紧到名字文字 {tw}x{th}，并设为当前玩家")
    if (tx0, ty0, tx1, ty1) == (0, 0, crop.shape[1], crop.shape[0]):
        print("  [!] 框里没找到像名字那样的一行白字（可能框错位置或名牌被挡），这次按原框保存了，建议重做")
    elif tw * th > 60 * 20:
        print("  提示：收紧后仍比较大（名字通常约 30x12 px），如果运行时 player_score 低于 0.8，重做一次")


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
        set_setting("roi.player.anchor_dy", 2)
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


def do_monster_dedupe():
    m = current_monster()
    if not m:
        return
    d = monster_dir(m)
    n_dup = len(glob.glob(os.path.join(d, dd.DUP_DIR, "*.png")))
    if n_dup:
        print(f"（{dd.DUP_DIR}/ 里已有 {n_dup} 张之前去掉的模板，可选择移回）")
        if ask("移回之前去掉的模板? (y/n)", "n").lower() == "y":
            print(f"已移回 {dd.restore(d)} 张")
            return
    thr = float(ask("相似度阈值（0.75 推荐；越高保留越多）", 0.75))
    keep, drop = dd.plan(d, thr)
    print(f"当前 {len(keep) + len(drop)} 张 -> 保留 {len(keep)} 张，可去掉 {len(drop)} 张（移到 {dd.DUP_DIR}/，随时可移回）")
    if drop and ask("执行? (y/n)", "y").lower() == "y":
        dd.dedupe(d, thr)
        print("完成。模板越少检测越快：耗时 ≈ ROI 面积 × 模板数 × 2(翻转)")


def do_monster_delete():
    ms = monsters()
    m = choose("删除哪个怪物（整个目录）", [(x, x) for x in ms]) if ms else None
    if m and ask(f"确认删除 {m} 及其全部模板? (y/n)", "n").lower() == "y":
        shutil.rmtree(monster_dir(m))
        print("已删除")


def do_monster_wz():
    """从游戏客户端原版精灵图（aa/ 目录的 Unity 包）导入带透明通道的模板到当前怪物集。"""
    m = current_monster()
    if not m:
        return
    c = cfg()
    aa = (c.get("wz") or {}).get("aa_dir") or ""
    print(f"客户端资源目录 aa_dir: {aa or '(未设置，config.yaml 的 wz.aa_dir)'}")
    print("怪物 ID 是 7 位数字（冒险岛 Mob ID），已知：野猪 2230102、斧木妖(带斧头树桩) 1130100、树桩 0130100、黑斧木妖 1140100、绿蘑菇 1110100、蘑菇 2230101")
    print("不知道 ID 可以先用命令行看图集：python tools/wz_sprites.py atlas --mob <id> --out /tmp/x")
    ids = ask("要导入的怪物 ID（多个用逗号分隔）", "")
    ids = [x.strip() for x in str(ids).replace("，", ",").split(",") if x.strip()]
    if not ids:
        return
    import wz_sprites
    argv = ["extract", "--out", os.path.abspath(monster_dir(m)), "--dedupe", "0.9"]
    for x in ids:
        argv += ["--mob", x]
    if aa:
        argv += ["--aa", aa]
    try:
        wz_sprites.main(argv)
    except SystemExit as e:
        print(e)
        return
    except Exception as e:
        print("导入失败:", e)
        return
    n_wz = len(glob.glob(os.path.join(monster_dir(m), "wz_*.png")))
    n_manual = len(glob.glob(os.path.join(monster_dir(m), "*.png"))) - n_wz
    print(f"当前集 {m}: 精灵图模板 {n_wz} 张，手抠模板 {n_manual} 张。精灵缩放比 detection.sprite_scale={c['detection'].get('sprite_scale')}"
          "（换了游戏窗口大小要重标：python tools/wz_sprites.py scale --mob <id> --source <录像>）")
    if n_manual:
        print("提示：手抠模板可以移到 _manual/ 子目录停用（省一半耗时；被宠物挡住/技能特效盖住的怪只有手抠+运动门槛能认，需要时再放回来）")


WZ_LIB = os.path.join(HERE, "templates", "_wz")


def wz_catalog():
    """精灵库目录 templates/_wz/（tools/wz_sprites.py extract-all 生成）：{id: {frames, sizes, name}}；没有则 None。"""
    p = os.path.join(WZ_LIB, "catalog.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        cat = json.load(f)
    np_ = os.path.join(WZ_LIB, "names.json")
    if os.path.exists(np_):
        with open(np_, encoding="utf-8") as f:
            names = json.load(f)
        for k, v in cat.items():
            if not v.get("name"):
                v["name"] = names.get(k, "")
    return cat


def do_monster_search_add():
    """搜索精灵库（名字/ID）把怪加进当前模板集。"""
    m = current_monster()
    if not m:
        return
    cat = wz_catalog()
    if cat is None:
        print(f"还没有精灵库 {WZ_LIB}（运行 python tools/wz_sprites.py extract-all 生成，约 1 分钟，需要客户端 aa 目录）")
        return
    while True:
        q = ask("搜索怪物（名字或 ID 的一部分，如 野猪 / 2230；留空返回）", "")
        if not q:
            return
        q = str(q).strip()
        hits = [(k, v) for k, v in sorted(cat.items()) if v.get("frames") and (q in k or q in (v.get("name") or ""))]
        if not hits:
            print("没找到；可以打开缩略图找 ID：templates/_wz/sheet_XX.jpg（按 ID 排序，每页 100 只）")
            continue
        items = [(f"{k}  {v.get('name') or '(无名字)'}  {v['frames']} 帧 {v['sizes'][0][0]}x{v['sizes'][0][1]}", k) for k, v in hits[:30]]
        if len(hits) > 30:
            print(f"匹配 {len(hits)} 只，只列前 30，请输入更具体的关键字")
        print("可一次选多个：输入编号用逗号分隔（如 1,3）")
        print(f"\n=== 搜索「{q}」 ===")
        for i, (label, _) in enumerate(items, 1):
            print(f"  {i}. {label}")
        sel = ask("选择编号（多个用逗号；留空重搜）", "")
        if not sel:
            continue
        chosen = []
        for tok in str(sel).replace("，", ",").split(","):
            tok = tok.strip()
            if tok.isdigit() and 1 <= int(tok) <= len(items):
                chosen.append(items[int(tok) - 1][1])
        for mob in chosen:
            src = glob.glob(os.path.join(WZ_LIB, mob, "wz_*.png"))
            for f in src:
                shutil.copy2(f, monster_dir(m))
            print(f"已加入 {mob} {cat[mob].get('name', '')}：{len(src)} 帧 -> {monster_dir(m)}")
        n_wz = len(glob.glob(os.path.join(monster_dir(m), "wz_*.png")))
        print(f"当前集 {m} 共 {n_wz} 张精灵图模板（每张 ≈1 ms/帧；超过 ~30 张会掉帧，可用「查看模板」核对后删掉多余动作帧）")
        if ask("继续搜索添加？(y/n)", "n").lower() != "y":
            return


def do_monster_browse_lib():
    """打开精灵库缩略图（每页 100 只，按 ID 排）。"""
    pages = sorted(glob.glob(os.path.join(WZ_LIB, "sheet_*.jpg")))
    if not pages:
        print("还没有精灵库缩略图（python tools/wz_sprites.py extract-all）")
        return
    print(f"共 {len(pages)} 页；正在打开第 1 页，其余在 {WZ_LIB}/sheet_XX.jpg")
    open_file(pages[0])


def menu_monster():
    while True:
        cur = cfg().get("monster", {}).get("current", "(未设置)")
        act = choose(f"怪物模板  当前: {cur}", [
            ("新建怪物", do_monster_new),
            ("选择怪物", do_monster_select),
            ("手动抠模板（摄像头定格拖框）", do_monster_crop),
            ("搜索精灵库添加怪物（按名字/ID，推荐）", do_monster_search_add),
            ("浏览精灵库缩略图（找不到名字时按图找 ID）", do_monster_browse_lib),
            ("从游戏原版精灵图导入（带透明通道，按 ID 直接从客户端包导；需要客户端 aa 目录）", do_monster_wz),
            ("半自动采集（录制 → 扫描 → 挑选）", do_monster_harvest),
            ("重新挑选上次扫描的候选", do_monster_pick_again),
            ("查看 / 删除模板", do_monster_view),
            ("模板去重（相似的移到 _dup/，加快检测）", do_monster_dedupe),
            ("删除怪物", do_monster_delete),
        ])
        if act is None:
            return
        act()


def roi_box_to_offsets(box, player):
    """拖出的框 (x0,y0,x1,y1) + 玩家脚底 (px,py) -> (near, far, up, down)。框中心在玩家左边就按左侧镜像换算（运行时左右对称）。"""
    x0, y0, x1, y1 = box
    px, py = player
    if (x0 + x1) / 2 >= px:
        near, far = x0 - px, x1 - px
    else:
        near, far = px - x1, px - x0
    return int(round(near)), int(round(far)), int(round(py - y0)), int(round(y1 - py))


def do_roi_drag():
    """在画面上拖框设置检测范围（怪物搜索 ROI）：框按玩家脚底换算成 near/far/up/down 存进 settings。"""
    c = cfg()
    try:
        rp = ROIProvider(c["roi"], c["screen"]["output_width"], c["screen"]["output_height"])
    except Exception as e:
        print("玩家名牌模板不可用（先到「玩家设置」新增玩家）:", e)
        return
    r = c["roi"]

    def overlay(frame, vis):
        p = rp.locate_player(frame)
        if p is None or rp.lost_frames:
            cv2.putText(vis, "player nameplate NOT found in this frame", (6, 46), 0, 0.6, (0, 0, 255), 2)
            return
        px, py = map(int, p)
        cv2.drawMarker(vis, (px, py), (255, 0, 255), cv2.MARKER_CROSS, 24, 2)          # 紫色十字 = 玩家脚底（换算基准）
        for sgn in (1, -1):                                                              # 青色 = 当前范围（右侧 + 左侧镜像）
            xa, xb = sorted((px + sgn * r["near_offset"], px + sgn * r["far_offset"]))
            cv2.rectangle(vis, (xa, py - r["up"]), (xb, py + r["down"]), (255, 255, 0), 1)

    print("在画面里：等角色和名牌清晰可见时按 空格 定格 → 在角色【面朝的一侧】拖出检测框 → 按 s 保存。")
    print("紫色十字 = 玩家脚底（框会换算成相对它的 前/后/上/下 距离，运行时左右自动镜像）；青色框 = 当前范围。")
    print(f"当前: 前方 {r['near_offset']}~{r['far_offset']} px，上 {r['up']} / 下 {r['down']} px。框越大越费时（模板匹配耗时 ∝ 面积，300x90 ≈ 20 ms/帧）")
    res = live_freeze_and_drag("set detection range", "drag the DETECTION BOX on the side the player faces (magenta=feet, cyan=current)", overlay=overlay)
    if res is None:
        print("已取消")
        return
    frame, box = res
    p = rp.locate_player(frame)
    if p is None or rp.lost_frames:
        print("定格画面上没定位到玩家名牌，无法换算；请在名牌清晰可见时重新定格")
        return
    near, far, up, down = roi_box_to_offsets(box, p)
    if far - near < 20 or up + down < 10:
        print(f"框太小（宽 {far - near}、高 {up + down}），没有保存")
        return
    for k, v in (("near_offset", near), ("far_offset", far), ("up", up), ("down", down)):
        set_setting(f"roi.{k}", v)
    side = "右" if (box[0] + box[2]) / 2 >= p[0] else "左"
    print(f"已保存检测范围（按{side}侧的框换算）：前方 {near}~{far} px，上 {up} / 下 {down} px，ROI {far - near}x{up + down}"
          f"（原 300x90 ≈ 20 ms/帧，估计 {(far - near) * (up + down) / 27000 * 20:.0f} ms/帧）"
          + ("；near 为负 = 从角色身后开始" if near < 0 else ""))


def menu_settings():
    while True:
        c = cfg()
        act = choose("设置", [
            (f"摄像头      当前: {c['camera']['source']}", "cam"),
            (f"朝向模式    当前: {c['roi']['facing']}  (key=按方向键,推荐 / auto / left / right / both)", "facing"),
            (f"匹配阈值    当前: 候选下限 {c['detection']['threshold']}，直接接受 {c['detection'].get('sure_score', 0)}（之间的要求框里在动）", "thr"),
            (f"检测范围(ROI) 当前: 前方 {c['roi']['near_offset']}~{c['roi']['far_offset']} px, 上 {c['roi']['up']} / 下 {c['roi']['down']} px  (画面上拖框 / 输入数字)", "roi"),
            (f"控制模式    当前: {c.get('control', {}).get('mode', 'off')}  (off=只检测 / dry=只打日志 / pico=真按键)", "ctl"),
            (f"攻击键/拾取键/间隔 当前: {c.get('control', {}).get('attack_key')} / {c.get('control', {}).get('pickup_key')} / {c.get('control', {}).get('attack_interval_ms')} ms", "atk"),
            (f"卡住后跳跃恢复 当前: {'开' if c.get('control', {}).get('jump_on_stuck') else '关'}  (卡住 1.5s -> 按着方向键跳; 连跳 3 次无效 -> 掉头)", "jump"),
            (f"拟人随机动作 当前: 随机跳 {c.get('control', {}).get('human_jump_per_min')}/min, 端点偏移 ±{c.get('control', {}).get('human_endpoint_px')}px, "
             f"端点停顿 0~{c.get('control', {}).get('human_pause_ms')}ms  (0=关)", "human"),
            (f"运行方式    当前: {'开窗口' if (c.get('app') or {}).get('show', True) else '不开窗口(省 CPU, Ctrl+C 停)'}，"
             f"时长 {(c.get('app') or {}).get('run_seconds') or 0} s (0=不限)", "runmode"),
        ])
        if act is None:
            return
        if act == "cam":
            cams = list_cameras()
            print("检测到的摄像头：" + (", ".join(f"{i}={n}" for i, n in cams) if cams else "无"))
            try:
                ndis = list_ndi_sources(timeout=3.0, transport=c["camera"].get("ndi_transport", "tcp"))
                print("局域网 NDI 源：" + (", ".join(f"ndi:{n}" for n in ndis) if ndis else "无（B 机 OBS 要开 NDI 输出）"))
            except Exception as e:
                print(f"NDI 不可用（{e}）")
            v = ask("摄像头编号 / 名称关键字（如 Insta360）/ NDI 写 ndi:源名关键字（如 ndi:Game-PC）", c["camera"]["source"])
            set_setting("camera.source", int(v) if str(v).isdigit() else v)
            if is_ndi_source(str(v)):
                print("提示：NDI 换源后请执行「标定屏幕四角 → 自动」，模板也可能需要在新画面上重抠")
        elif act == "facing":
            v = choose("朝向模式", [("key（用决策按住的方向键，只检测前进方向，推荐）", "key"), ("auto（按位移估计前进方向）", "auto"),
                                 ("right", "right"), ("left", "left"), ("both（两侧，耗时翻倍）", "both")])
            if v:
                set_setting("roi.facing", v)
        elif act == "thr":
            v = ask("候选下限 threshold（0~1；开着颜色校验+运动门槛时 0.5 合适）", c["detection"]["threshold"])
            set_setting("detection.threshold", float(v))
            v2 = ask("直接接受分 sure_score（≥此分不看运动；threshold~此分之间要求候选框里在动；0=关运动门槛）", c["detection"].get("sure_score", 0.65))
            set_setting("detection.sure_score", float(v2))
        elif act == "ctl":
            v = choose("控制模式", [("off（只检测）", "off"), ("dry（只打日志，不发按键）", "dry"), ("pico（真发按键给 Pico）", "pico")])
            if v:
                set_setting("control.mode", v)
        elif act == "atk":
            k = ask("攻击键（Pico 键名，如 ctrl / shift / a）", c.get("control", {}).get("attack_key", "ctrl"))
            pk = ask("拾取键（每 0.5~1 s 随机点按一次；输入 none 关闭）", c.get("control", {}).get("pickup_key", "z"))
            ms = ask("攻击间隔 ms", c.get("control", {}).get("attack_interval_ms", 700))
            set_setting("control.attack_key", str(k))
            set_setting("control.pickup_key", None if str(pk).lower() in ("none", "no", "") else str(pk))
            set_setting("control.attack_interval_ms", int(ms))
        elif act == "jump":
            v = choose("卡住后跳跃恢复", [("开", True), ("关（只在日志/画面里报 STUCK）", False)])
            if v is not None:
                set_setting("control.jump_on_stuck", v)
                k = ask("跳跃键（Pico 键名）", c.get("control", {}).get("jump_key", "alt"))
                set_setting("control.jump_key", str(k))
        elif act == "runmode":
            v = choose("运行时显示画面窗口？", [("不开窗口（省 CPU，掉帧时用；Ctrl+C 或到时自动停）", False), ("开窗口（能看 ROI/分数，q 退出）", True)])
            if v is not None:
                set_setting("app.show", v)
            secs = ask("运行多少秒后自动停止（0=不限）", (c.get("app") or {}).get("run_seconds") or 0)
            set_setting("app.run_seconds", float(secs))
        elif act == "human":
            cc = c.get("control", {})
            v = ask("巡逻中平均每分钟随机跳几次（0=不跳）", cc.get("human_jump_per_min", 4))
            set_setting("control.human_jump_per_min", float(v))
            v = ask("每次掉头时端点随机多走/少走的最大像素（0=关；小地图模式下 60≈6 个小地图像素）", cc.get("human_endpoint_px", 60))
            set_setting("control.human_endpoint_px", float(v))
            v = ask("到端点后随机停顿的最长毫秒（0=关）", cc.get("human_pause_ms", 800))
            set_setting("control.human_pause_ms", float(v))
        elif act == "roi":
            how = choose("怎么设检测范围", [("在画面上拖框（推荐：定格 → 在角色面朝一侧拖框 → s）", "drag"), ("输入数字（相对角色脚底的 前/后/上/下 距离）", "num")])
            if how == "drag":
                do_roi_drag()
                continue
            if how != "num":
                continue
            a = ask("近端(px)，负数=从角色身后开始", c["roi"]["near_offset"])
            b = ask("远端(px)", c["roi"]["far_offset"])
            u = ask("向上(px)，只盖同一层即可（怪高约 40~60）", c["roi"]["up"])
            d = ask("向下(px)", c["roi"]["down"])
            set_setting("roi.near_offset", int(a))
            set_setting("roi.far_offset", int(b))
            set_setting("roi.up", int(u))
            set_setting("roi.down", int(d))
        print("已保存")


def patrol_bounds_of(monster):
    """这张地图（=怪物模板集）已标定的巡逻端点 dict(left_x, right_x)，没有则 {}。"""
    return (settings().get("patrol") or {}).get(monster) or {}


def do_patrol_bounds():
    """P7：现场标定这张地图的巡逻两端点（= 你想让角色掉头的位置，不必是地图边界）。
    你手动把角色走到左掉头点按 l、右掉头点按 r（记下当时的玩家屏幕 x），按 s 保存。

    屏幕 x 只有在「镜头被地图边界顶住」时才等于地图位置；镜头跟着人走时屏幕 x 恒在中间、记了也没用。
    所以窗口里会实时显示 CAM: LOCKED / FOLLOWING（用背景相位相关判断背景有没有在滚），
    只有 LOCKED 时记的点才可靠。"""
    c = cfg()
    m = c.get("monster", {}).get("current")
    if not m:
        print("请先在「怪物模板」里选一个怪物（端点按地图/怪物分别保存）")
        return
    if not os.path.exists(c["roi"]["player"]["template"]):
        print("玩家名牌模板不存在，请先到「玩家设置」新增玩家（端点要靠玩家定位才能记）")
        return
    rect = rectifier()
    if rect is None:
        return
    try:
        src = open_source()
    except Exception as e:
        print("打开摄像头失败:", e)
        return
    out_w, out_h = c["screen"]["output_width"], c["screen"]["output_height"]
    rp = ROIProvider(c["roi"], out_w, out_h)
    mmt = MinimapTracker(c.get("minimap") or {}, out_w, out_h) if (c.get("minimap") or {}).get("enabled", True) else None
    b = dict(patrol_bounds_of(m))
    b.pop("landmarks", None)
    wcfg = c["roi"].get("world") or {}
    tracker = WorldTracker(out_w, wcfg.get("lock_frames", 20), wcfg.get("move_px", 50), wcfg.get("scroll_px", 6),
                           deadband=wcfg.get("deadband", 1.0))
    tracker.zeroed = tracker.localized = True      # 标定时以打开窗口那一刻的镜头位置为原点；两端点相对一致即可
    hw, up, dn = wcfg.get("landmark_box", [150, 250, 100])
    lms = {}                                       # side -> dict(gray, world_x, y, tex)
    print(f"标定巡逻端点（地图: {m}）")
    print("  用你自己的键盘把角色走到你想让它**向右掉头**的位置，按 r 记录；走到**向左掉头**的位置，按 l 记录；按 s 保存")
    print("  窗口顶部 CAM 状态：LOCKED=镜头被地图边界顶住，这里的屏幕 x 可靠，可以记；")
    print("                    FOLLOWING=镜头正跟着人走，屏幕 x 不代表地图位置，这里记的点无效（先来回走两步让它判断）")
    print("  小地图坐标（推荐）：窗口里 mm=黄点在小地图里的 x，任何位置都可靠，不看 CAM 状态；保存时选「小地图坐标」")
    print("  （左右记反了也没关系，保存时会自动排序）q 取消")
    band = (350, 650)                      # 背景滚动估计用的画面带（平台/树这一层，避开 HUD 和视差天空）
    prev_small = None
    hist = []                              # 最近 N 帧的 (玩家Δx, 背景dx)
    prev_px = None
    win = f"patrol bounds - {m}"
    cv2.namedWindow(win)
    saved = False
    while True:
        ok, f = src.read()
        if not ok:
            break
        game = rect(f)
        p = rp.locate_player(game)
        px = None if p is None else int(p[0])
        mm = mmt.locate(game) if mmt is not None else None
        gray = cv2.cvtColor(game, cv2.COLOR_BGR2GRAY)
        # 镜头是否被顶住：玩家在动而背景不动 -> LOCKED；背景在滚 -> FOLLOWING
        small = cv2.resize(cv2.cvtColor(game[band[0]:band[1]], cv2.COLOR_BGR2GRAY), None, fx=0.25, fy=0.25,
                           interpolation=cv2.INTER_AREA).astype("float32")
        bg_dx = 0.0
        if prev_small is not None:
            (dx, _dy), resp = cv2.phaseCorrelate(prev_small, small)
            bg_dx = dx * 4 if resp > 0.05 else 0.0
        prev_small = small
        wx = tracker.update(px if (p is not None and rp.lost_frames == 0) else None, bg_dx)
        hist.append((0 if (px is None or prev_px is None) else px - prev_px, bg_dx))
        hist = hist[-12:]
        prev_px = px
        moved = sum(abs(a) for a, _ in hist)
        scrolled = sum(abs(d) for _, d in hist)
        if scrolled > 6:
            cam = "FOLLOWING (x unreliable here!)"
        elif moved > 15:
            cam = "LOCKED (ok)"
        else:
            cam = "? walk a bit to test"
        vis = game.copy()
        if p is not None:
            cv2.drawMarker(vis, (int(p[0]), int(p[1])), (255, 0, 255), cv2.MARKER_CROSS, 20, 2)
        for key, color in (("left_x", (0, 165, 255)), ("right_x", (0, 255, 0))):
            if b.get(key) is not None:
                x = int(b[key])
                cv2.line(vis, (x, 0), (x, out_h), color, 2)
                cv2.putText(vis, key[0].upper(), (x + 4, out_h - 10), 0, 0.7, color, 2)
        # OpenCV 自带字体不支持中文，窗口内提示只能用英文
        txt = (f"x={px if px is not None else '--'} wx={'--' if wx is None else int(wx)} mm={mm[0] if mm else '--'} s={rp.player_score:.2f} | CAM: {cam} | "
               f"L={b.get('left_x')}/{b.get('left_wx')}/mm{b.get('left_mm')} R={b.get('right_x')}/{b.get('right_wx')}/mm{b.get('right_mm')} | l/r=set  s=save  q=cancel")
        if mmt is not None:
            mmt.draw(vis)
        if p is not None:
            x0, y0 = max(0, int(p[0]) - hw), max(0, int(p[1]) - up)
            cv2.rectangle(vis, (x0, y0), (min(out_w, int(p[0]) + hw), max(0, int(p[1]) - dn)), (255, 0, 255), 1)  # 地标框
        cv2.rectangle(vis, (0, 0), (out_w, 26), (0, 0, 0), -1)
        cv2.putText(vis, txt, (6, 18), 0, 0.5, (0, 0, 255) if cam.startswith("FOLLOWING") else (0, 255, 255), 1)
        cv2.imshow(win, vis)
        k = cv2.waitKey(20) & 0xFF
        if k in (ord("q"), 27):
            break
        elif k in (ord("l"), ord("r")):
            if px is None and mm is None:
                print("[!] 这一帧没定位到玩家（名牌被挡/爬梯时会这样），也没找到小地图黄点，走两步再按")
            else:
                side = "left" if k == ord("l") else "right"
                b[f"{side}_x"] = px
                b[f"{side}_wx"] = None if wx is None else int(wx)
                b[f"{side}_mm"] = None if mm is None else int(mm[0])
                print(f"记录 {'左' if side == 'left' else '右'}端点 小地图x={b[f'{side}_mm']}  屏幕x={px}")
                if px is None:
                    print("    这一帧名牌没定位到：只记了小地图坐标（屏幕/地图坐标模式用不了这个端点）")
                    lms.pop(side, None)
                    continue
                # 抠地标：玩家头顶上方的背景里挑最独特的一块（避开人物、地面怪、HUD；避开会错配的重复瓦片）
                lm = pick_landmark(gray, px, int(p[1]), half_w=hw, boxes=((up, dn), (up + 100, dn + 100), (up + 200, dn + 200), (up + 50, dn)))
                if lm is None:
                    print("    [!] 头顶上方找不到有纹理的背景（天空/纯色），地图坐标模式在这个端点校不准")
                    lms.pop(side, None)
                else:
                    lms[side] = dict(gray=lm["gray"], world_x=(lm["x0"] + tracker.cam_x), y=lm["y0"], tex=lm["tex"])
                    print(f"记录 {'左' if side == 'left' else '右'}端点 屏幕x={px} 地图x={b[f'{side}_wx']}  CAM: {cam}  "
                          f"地标 纹理={lm['tex']:.0f} 次高峰={lm['second']:.2f}{'（偏高，附近有相似花纹，运行时可能错配）' if lm['second'] > 0.8 else ''}")
                if cam.startswith("FOLLOWING"):
                    print("    [!] 镜头正在跟随：屏幕坐标模式下这个端点无效；地图坐标模式(靠地标)可以用")

        elif k in (ord("s"), 13, 10):
            has_screen = b.get("left_x") is not None and b.get("right_x") is not None
            has_mm = b.get("left_mm") is not None and b.get("right_mm") is not None
            if not has_screen and not has_mm:
                print("[s] 还差一个端点：左端按 l、右端按 r")
            elif has_mm and abs(b["left_mm"] - b["right_mm"]) < 8:
                print(f"[s] 两个端点在小地图上只差 {abs(b['left_mm'] - b['right_mm'])} px，太近了，是不是记到同一个地方了？")
            elif has_screen and not has_mm and abs(b["left_x"] - b["right_x"]) < 100:
                print(f"[s] 两个端点只差 {abs(b['left_x'] - b['right_x'])} px，太近了，是不是记到同一个地方了？")
            else:
                key = "left_mm" if has_mm else "left_x"
                if b[key] > b[key.replace("left", "right")]:      # 左右记反了：连同地图坐标和地标一起换
                    for k_ in ("x", "wx", "mm"):
                        b[f"left_{k_}"], b[f"right_{k_}"] = b.get(f"right_{k_}"), b.get(f"left_{k_}")
                    lms = {"left": lms.get("right"), "right": lms.get("left")}
                entry = {"left_x": b.get("left_x"), "right_x": b.get("right_x"),
                         "left_wx": b.get("left_wx"), "right_wx": b.get("right_wx"),
                         "left_mm": b.get("left_mm"), "right_mm": b.get("right_mm"),
                         "mode": "minimap" if has_mm else "screen"}
                lm_dir = os.path.join("calibration", "landmarks"); os.makedirs(lm_dir, exist_ok=True)
                entry["landmarks"] = []
                for side in ("left", "right"):
                    lm = lms.get(side)
                    if lm is None or lm["gray"].size == 0:
                        continue
                    f = os.path.join(lm_dir, f"{m}_{side}.png")
                    cv2.imwrite(f, lm["gray"])
                    entry["landmarks"].append({"name": side, "file": f, "world_x": float(lm["world_x"]), "y": int(lm["y"])})
                can_world = (len(entry["landmarks"]) == 2 and entry["left_wx"] is not None and entry["right_wx"] is not None)
                st = settings()
                st.setdefault("patrol", {})[m] = entry
                app.save_settings(st)
                print(f"已保存 {m} 的巡逻端点: 小地图 {entry['left_mm']}~{entry['right_mm']}  屏幕 {entry['left_x']}~{entry['right_x']}  "
                      f"地图 {entry['left_wx']}~{entry['right_wx']}  地标 {len(entry['landmarks'])} 个")
                saved = True
                if can_world or (has_mm and has_screen):
                    opts = []
                    if has_mm:
                        opts.append(("小地图坐标（推荐：黄点=绝对位置，端点可在任意处，名牌被挡也能继续巡逻）", "minimap"))
                    if has_screen:
                        opts.append(("屏幕坐标（简单可靠；要求两端点都在镜头被顶住的位置）", "screen"))
                    if can_world:
                        opts.append(("地图坐标（靠地标校准，端点可以在任意位置）", "world"))
                    mode = choose("巡逻用哪种坐标", opts, allow_back=False)
                    entry["mode"] = mode or entry["mode"]
                    st["patrol"][m] = entry
                    app.save_settings(st)
                    print(f"巡逻坐标模式: {entry['mode']}")
                break
        elif k != 255:
            print(f"[key] 收到按键码 {k}（不是 l/r/s/q）")
    src.release()
    cv2.destroyWindow(win)
    if not saved:
        print("已取消（未保存）")


def do_patrol_clear():
    m = cfg().get("monster", {}).get("current")
    st = settings()
    if m and (st.get("patrol") or {}).pop(m, None) is not None:
        app.save_settings(st)
        print(f"已清除 {m} 的巡逻端点，巡逻回到「按时间掉头」")
    else:
        print("当前地图本来就没有标定端点")


def menu_patrol():
    while True:
        m = cfg().get("monster", {}).get("current") or "(未选怪物)"
        b = patrol_bounds_of(m)
        cur = (f"小地图 L={b.get('left_mm')} R={b.get('right_mm')}  屏幕 L={b.get('left_x')} R={b.get('right_x')}  "
               f"地图 L={b.get('left_wx')} R={b.get('right_wx')}  模式={b.get('mode', 'screen')}"
               if b else "未标定（巡逻按时间掉头）")
        act = choose(f"巡逻端点  地图: {m}  当前: {cur}", [
            ("标定端点（走到左端按 l / 右端按 r / s 保存）", do_patrol_bounds),
            ("切换坐标模式（小地图 / 屏幕 / 地图）", "mode"),
            ("清除本地图的端点", do_patrol_clear),
        ])
        if act is None:
            return
        if act == "mode":
            opts = []
            if b and b.get("left_mm") is not None and b.get("right_mm") is not None:
                opts.append(("小地图坐标（推荐）", "minimap"))
            if b and b.get("left_x") is not None and b.get("right_x") is not None:
                opts.append(("屏幕坐标", "screen"))
            if b and b.get("landmarks") and b.get("left_wx") is not None:
                opts.append(("地图坐标（地标校准）", "world"))
            if not opts:
                print("本地图还没有标定端点（先「标定端点」）")
                continue
            v = choose("巡逻坐标模式", opts)
            if v:
                set_setting(f"patrol.{m}.mode", v)
                print("已保存")
            continue
        act()


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
    mode = c.get("control", {}).get("mode", "off")
    ac = c.get("app") or {}
    show, secs = bool(ac.get("show", True)), float(ac.get("run_seconds") or 0)
    argv = ["--source", str(c["camera"]["source"]), "--monster", m]
    if not show:
        argv.append("--no-show")
    if secs > 0:
        argv += ["--seconds", str(secs)]
    if show:
        print(f"控制模式: {mode}（在「设置」里改）。窗口按键: q 退出 | 空格 暂停 | p 暂停/恢复控制 | t 抠模板 | r 录制 | c 重新标定")
    else:
        print(f"控制模式: {mode}。无窗口运行（省 CPU）：{'到 %.0f 秒自动停' % secs if secs > 0 else '不限时'}，"
              "随时按 Ctrl+C 停止（退出时自动松开全部按键）。要开窗口在「设置 → 运行方式」里改")
    app.main(argv)


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
                  f"标定={'已' if load_corners(c['screen']['calibration_file']) is not None else '未'}  "
                  f"端点={'已' if patrol_bounds_of(c.get('monster', {}).get('current')) else '未'}")
        act = choose("游戏目标检测  " + status, [
            ("开始检测", do_run),
            ("标定屏幕四角（首次 / 摄像头挪动后）", do_calibrate),
            ("玩家设置", menu_player),
            ("巡逻端点（走到地图两端记录，P7 位置巡逻）", menu_patrol),
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

"""一键标定 detection.sprite_scale —— 框一下怪，几秒出结果。

和 tools/wz_sprites.py scale 算的是同一个值，区别只在搜索范围：
  wz_sprites scale : 在整幅 1280x470 画面上盲扫 21 个尺度 x 120 帧 x 全部模板 约 8.5 万次带遮罩匹配（实测 ~2 小时）
  本脚本           : 你用鼠标圈出怪在哪，只在那个小窗口里扫 0.70~1.30 共 61 个尺度，几秒出结果

用法（在 game_vision 目录下）：
  python tools/measure_scale.py --mob 2230102
  python tools/measure_scale.py --mob 2230102 --source recordings/rec_20260910_155137.mp4
  python tools/measure_scale.py --mob 2230102 --mob 1130100 --write

操作：
  空格 = 暂停/继续    n = 下一帧    r = 重新框    q 或 Esc = 退出
  暂停后用鼠标拖框把怪圈起来，然后按回车（或空格）确认，才开始计算。
  按 c 或 Esc 取消这次框选。框大一点没关系，框歪了取消重来即可。

精灵优先取 templates/_wz/<mob>/（extract-all 生成的库，不需要 UnityPy）；
库里没有才回落到直接读客户端 aa 包（那条路需要 pip install UnityPy lz4）。
"""
import argparse
import glob
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import app                                              # noqa: E402
from calibration import Rectifier, load_corners         # noqa: E402
from camera import FrameSource                          # noqa: E402
from wz_sprites import masked_gray                      # noqa: E402

WIN = "measure_scale   space=pause  n=next  r=redraw  q=quit"
LO, HI = 0.70, 1.30                  # 扫描范围比 wz_sprites 的 0.85~1.05 更宽，窗口小所以扫得起
COARSE, FINE = 0.02, 0.01            # 先按 0.02 粗扫全程，再在峰值附近 ±0.02 按 0.01 精扫
MARGIN = 60                          # 框外再留多少像素，给匹配留挪动余地
MAX_SPRITES = 12                     # 每只怪最多用几张动作帧，帧之间很像，多了只是变慢


def load_sprites(mob, lib, aa_dir):
    """返回该怪的 RGBA 帧列表。优先精灵库目录，回落到客户端 aa 包。"""
    d = os.path.join(lib, mob)
    paths = sorted(glob.glob(os.path.join(d, "wz_*.png")))
    if paths:
        out = []
        for p in paths[:MAX_SPRITES]:
            img = cv2.imread(p, cv2.IMREAD_UNCHANGED)
            if img is not None and img.ndim == 3 and img.shape[2] == 4:
                out.append(img)
        if out:
            print(f"[sprite] {mob}: 从精灵库 {d} 取 {len(out)} 帧")
            return out
        print(f"[sprite] {mob}: {d} 里的图没有透明通道，跳过")
    from wz_sprites import MobSheets
    print(f"[sprite] {mob}: 精灵库里没有，改从客户端包读（需要 UnityPy/lz4）")
    ms = MobSheets(aa_dir)
    fr = [img for img, _ in ms.frames(mob)][:MAX_SPRITES]
    print(f"[sprite] {mob}: 从客户端包取 {len(fr)} 帧")
    return fr


def native_height(sprites):
    """精灵原始高度的中位数，只用来把结果换算成像素，方便人眼核对。"""
    return float(np.median([s.shape[0] for s in sprites]))


def score_at(win_gray, sprites, s):
    """某个缩放比下，所有精灵帧（含水平翻转）在这块画面里的最高匹配分。"""
    best = -1.0
    for sp in sprites:
        for flip in (False, True):
            t = cv2.flip(sp, 1) if flip else sp
            g, m = masked_gray(t, float(s))
            if g.shape[0] > win_gray.shape[0] or g.shape[1] > win_gray.shape[1]:
                continue
            if g.shape[0] < 6 or g.shape[1] < 6:
                continue
            r = np.nan_to_num(cv2.matchTemplate(win_gray, g, cv2.TM_CCOEFF_NORMED, mask=m), nan=-1)
            best = max(best, float(r.max()))
    return best


def scan(win_gray, sprites, lo=LO, hi=HI):
    """两段扫描：先按 COARSE 粗扫全程定位峰值，再在峰值 ±COARSE 内按 FINE 精扫。
    比全程 0.01 逐点扫快 2~3 倍，结果一致。返回 [(scale, score), ...] 已按 scale 排序。"""
    got = {}

    def evaluate(s):
        key = round(float(s), 2)
        if key not in got:
            v = score_at(win_gray, sprites, key)
            if v > -1:
                got[key] = v
        return got.get(key, -1.0)

    for s in np.arange(lo, hi + COARSE / 2, COARSE):
        evaluate(s)
    if not got:
        return []
    peak = max(got, key=got.get)
    for s in np.arange(max(lo, peak - COARSE), min(hi, peak + COARSE) + FINE / 2, FINE):
        evaluate(s)
    return sorted(got.items())


def report(res, nat_h, tag=""):
    """打印峰值附近的曲线，返回 (最佳尺度, 最佳分)。"""
    if not res:
        print("  没有任何尺度匹配得上：框太小，或者框里没有这只怪")
        return None
    best = max(res, key=lambda kv: kv[1])
    i = res.index(best)
    print(f"  {tag}峰值附近：")
    for s, v in res[max(0, i - 4):i + 5]:
        bar = "#" * int(max(v, 0) * 40)
        mark = "   <-- 最佳" if s == best[0] else ""
        print(f"    {s:.2f}  {v:.3f}  {bar}{mark}")
    print(f"  {tag}最佳尺度 {best[0]:.2f}，匹配分 {best[1]:.3f}，"
          f"精灵原始高 {nat_h:.0f}px 对应画面里约 {nat_h * best[0]:.0f}px")
    if best[1] < 0.55:
        print("  [!] 匹配分偏低：框里可能不是这只怪，或者被宠物/技能特效挡住了。换一帧重框。")
    if best[0] <= LO or best[0] >= HI:
        print("  [!] 最佳值落在扫描边界上，真实值可能在范围外，多半是框错了怪。")
    return best


def main():
    ap = argparse.ArgumentParser(description="框一下怪，算出 detection.sprite_scale")
    ap.add_argument("--mob", action="append", default=[], required=True,
                    help="怪物 ID，可多次。要和你框的那只对得上，如 2230102=野猪 1130100=斧木妖")
    ap.add_argument("--source", default=None, help="录像文件 / 摄像头编号 / ndi:源名；省略则用 settings 里的画面源")
    ap.add_argument("--calib", default=None, help="标定文件，省略用 config 里的")
    ap.add_argument("--lib", default=None, help="精灵库目录，默认 templates/_wz")
    ap.add_argument("--aa", default=None, help="客户端 aa 目录，精灵库缺失时才用")
    ap.add_argument("--write", action="store_true", help="把结果写进 settings.yaml 的 detection.sprite_scale")
    args = ap.parse_args()

    cfg = app.load_config(os.path.join(ROOT, "config.yaml"))
    sc = cfg["screen"]
    lib = args.lib or os.path.join(ROOT, "templates", "_wz")
    aa = args.aa or (cfg.get("wz") or {}).get("aa_dir") or os.path.join(os.path.dirname(ROOT), "aa")

    sprites = {}
    for mob in args.mob:
        try:
            s = load_sprites(mob, lib, aa)
        except Exception as e:
            sys.exit(f"取精灵失败 {mob}: {e}")
        if not s:
            sys.exit(f"怪物 {mob} 没有精灵帧")
        sprites[mob] = s

    corners = load_corners(args.calib or sc["calibration_file"])
    rect = Rectifier(corners, sc["output_width"], sc["output_height"])

    src_arg = args.source if args.source is not None else str(cfg["camera"]["source"])
    if os.path.exists(src_arg):
        src_arg = os.path.abspath(src_arg)
    print(f"[src] {src_arg}")
    src = FrameSource(src_arg, cfg["camera"]["width"], cfg["camera"]["height"], False)

    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WIN, sc["output_width"], sc["output_height"])
    print("操作：空格暂停 -> 鼠标拖框圈住怪 -> 按回车确认开始计算。n 下一帧，q 退出。")

    frame, paused = None, False
    try:
        while True:
            if not paused or frame is None:
                ok, f = src.read()
                if not ok:
                    if frame is None:
                        sys.exit("读不到画面：确认源是否正确、NDI 是否在发、录像路径是否存在")
                    print("[src] 播到结尾了，停在最后一帧")
                    paused = True
                else:
                    frame = rect(f)
            cv2.imshow(WIN, frame)
            k = cv2.waitKey(30 if not paused else 50) & 0xFF
            if k in (ord("q"), 27):
                return
            if k == ord(" "):
                paused = not paused
                print("[已暂停] 拖框圈住怪，然后按回车确认" if paused else "[继续播放]")
            if k == ord("n"):
                paused = False
                continue
            if not paused:
                continue

            box = cv2.selectROI(WIN, frame, showCrosshair=True, fromCenter=False)
            x, y, w, h = [int(v) for v in box]
            if w < 8 or h < 8:
                print("没框到东西（框太小或按了 Esc 取消）。再拖一次，或按 q 退出")
                continue

            H, W = frame.shape[:2]
            x0, y0 = max(0, x - MARGIN), max(0, y - MARGIN)
            x1, y1 = min(W, x + w + MARGIN), min(H, y + h + MARGIN)
            win_gray = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
            print(f"\n框选 {w}x{h}，搜索窗口 {x1 - x0}x{y1 - y0}，扫描 {LO:.2f} 到 {HI:.2f}")

            per_mob = {}
            for mob, sp in sprites.items():
                res = scan(win_gray, sp)
                b = report(res, native_height(sp), tag=f"[{mob}] ")
                if b:
                    per_mob[mob] = b

            if not per_mob:
                print("没算出结果，换一帧重试")
                paused = True
                continue
            vals = [b[0] for b in per_mob.values()]
            final = float(np.median(vals))
            print(f"\n==> detection.sprite_scale = {final:.2f}")
            if len(vals) > 1:
                spread = max(vals) - min(vals)
                note = "一致，可信" if spread <= 0.02 else "差得有点大，多半有一只框错了或被挡住"
                print(f"    {len(vals)} 只怪分别是 {', '.join(f'{v:.2f}' for v in vals)}，{note}")

            if args.write:
                st = app.load_settings() or {}
                st.setdefault("detection", {})["sprite_scale"] = round(final, 2)
                app.save_settings(st)
                print(f"    已写入 settings.yaml：detection.sprite_scale = {round(final, 2)}")
            else:
                print("    把它填进 config.yaml 的 detection.sprite_scale，或者重跑本脚本加 --write 自动写入")
            print("\n再验一帧按 n，退出按 q。")
            paused = True
    finally:
        try:
            src.release()
        except Exception:
            pass
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()

"""名牌匹配离线评估：同一段录像上逐帧比较「整块 NCC」和「只比文字像素的 masked NCC」（roi.text_mask_of）。

为什么：名牌底条是半透明的，背景亮/花时整块 NCC 掉到 0.6~0.7 就丢名牌或锁到别人的名牌上。开 text_mask 前要先用录像看两件事：
  1. 分数分布——低于阈值的帧比例是不是明显少了（决定 match/local/mid 阈值怎么重标）；
  2. 有没有副作用——掩码只占模板约 1/3 像素，会不会被亮纹理骗（看两种方法位置不一致的帧和最低分帧的拼图）。
没有逐帧真值，靠三样东西判断：两种方法的位置一致率、最高峰与次高峰的分差（越大越不容易锁错）、人工核对拼图。

用法（game_vision 目录）：
  python tools/nameplate_eval.py --source recordings/rec_xxx.mp4 --template templates/players/KEEEE.png [--calib calibration/xxx.json]
  python tools/nameplate_eval.py --source 拍屏.mp4 --template 抠好的名牌.png --raw --band 300 300 1650 700   # 不矫正，直接在原始帧上找
  选项：--step 3 每几帧算一帧  --max 600 最多算多少帧  --white 170 --dilate 1 掩码参数  --out 输出目录（默认 harvest/nameplate_eval）
输出：<out>/eval.jsonl（每帧两种方法的分/位置/次高峰/文字亮度）、<out>/worst.jpg（masked 分最低的 24 帧，两种方法各截一块）、
      <out>/disagree.jpg（两种方法位置不一致的 24 帧）；终端打印汇总。
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

from roi import text_mask_of  # noqa: E402


def ncc(img, tpl, mask=None):
    if mask is None:
        return cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
    return np.nan_to_num(cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED, mask=mask), nan=-1.0, posinf=-1.0, neginf=-1.0)


def best_two(res, th, tw):
    """最高峰 (分, x, y) 和压掉最高峰 ±半个模板后的次高峰分。"""
    _, m1, _, (x, y) = cv2.minMaxLoc(res)
    r = res.copy()
    r[max(0, y - th // 2):y + th // 2 + 1, max(0, x - tw // 2):x + tw // 2 + 1] = -1
    m2 = float(cv2.minMaxLoc(r)[1])
    return float(m1), x, y, m2


def crop_patch(gray_or_bgr, x, y, tw, th, pad=12, zoom=4):
    H, W = gray_or_bgr.shape[:2]
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(W, x + tw + pad), min(H, y + th + pad)
    p = gray_or_bgr[y0:y1, x0:x1]
    if p.ndim == 2:
        p = cv2.cvtColor(p, cv2.COLOR_GRAY2BGR)
    p = cv2.resize(p, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_NEAREST)
    cv2.rectangle(p, ((x - x0) * zoom, (y - y0) * zoom), ((x - x0 + tw) * zoom, (y - y0 + th) * zoom), (0, 255, 0), 1)
    return p


def sheet(rows, frames_bgr, tw, th, title, path, n=24, cols=4):
    """每行一帧：左 = 整块 NCC 的位置，右 = masked 的位置。"""
    tiles = []
    for r in rows[:n]:
        f = frames_bgr.get(r["frame"])
        if f is None:
            continue
        a = crop_patch(f, r["plain_x"], r["plain_y"], tw, th)
        b = crop_patch(f, r["mask_x"], r["mask_y"], tw, th)
        h = max(a.shape[0], b.shape[0])
        a = cv2.copyMakeBorder(a, 0, h - a.shape[0], 0, 0, cv2.BORDER_CONSTANT)
        b = cv2.copyMakeBorder(b, 0, h - b.shape[0], 0, 0, cv2.BORDER_CONSTANT)
        t = np.hstack([a, np.full((h, 8, 3), 60, np.uint8), b])
        t = cv2.copyMakeBorder(t, 18, 0, 0, 0, cv2.BORDER_CONSTANT)
        cv2.putText(t, f"f{r['frame']} plain {r['plain']:.2f}/{r['plain2']:.2f}  mask {r['mask']:.2f}/{r['mask2']:.2f} txt {r['text_mean']:.0f}-{r['ring_mean']:.0f}",
                    (2, 13), 0, 0.4, (0, 255, 255), 1)
        tiles.append(t)
    if not tiles:
        return
    w = max(t.shape[1] for t in tiles)
    h = max(t.shape[0] for t in tiles)
    tiles = [cv2.copyMakeBorder(t, 0, h - t.shape[0], 0, w - t.shape[1], cv2.BORDER_CONSTANT) for t in tiles]
    while len(tiles) % cols:
        tiles.append(np.zeros((h, w, 3), np.uint8))
    grid = np.vstack([np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)])
    grid = cv2.copyMakeBorder(grid, 22, 0, 0, 0, cv2.BORDER_CONSTANT)
    cv2.putText(grid, title + "   (left = plain NCC, right = text-mask NCC)", (4, 16), 0, 0.5, (255, 255, 255), 1)
    cv2.imwrite(path, grid, [cv2.IMWRITE_JPEG_QUALITY, 88])


def pct(vals, q):
    return float(np.percentile(vals, q)) if vals else float("nan")


def main():
    ap = argparse.ArgumentParser(description="名牌整块 NCC vs 文字掩码 NCC 离线评估")
    ap.add_argument("--source", required=True, help="录像文件")
    ap.add_argument("--template", required=True, help="名牌模板 png（要和录像同一画面来源/缩放）")
    ap.add_argument("--calib", default=None, help="标定文件；默认 config 里的；--raw 时不矫正")
    ap.add_argument("--raw", action="store_true", help="不矫正，直接在原始帧上找（拍屏录像+从原始帧抠的模板）")
    ap.add_argument("--band", type=int, nargs=4, default=None, metavar=("X1", "Y1", "X2", "Y2"), help="搜索范围；默认 config 的 roi.player.search_band")
    ap.add_argument("--step", type=int, default=3)
    ap.add_argument("--max", type=int, default=600, help="最多评估多少帧")
    ap.add_argument("--white", type=int, default=170)
    ap.add_argument("--dilate", type=int, default=1)
    ap.add_argument("--thr", type=float, nargs="+", default=[0.55, 0.7], help="统计低于这些阈值的帧比例")
    ap.add_argument("--truth", default=None, help="逐帧真值 jsonl（每行 {frame, x, y} = 名牌左上角，矫正后坐标）；给了就按命中率评估")
    ap.add_argument("--tol", type=int, default=6, help="命中判定：与真值相差不超过这么多像素")
    ap.add_argument("--out", default=os.path.join(ROOT, "harvest", "nameplate_eval"))
    a = ap.parse_args()
    truth = {}
    if a.truth:
        for line in open(a.truth, encoding="utf-8"):
            if line.strip():
                d = json.loads(line)
                truth[int(d["frame"])] = (int(d["x"]), int(d["y"]))

    import app
    cfg = app.load_config(os.path.join(ROOT, "config.yaml"))
    tpl = cv2.imread(a.template, cv2.IMREAD_GRAYSCALE)
    if tpl is None:
        sys.exit(f"模板读不到: {a.template}")
    th, tw = tpl.shape
    core, mask = text_mask_of(tpl, a.white, a.dilate)
    print(f"模板 {tw}x{th}：文字像素 {int(core.sum())} ({100 * core.mean():.0f}%)，掩码像素 {int(mask.sum())} ({100 * mask.mean():.0f}%)")
    if int(core.sum()) < 15:
        sys.exit("文字像素太少：--white 调低，或模板不是白字名牌")

    rect = None
    if not a.raw:
        from calibration import Rectifier, load_corners
        sc = cfg["screen"]
        rect = Rectifier(load_corners(a.calib or sc["calibration_file"]), sc["output_width"], sc["output_height"])
    band = a.band or cfg["roi"]["player"].get("search_band")
    cap = cv2.VideoCapture(a.source)
    if not cap.isOpened():
        sys.exit(f"打不到录像: {a.source}")
    os.makedirs(a.out, exist_ok=True)
    rows, frames_bgr = [], {}
    core_b, ring_b = core.astype(bool), mask.astype(bool) & ~core.astype(bool)
    i = 0
    while len(rows) < a.max:
        ok, f = cap.read()
        if not ok:
            break
        i += 1
        if (i - 1) % a.step:
            continue
        g_bgr = rect(f) if rect is not None else f
        gray = cv2.cvtColor(g_bgr, cv2.COLOR_BGR2GRAY)
        if band:
            x1, y1, x2, y2 = band
        else:
            x1, y1, x2, y2 = 0, 0, gray.shape[1], gray.shape[0]
        sub = gray[y1:y2, x1:x2]
        p1, px, py, p2 = best_two(ncc(sub, tpl), th, tw)
        m1, mx, my, m2 = best_two(ncc(sub, tpl, mask), th, tw)
        px, py, mx, my = px + x1, py + y1, mx + x1, my + y1
        patch = gray[my:my + th, mx:mx + tw]
        tmean = float(patch[core_b].mean()) if patch.shape == tpl.shape else float("nan")
        rmean = float(patch[ring_b].mean()) if (patch.shape == tpl.shape and ring_b.any()) else float("nan")
        rows.append(dict(frame=i, plain=p1, plain2=p2, plain_x=px, plain_y=py, mask=m1, mask2=m2, mask_x=mx, mask_y=my,
                         agree=bool(abs(px - mx) <= 4 and abs(py - my) <= 4), text_mean=tmean, ring_mean=rmean))
        frames_bgr[i] = g_bgr
        if len(frames_bgr) > 400:                      # 内存：只留最近的帧给拼图用（最差帧多半分散，够用）
            frames_bgr.pop(next(iter(frames_bgr)))
    cap.release()
    if not rows:
        sys.exit("没有帧")
    with open(os.path.join(a.out, "eval.jsonl"), "w", encoding="utf-8") as fp:
        for r in rows:
            fp.write(json.dumps(r) + "\n")

    P = [r["plain"] for r in rows]; M = [r["mask"] for r in rows]
    Pm = [r["plain"] - r["plain2"] for r in rows]; Mm = [r["mask"] - r["mask2"] for r in rows]
    agree = sum(r["agree"] for r in rows) / len(rows)
    print(f"\n帧数 {len(rows)}（每 {a.step} 帧一算），搜索范围 {band}")
    print(f"{'':12s} {'中位':>6s} {'p25':>6s} {'p10':>6s} " + " ".join(f"<{t:.2f}".rjust(7) for t in a.thr) + f" {'峰差中位':>8s}")
    for name, v, mg in (("整块 NCC", P, Pm), ("文字掩码", M, Mm)):
        frac = " ".join(f"{100 * sum(x < t for x in v) / len(v):6.1f}%" for t in a.thr)
        print(f"{name:12s} {pct(v, 50):6.3f} {pct(v, 25):6.3f} {pct(v, 10):6.3f} {frac} {pct(mg, 50):8.3f}")
    print(f"两种方法位置一致（±4 px）的帧：{100 * agree:.1f}%")
    tm = [r["text_mean"] for r in rows if not np.isnan(r["text_mean"])]
    ct = [r["text_mean"] - r["ring_mean"] for r in rows if not np.isnan(r["ring_mean"])]
    if tm:
        print(f"掩码位置处文字亮度 中位 {pct(tm, 50):.0f} / p10 {pct(tm, 10):.0f}；文字-外圈对比 中位 {pct(ct, 50):.0f} / p10 {pct(ct, 10):.0f}"
              f"（config text_mask.min_text / min_contrast 参考）")
    if truth:
        tr = [r for r in rows if r["frame"] in truth]
        print(f"\n真值帧 {len(tr)}（±{a.tol} px 算命中）。「阈值→命中率」= 分数 ≥ 阈值且位置正确的帧比例；「错锁」= 分数 ≥ 阈值但位置错（会锁到别处，最伤）")
        for name, kx, ky, ks in (("整块 NCC", "plain_x", "plain_y", "plain"), ("文字掩码", "mask_x", "mask_y", "mask")):
            hit = [abs(r[kx] - truth[r["frame"]][0]) <= a.tol and abs(r[ky] - truth[r["frame"]][1]) <= a.tol for r in tr]
            s_hit = [r[ks] for r, h in zip(tr, hit) if h]
            s_miss = [r[ks] for r, h in zip(tr, hit) if not h]
            line = f"{name:12s} 位置正确 {100 * sum(hit) / len(tr):5.1f}%  正确时分 中位 {pct(s_hit, 50):.3f}/p10 {pct(s_hit, 10):.3f}  错误时分 中位 {pct(s_miss, 50):.3f}/p90 {pct(s_miss, 90):.3f} |"
            for t in sorted(set(a.thr + [0.6, 0.65, 0.75, 0.8])):
                ok = sum(1 for r, h in zip(tr, hit) if h and r[ks] >= t)
                bad = sum(1 for r, h in zip(tr, hit) if (not h) and r[ks] >= t)
                line += f" {t:.2f}→{100 * ok / len(tr):4.0f}%/错锁{100 * bad / len(tr):3.0f}%"
            print(line)
    worst = sorted(rows, key=lambda r: r["mask"])
    sheet(worst, frames_bgr, tw, th, "lowest text-mask scores", os.path.join(a.out, "worst.jpg"))
    dis = [r for r in rows if not r["agree"]]
    sheet(sorted(dis, key=lambda r: -(r["plain"] + r["mask"])), frames_bgr, tw, th, "plain vs mask disagree", os.path.join(a.out, "disagree.jpg"))
    print(f"拼图：{os.path.join(a.out, 'worst.jpg')}，{os.path.join(a.out, 'disagree.jpg')}（{len(dis)} 帧不一致）；逐帧 eval.jsonl")


if __name__ == "__main__":
    main()

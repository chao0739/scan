"""模板去重：把与已保留模板过于相似（含镜像相似）的模板移到 <目录>/_dup/，减少匹配次数、降低单帧耗时。

检测耗时 ≈ ROI 面积 × 模板数（×2 若开 flip）。半自动采集容易挑出几十张几乎一样的模板，
去重后检测结果几乎不变（实测 woniu 45→15 张：138 个 ROI 判定 137 个一致，单 ROI 104ms→34ms）。

用法：
  python tools/dedupe_templates.py templates/woniu            # 默认阈值 0.75
  python tools/dedupe_templates.py templates/woniu --thr 0.85  # 更保守（保留更多）
  python tools/dedupe_templates.py templates/woniu --restore   # 把 _dup/ 里的全部移回
"""
import argparse
import glob
import os
import shutil

import cv2

DUP_DIR = "_dup"


def _sim(a, b):
    """两张灰度图的相似度：小图在大图里滑动匹配的最高分；尺寸互不包含时视为不相似。"""
    if a.shape[0] > b.shape[0] or a.shape[1] > b.shape[1]:
        a, b = b, a
    if a.shape[0] > b.shape[0] or a.shape[1] > b.shape[1]:
        return 0.0
    return float(cv2.matchTemplate(b, a, cv2.TM_CCOEFF_NORMED).max())


def plan(template_dir, thr=0.75, flip_aware=True):
    """返回 (keep, drop) 两个路径列表，不动文件。按文件名顺序贪心：与任一已保留模板相似度 >= thr 的丢弃。"""
    paths = sorted(glob.glob(os.path.join(template_dir, "*.png")))
    imgs = {}
    for p in paths:
        im = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
        if im is not None:
            imgs[p] = im
    keep, drop = [], []
    for p in imgs:
        dup = False
        for k in keep:
            if _sim(imgs[p], imgs[k]) >= thr or (flip_aware and _sim(cv2.flip(imgs[p], 1), imgs[k]) >= thr):
                dup = True
                break
        (drop if dup else keep).append(p)
    return keep, drop


def dedupe(template_dir, thr=0.75, flip_aware=True, dry_run=False):
    """执行去重：drop 的文件移到 template_dir/_dup/。返回 (keep, drop)。"""
    keep, drop = plan(template_dir, thr, flip_aware)
    if not dry_run and drop:
        d = os.path.join(template_dir, DUP_DIR)
        os.makedirs(d, exist_ok=True)
        for p in drop:
            shutil.move(p, os.path.join(d, os.path.basename(p)))
    return keep, drop


def restore(template_dir):
    """把 _dup/ 里的模板全部移回。返回移回的数量。"""
    d = os.path.join(template_dir, DUP_DIR)
    files = glob.glob(os.path.join(d, "*.png"))
    for p in files:
        shutil.move(p, os.path.join(template_dir, os.path.basename(p)))
    return len(files)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("template_dir")
    ap.add_argument("--thr", type=float, default=0.75, help="相似度阈值，越高保留越多")
    ap.add_argument("--no-flip", action="store_true", help="不把镜像相似视为重复")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--restore", action="store_true")
    a = ap.parse_args()
    if a.restore:
        print(f"已移回 {restore(a.template_dir)} 张")
        return
    keep, drop = dedupe(a.template_dir, a.thr, not a.no_flip, a.dry_run)
    print(f"保留 {len(keep)} 张，{'将' if a.dry_run else '已'}移入 {DUP_DIR}/ {len(drop)} 张")
    for p in drop:
        print("  -", os.path.basename(p))


if __name__ == "__main__":
    main()

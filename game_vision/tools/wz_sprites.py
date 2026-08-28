"""从游戏客户端的 Unity Addressables 包（aa/ 目录）里直接取怪物精灵图，做成带透明通道的模板。

为什么：手抠/采集的模板带着当时的背景，换个背景（棕色岩壁）分数就掉到 0.5~0.65，和岩石纹理分不开；
原版精灵图有 alpha，可以只比精灵像素（masked 匹配），真怪 0.85~0.94、背景 ≤0.6。

包结构（2026-08-28 实测，Unity 6000.3，UnityFS/LZ4）：
  aa/w/spritesheet_<hash>.bundle 里 Assets/WzAssets/SpriteSheet/CN/Mob/<mobid>.wzspritesheet + <mobid>_0.png
  <mobid>_0.png = 该怪所有动作帧拼成的 RGBA32 图集（贴图数据在 .resS 里）；
  .wzspritesheet 是 MonoBehaviour，没有 typetree，但里面有一张 (x, y, w, h) 帧矩形表（y 从图集底部量起）。
  Mob 包解压后 8.8 GB，所以只按需解压需要的 LZ4 块（BundleReader），不整包载入。

用法：
  python tools/wz_sprites.py list  [--aa /home/cc/scan/aa] [--grep 2230]      # 列出 Mob 包里的怪物 ID
  python tools/wz_sprites.py atlas --mob 2230102 [--mob 1130100] --out /tmp/x   # 导出整张图集（看是哪种怪）
  python tools/wz_sprites.py extract --mob 2230102 --mob 1130100 --out templates/yezhu [--dedupe 0.9]
      -> templates/yezhu/wz_2230102_00.png ...（RGBA，原始尺寸；运行时按 detection.sprite_scale 缩放）
  python tools/wz_sprites.py scale --mob 2230102 --source recordings/harvest_yezhu.mp4 [--calib ...]
      -> 扫描 0.85~1.05 找精灵在矫正后画面里的缩放比（本机 1854x1042 窗口 -> 1280x720 实测 0.93）
需要：pip install UnityPy lz4
"""
import argparse
import bisect
import glob
import lzma
import os
import re
import struct
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


# ---------------- UnityFS 流式读取 ----------------
class BundleReader:
    """只解压需要的字节范围。"""

    def __init__(self, path):
        import lz4.block
        self.lz4 = lz4.block
        self.f = open(path, "rb")
        b = self.f.read(64 * 1024)

        def cstr(off):
            e = b.index(b"\x00", off)
            return b[off:e].decode(), e + 1

        sig, off = cstr(0)
        if sig != "UnityFS":
            raise RuntimeError(f"不是 UnityFS 包: {path}")
        self.version = struct.unpack(">I", b[off:off + 4])[0]
        off += 4
        _, off = cstr(off)
        _, off = cstr(off)
        _size, cbi, ubi, flags = struct.unpack(">QIII", b[off:off + 20])
        off += 20
        if self.version >= 7:
            off = (off + 15) & ~15
        if flags & 0x80:
            raise RuntimeError("blocks info 在文件尾，未支持")
        self.f.seek(off)
        bi = self.f.read(cbi)
        comp = flags & 0x3F
        if comp in (2, 3):
            bi = self.lz4.decompress(bi, uncompressed_size=ubi)
        elif comp == 1:
            bi = lzma.decompress(bi)
        off += cbi
        n = struct.unpack(">I", bi[16:20])[0]
        p = 20
        self.blocks = []
        for _ in range(n):
            self.blocks.append(struct.unpack(">IIH", bi[p:p + 10]))
            p += 10
        nn = struct.unpack(">I", bi[p:p + 4])[0]
        p += 4
        self.nodes = []
        for _ in range(nn):
            o, s, fl = struct.unpack(">QQI", bi[p:p + 20])
            p += 20
            e = bi.index(b"\x00", p)
            self.nodes.append((bi[p:e].decode(), o, s))
            p = e + 1
        if self.version >= 7 and (flags & 0x200):
            off = (off + 15) & ~15
        self.u_off, self.c_off = [0], [off]
        for u, c, _ in self.blocks:
            self.u_off.append(self.u_off[-1] + u)
            self.c_off.append(self.c_off[-1] + c)
        self._cache = {}

    def _block(self, i):
        if i in self._cache:
            return self._cache[i]
        u, c, fl = self.blocks[i]
        self.f.seek(self.c_off[i])
        raw = self.f.read(c)
        m = fl & 0x3F
        data = self.lz4.decompress(raw, uncompressed_size=u) if m in (2, 3) else (lzma.decompress(raw) if m == 1 else raw)
        if len(self._cache) > 64:
            self._cache.clear()
        self._cache[i] = data
        return data

    def read(self, offset, size):
        out = []
        i = bisect.bisect_right(self.u_off, offset) - 1
        pos, end = offset, offset + size
        while pos < end and i < len(self.blocks):
            d = self._block(i)
            s = pos - self.u_off[i]
            take = min(len(d) - s, end - pos)
            out.append(d[s:s + take])
            pos += take
            i += 1
        return b"".join(out)

    def node(self, name):
        for n, o, s in self.nodes:
            if n == name:
                return o, s
        raise KeyError(name)


def find_mob_bundle(aa_dir):
    """在 aa/w 里找含 Mob 精灵图的包（读每个 spritesheet 包的 CAB 节点，搜 'SpriteSheet/CN/Mob/'）。"""
    cands = sorted(glob.glob(os.path.join(aa_dir, "w", "spritesheet_*.bundle")), key=os.path.getsize, reverse=True)
    for p in cands:
        try:
            br = BundleReader(p)
        except Exception:
            continue
        for n, o, s in br.nodes:
            if n.endswith(".resS"):
                continue
            head = br.read(o, min(s, 64 * 2 ** 20))
            if b"SpriteSheet/CN/Mob/" in head:
                return p, br, n
    raise RuntimeError(f"{aa_dir}/w 下没找到含 Mob 精灵图的包")


class MobSheets:
    def __init__(self, aa_dir):
        import UnityPy
        self.path, self.br, self.cab_name = find_mob_bundle(aa_dir)
        o, s = self.br.node(self.cab_name)
        cab = self.br.read(o, s)
        self.res_off, _ = self.br.node(self.cab_name + ".resS")
        tmp = os.path.join(aa_dir, ".wz_cache")
        os.makedirs(tmp, exist_ok=True)
        cab_path = os.path.join(tmp, self.cab_name)
        if not os.path.exists(cab_path) or os.path.getsize(cab_path) != len(cab):
            with open(cab_path, "wb") as f:
                f.write(cab)
        self.env = UnityPy.load(cab_path)
        self.tex = {}
        self.meta = {}
        for ob in self.env.objects:
            if ob.type.name == "Texture2D":
                d = ob.read()
                self.tex[d.m_Name] = d
            elif ob.type.name == "MonoBehaviour":
                raw = ob.get_raw_data()
                m = re.search(rb"Mob/([0-9A-Za-z_]+)", raw[:256])
                if m:
                    self.meta[m.group(1).decode()] = raw
        self.ids = sorted(self.meta)

    def atlas(self, mob):
        """返回 BGRA 图集（列表，通常 1 张）。"""
        out = []
        for n in sorted(k for k in self.tex if k.startswith(mob + "_")):
            d = self.tex[n]
            sd = d.m_StreamData
            fmt = int(getattr(d.m_TextureFormat, "value", d.m_TextureFormat))
            data = self.br.read(self.res_off + sd.offset, sd.size) if sd.size else bytes(d.image_data)
            if fmt != 4:      # 只见过 RGBA32（=4）；别的格式要用 texture2ddecoder 解，遇到再加
                raise RuntimeError(f"{n}: 贴图格式 {fmt} 不是 RGBA32，暂不支持")
            arr = np.frombuffer(data[:d.m_Width * d.m_Height * 4], np.uint8).reshape(d.m_Height, d.m_Width, 4)[::-1]   # 贴图自下而上存
            out.append(cv2.cvtColor(np.ascontiguousarray(arr), cv2.COLOR_RGBA2BGRA))
        return out

    def frame_rects(self, mob, W, H):
        """帧矩形表：原始字节里最长的一串合法 (x,y,w,h)（在图集内、互不重叠）。y 从图集底部量起。"""
        raw = self.meta[mob]
        ints = struct.unpack("<%di" % (len(raw) // 4), raw[:len(raw) // 4 * 4])
        best = []
        for start in range(0, min(len(ints) - 4, 400)):
            run, i = [], start
            while i + 3 < len(ints):
                x, y, w, h = ints[i:i + 4]
                if not (0 <= x and 0 <= y and 4 <= w <= W and 4 <= h <= H and x + w <= W and y + h <= H):
                    break
                if any(x < bx + bw and bx < x + w and y < by + bh and by < y + h for bx, by, bw, bh in run):
                    break
                run.append((x, y, w, h))
                i += 4
            if len(run) > len(best):
                best = run
        return best

    def frames(self, mob):
        """[(BGRA 帧, (x,y,w,h))]，只取第一张图集。"""
        atl = self.atlas(mob)
        if not atl:
            raise KeyError(f"没有 {mob} 的图集")
        a = atl[0]
        H, W = a.shape[:2]
        out = []
        for x, y, w, h in self.frame_rects(mob, W, H):
            yt = H - y - h
            out.append((a[yt:yt + h, x:x + w].copy(), (x, yt, w, h)))
        return out


def masked_gray(bgra, scale=1.0):
    t = bgra if scale == 1.0 else cv2.resize(bgra, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(t[..., :3], cv2.COLOR_BGR2GRAY), (t[..., 3] > 128).astype(np.uint8)


def dedupe(frames, thr):
    keep = []
    for i, (fr, _) in enumerate(frames):
        gi, mi = masked_gray(fr)
        dup = False
        for j in keep:
            gj, mj = masked_gray(frames[j][0])
            big, small, sm = (gi, gj, mj) if gi.size >= gj.size else (gj, gi, mi)
            h, w = max(big.shape[0], small.shape[0]), max(big.shape[1], small.shape[1])
            pad = np.zeros((h, w), np.uint8)
            pad[:big.shape[0], :big.shape[1]] = big
            r = np.nan_to_num(cv2.matchTemplate(pad, small, cv2.TM_CCOEFF_NORMED, mask=sm), nan=-1)
            if r.max() >= thr:
                dup = True
                break
        if not dup:
            keep.append(i)
    return [frames[i] for i in keep]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["list", "atlas", "extract", "scale"])
    ap.add_argument("--aa", default=None, help="客户端 aa 目录（默认 config.yaml 的 wz.aa_dir）")
    ap.add_argument("--mob", action="append", default=[], help="怪物 ID（可多次），如 2230102=野猪 1130100=斧木妖")
    ap.add_argument("--grep", default=None, help="list 时只列含此子串的 ID")
    ap.add_argument("--out", default=None, help="atlas/extract 的输出目录")
    ap.add_argument("--dedupe", type=float, default=0.9, help="extract 时动画帧两两 masked 相似度 ≥此值去重；0 不去重")
    ap.add_argument("--source", default=None, help="scale 用的录像/来源")
    ap.add_argument("--calib", default=None)
    ap.add_argument("--every", type=int, default=30, help="scale 每隔几帧采一帧")
    args = ap.parse_args(argv)
    import app  # noqa: E402
    cfg = app.load_config(os.path.join(ROOT, "config.yaml"))
    aa = args.aa or (cfg.get("wz") or {}).get("aa_dir") or os.path.join(os.path.dirname(ROOT), "aa")
    ms = MobSheets(aa)
    print(f"[wz] Mob 包 {os.path.basename(ms.path)}，怪物 {len(ms.ids)} 个")
    if args.cmd == "list":
        ids = [i for i in ms.ids if not args.grep or args.grep in i]
        print(" ".join(ids))
        return
    if not args.mob:
        sys.exit("需要 --mob <id>")
    if args.cmd == "atlas":
        os.makedirs(args.out or ".", exist_ok=True)
        for mob in args.mob:
            for k, a in enumerate(ms.atlas(mob)):
                p = os.path.join(args.out or ".", f"atlas_{mob}_{k}.png")
                cv2.imwrite(p, a)
                print(f"  {p} {a.shape[1]}x{a.shape[0]}")
        return
    if args.cmd == "extract":
        out = args.out or os.path.join(ROOT, "templates", "wz_" + "_".join(args.mob))
        os.makedirs(out, exist_ok=True)
        for mob in args.mob:
            fr = ms.frames(mob)
            n0 = len(fr)
            if args.dedupe:
                fr = dedupe(fr, args.dedupe)
            for k, (img, rect) in enumerate(fr):
                cv2.imwrite(os.path.join(out, f"wz_{mob}_{k:02d}.png"), img)
            print(f"  {mob}: {n0} 帧 -> 保留 {len(fr)} 张 -> {out}/wz_{mob}_*.png  尺寸 {[f'{r[2]}x{r[3]}' for _, r in fr]}")
        print("提示：运行时按 detection.sprite_scale 缩放（本机 0.93）；先用 `scale` 子命令标定")
        return
    if args.cmd == "scale":
        from camera import FrameSource
        from calibration import Rectifier, load_corners
        sc = cfg["screen"]
        corners = load_corners(args.calib or sc["calibration_file"])
        rect = Rectifier(corners, sc["output_width"], sc["output_height"])
        src = FrameSource(os.path.abspath(args.source) if os.path.exists(args.source or "") else (args.source or str(cfg["camera"]["source"])),
                          cfg["camera"]["width"], cfg["camera"]["height"], False)
        frames = []
        n = 0
        while True:
            ok, f = src.read()
            if not ok or (src.is_file is False and n >= 60 * args.every):
                break
            if n % args.every == 0:
                frames.append(cv2.cvtColor(rect(f), cv2.COLOR_BGR2GRAY)[150:620])
            n += 1
            if len(frames) >= 120:
                break
        src.release()
        sprites = []
        for mob in args.mob:
            sprites += [img for img, _ in dedupe(ms.frames(mob), 0.9)]
        print(f"[scale] 采样 {len(frames)} 帧，精灵 {len(sprites)} 张；每个尺度取各帧最高分的前 10 名均值（真怪出现的帧）")
        best = None
        for s in np.arange(0.85, 1.051, 0.01):
            vs = []
            for t in sprites:
                for flip in (False, True):
                    tt = cv2.flip(t, 1) if flip else t
                    vs.append(masked_gray(tt, s))
            tops = []
            for g in frames:
                b = -1.0
                for gray, mask in vs:
                    if gray.shape[0] > g.shape[0] or gray.shape[1] > g.shape[1]:
                        continue
                    r = np.nan_to_num(cv2.matchTemplate(g, gray, cv2.TM_CCOEFF_NORMED, mask=mask), nan=-1)
                    b = max(b, float(r.max()))
                tops.append(b)
            v = float(np.mean(sorted(tops)[-10:]))
            print(f"  {s:.2f}: top10 均值 {v:.3f}")
            if best is None or v > best[1]:
                best = (round(float(s), 2), v)
        print(f"最佳尺度 {best[0]}（分 {best[1]:.3f}）-> 写进 settings: detection.sprite_scale: {best[0]}")


if __name__ == "__main__":
    main()

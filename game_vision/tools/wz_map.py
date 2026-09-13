"""从游戏客户端 aa/ 包里读地图数据（.wzjson）：脚手架/平台(foothold)、小地图参数(miniMap)、传送门、绳梯、怪物刷新点。

为什么：名牌被背景淹掉时要靠「小地图黄点 + 镜头位移」推算角色屏幕位置，巡逻路线也想按地图自动生成——
这些都需要地图的真实几何，而客户端里就有全部 705 张图的原版数据（Map.wz 的 Unity 版），不用手工建模。

包结构（2026-09-13 实测）：aa/w/json_<hash>.bundle（约 42 MB 那个）里 AssetBundle 容器
  Assets/WzAssets/Json/Map/Map/Map<N>/<9位地图ID>.wzjson  -> MonoBehaviour，无 typetree，自定义二进制：
  [m_Name][36 个 uint32 头][ "WZJS" ][ver=5][节点表 n×8 int32][int 池][...][名字表][路径表][字符串表]
  头里的偏移都相对 "WZJS" 起点。节点记录 8×int32 = [type, nameIdx, valIdx, firstChild, childCount, parent, ?, ?]：
    type 2 = 对象（子节点 firstChild..firstChild+childCount，按名字排序）
    type 6 = int（int 池[valIdx]；池按原始 WZ 顺序排列、无 count 前缀）
    type 11 = 字符串（字符串表[valIdx]）    type 18 = canvas（贴图引用，本文件里无像素）   type 8 = mobRate 等（浮点，未解）
  三张字符串表各是 (count, 字节区偏移, 偏移数组偏移)：h[26..28] 名字、h[29..31] 路径、h[32..34] 字符串值。
坐标约定（与原版 MapleStory 相同）：x 向右、y 向下（正值在下）；foothold 用 x1,y1,x2,y2 段 + prev/next 串成链，x1==x2 的是竖直墙；
  miniMap: 小地图画布像素 = (世界坐标 + centerX/centerY) / mag，画布尺寸 = width/mag × height/mag（width/height 是世界像素）。
  705 张图里 333 张有 miniMap（mag 全是 4）、397 张有 VRLeft/VRRight/VRTop/VRBottom（镜头边界）；勇士部落几张图没有 VR，
  镜头边界要么按 foothold 外包估，要么运行时从名牌可见的帧学。地图中文名在 String 表（find 子命令）。

用法（在 game_vision 目录）：
  python tools/wz_map.py find 野猪                       # 按名字/ID 搜地图
  python tools/wz_map.py export --map 101040001 [--out calibration/maps]   # 导出 JSON（info/miniMap/foothold/portal/ladderRope/life）
  python tools/wz_map.py platforms --map 101040001       # 打印平台链、绳梯、传送门、刷怪点（看路线怎么规划）
需要：pip install UnityPy（-r requirements-tools.txt）
"""
import argparse
import collections
import glob
import json
import os
import struct
import sys

# Windows 控制台/管道默认 cp1252，打印中文会炸
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


# ---------------- 包 ----------------
def default_aa_dir():
    try:
        import yaml
        cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8")) or {}
        d = (cfg.get("wz") or {}).get("aa_dir")
        if d and os.path.isdir(d):
            return d
    except Exception:
        pass
    for d in (os.path.join(ROOT, "..", "aa"), os.path.join(ROOT, "aa")):
        if os.path.isdir(d):
            return os.path.abspath(d)
    return None


class MapBundle:
    """含 Json/Map/Map/ 的 json 包：地图 ID -> MonoBehaviour 原始字节。"""

    def __init__(self, aa_dir):
        import UnityPy
        self.aa_dir = aa_dir
        self.env = self.cont = None
        for p in sorted(glob.glob(os.path.join(aa_dir, "w", "json_*.bundle")), key=os.path.getsize, reverse=True):
            env = UnityPy.load(p)
            cont = {}
            for ob in env.objects:
                if ob.type.name == "AssetBundle":
                    ab = ob.read()
                    items = ab.m_Container.items() if hasattr(ab.m_Container, "items") else ab.m_Container
                    for k, v in items:
                        cont[k] = v.asset.path_id
                    break
            if any("/Json/Map/Map/" in k for k in cont):
                self.path, self.env, self.cont = p, env, cont
                break
        if self.env is None:
            raise RuntimeError(f"{aa_dir}/w 下没找到含 Json/Map/Map 的包")
        self.objs = {ob.path_id: ob for ob in self.env.objects}
        self.ids = sorted(k.rsplit("/", 1)[1][:-7] for k in self.cont if "/Json/Map/Map/Map" in k and k.endswith(".wzjson")
                          and k.rsplit("/", 1)[1][:-7].isdigit())

    def raw(self, map_id):
        map_id = str(int(map_id)).zfill(9)
        for k, pid in self.cont.items():
            if k.endswith(f"/{map_id}.wzjson"):
                return self.objs[pid].get_raw_data()
        raise KeyError(f"地图 {map_id} 不在客户端包里")


def map_names(aa_dir):
    """String 表：{9位ID: (streetName, mapName)}。"""
    import UnityPy
    for p in sorted(glob.glob(os.path.join(aa_dir, "w", "json_*.bundle")), key=os.path.getsize):
        if os.path.getsize(p) > 20 * 2 ** 20:
            continue
        env = UnityPy.load(p)
        for ob in env.objects:
            if ob.type.name != "TextAsset":
                continue
            d = ob.read()
            if d.m_Name != "Map":
                continue
            txt = d.m_Script if isinstance(d.m_Script, str) else bytes(d.m_Script).decode("utf-8", "ignore")
            try:
                j = json.loads(txt)
            except Exception:
                continue
            if isinstance(j, dict) and "mapName" in next(iter(j.values()), {}):
                return {str(int(k)).zfill(9): (v.get("streetName", ""), v.get("mapName", "")) for k, v in j.items() if isinstance(v, dict)}
    return {}


# ---------------- .wzjson 解码 ----------------
class WzJson:
    def __init__(self, raw):
        self.raw = raw
        ln = struct.unpack_from("<I", raw, 28)[0]
        self.name = raw[32:32 + ln].decode()
        p = 32 + ((ln + 3) & ~3)
        self.h = h = struct.unpack_from("<36I", raw, p)
        self.B = B = p + 36 * 4
        if raw[B:B + 4] != b"WZJS":
            raise ValueError("不是 WZJS 格式")
        self.n = h[0]
        self.names = self._table(h[26], h[27], h[28])
        self.paths = self._table(h[29], h[30], h[31])
        self.strs = self._table(h[32], h[33], h[34])
        self.ipool = struct.unpack_from("<%di" % h[10], raw, B + h[5])

    def _table(self, cnt, off_bytes, off_idx):
        offs = struct.unpack_from("<%dI" % (cnt + 1), self.raw, self.B + off_idx)
        return [self.raw[self.B + off_bytes + offs[i]:self.B + off_bytes + offs[i + 1]].decode("utf-8", "replace") for i in range(cnt)]

    def rec(self, i):
        return struct.unpack_from("<8i", self.raw, self.B + 8 + 32 * i)

    def node(self, i):
        r = self.rec(i)
        t, v = r[0], r[2]
        if t == 2:
            return {self.names[self.rec(j)[1]]: self.node(j) for j in range(r[3], r[3] + r[4])}
        if t == 6:
            return self.ipool[v]
        if t == 11:
            return self.strs[v]
        if t == 18:
            return {"$canvas": True}
        return {"$type": t, "$raw": list(r[2:])}

    def decode(self):
        return self.node(0)


def load_map(aa_dir, map_id, bundle=None):
    mb = bundle or MapBundle(aa_dir)
    return WzJson(mb.raw(map_id)).decode()


# ---------------- 几何整理 ----------------
def footholds(d):
    """{id: dict(x1,y1,x2,y2,prev,next,layer,group)}"""
    out = {}
    for layer, groups in (d.get("foothold") or {}).items():
        for g, fhs in groups.items():
            for fid, f in fhs.items():
                if isinstance(f, dict) and "x1" in f:
                    out[int(fid)] = dict(f, layer=int(layer), group=int(g))
    return out


def platforms(d):
    """把非竖直的 foothold 沿 prev/next 串成平台链。返回 [dict(ids, x0, x1, y0, y1, layer)]，按 y（上->下）排。"""
    segs = footholds(d)
    walk = {k for k, s in segs.items() if s["x1"] != s["x2"]}
    seen, plats = set(), []
    for fid in sorted(walk):
        if fid in seen:
            continue
        cur, guard = fid, 0
        while segs[cur]["prev"] in walk and segs[cur]["prev"] not in seen and segs[cur]["prev"] != fid and guard < 10000:
            cur = segs[cur]["prev"]; guard += 1
        chain = []
        while cur in walk and cur not in seen:
            seen.add(cur); chain.append(cur); cur = segs[cur]["next"]
        xs = [segs[c]["x1"] for c in chain] + [segs[c]["x2"] for c in chain]
        ys = [segs[c]["y1"] for c in chain] + [segs[c]["y2"] for c in chain]
        plats.append(dict(ids=chain, x0=min(xs), x1=max(xs), y0=min(ys), y1=max(ys), layer=segs[chain[0]]["layer"]))
    plats.sort(key=lambda p: (p["y0"], p["x0"]))
    return plats


def ground_y(d, x, y_hint=None):
    """世界 x 处的地面 y：所有横跨 x 的非竖直 foothold 上按线性插值取 y；给了 y_hint 就取最接近 hint 的那条，否则取最高的（y 最小）。"""
    best = None
    for s in footholds(d).values():
        if s["x1"] == s["x2"]:
            continue
        lo, hi = sorted((s["x1"], s["x2"]))
        if not (lo <= x <= hi):
            continue
        t = (x - s["x1"]) / (s["x2"] - s["x1"])
        y = s["y1"] + t * (s["y2"] - s["y1"])
        key = abs(y - y_hint) if y_hint is not None else y
        if best is None or key < best[0]:
            best = (key, y)
    return None if best is None else best[1]


def summary(d):
    mm = d.get("miniMap") or {}
    info = d.get("info") or {}
    out = {"info": {k: info.get(k) for k in ("VRLeft", "VRRight", "VRTop", "VRBottom", "town", "returnMap", "mapMark", "bgm") if k in info},
           "miniMap": {k: mm.get(k) for k in ("width", "height", "centerX", "centerY", "mag") if k in mm}}
    if mm.get("mag"):
        out["miniMap"]["canvas_px"] = [mm["width"] / mm["mag"], mm["height"] / mm["mag"]]
    fh = footholds(d)
    if fh:
        xs = [v for s in fh.values() for v in (s["x1"], s["x2"])]
        ys = [v for s in fh.values() for v in (s["y1"], s["y2"])]
        out["foothold_bbox"] = [min(xs), min(ys), max(xs), max(ys)]
        out["footholds"] = len(fh)
    out["platforms"] = len(platforms(d))
    out["ladderRope"] = [(v["x"], v["y1"], v["y2"], "ladder" if v.get("l") else "rope") for v in (d.get("ladderRope") or {}).values() if isinstance(v, dict)]
    out["portals"] = [(v.get("pn"), v.get("x"), v.get("y"), v.get("tm")) for v in (d.get("portal") or {}).values() if isinstance(v, dict)]
    mobs = collections.Counter(v["id"] for v in (d.get("life") or {}).values() if isinstance(v, dict) and v.get("type") == "m")
    out["mobs"] = dict(mobs)
    return out


# ---------------- CLI ----------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--aa", default=None, help="客户端 aa/ 目录（默认 config.yaml wz.aa_dir 或仓库根的 aa/）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find", help="按名字/ID 搜地图"); f.add_argument("q", nargs="+")
    e = sub.add_parser("export", help="导出地图 JSON"); e.add_argument("--map", action="append", required=True); e.add_argument("--out", default=os.path.join(ROOT, "calibration", "maps"))
    p = sub.add_parser("platforms", help="打印平台链/绳梯/传送门/刷怪点"); p.add_argument("--map", required=True)
    a = ap.parse_args(argv)
    aa = a.aa or default_aa_dir()
    if not aa:
        print("找不到客户端 aa/ 目录：用 --aa 指定，或在 config.yaml 的 wz.aa_dir 里填"); return 2
    if a.cmd == "find":
        names = map_names(aa)
        mb = MapBundle(aa)
        have = set(mb.ids)
        hits = [(mid, st, nm) for mid, (st, nm) in sorted(names.items()) if any(q in nm or q in st or q == mid.lstrip("0") or q == mid for q in a.q)]
        for mid, st, nm in hits:
            print(f"{mid}  {st} / {nm}" + ("" if mid in have else "   (客户端包里没有地图数据)"))
        print(f"{len(hits)} 张；客户端包里共有 {len(have)} 张图的地图数据")
        return 0
    mb = MapBundle(aa)
    if a.cmd == "export":
        os.makedirs(a.out, exist_ok=True)
        names = map_names(aa)
        for m in a.map:
            mid = str(int(m)).zfill(9)
            d = load_map(aa, mid, mb)
            d["$id"], d["$name"] = mid, " / ".join(names.get(mid, ("", "")))
            fn = os.path.join(a.out, f"{mid}.json")
            json.dump(d, open(fn, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"{mid} {d['$name']} -> {fn}")
            print("  ", json.dumps(summary(d), ensure_ascii=False))
        return 0
    if a.cmd == "platforms":
        d = load_map(aa, a.map, mb)
        s = summary(d)
        print(json.dumps({k: s[k] for k in ("info", "miniMap", "foothold_bbox", "mobs")}, ensure_ascii=False))
        for pl in platforms(d):
            print(f"  y {pl['y0']:5d}~{pl['y1']:5d}  x {pl['x0']:5d}~{pl['x1']:5d}  长 {pl['x1'] - pl['x0']:4d}  段 {len(pl['ids']):2d}  layer {pl['layer']}")
        print("绳/梯 (x, y1, y2):", s["ladderRope"])
        print("传送门 (名, x, y, 目标图):", s["portals"])
        life = [(v["id"], v["x"], v["cy"]) for v in (d.get("life") or {}).values() if isinstance(v, dict) and v.get("type") == "m"]
        print("刷怪点 (id, x, 地面y):", sorted(life, key=lambda t: (t[2], t[1])))
        return 0


if __name__ == "__main__":
    sys.exit(main())

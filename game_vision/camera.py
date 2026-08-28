"""摄像头 / 视频文件 / NDI 网络流 统一读取。

source 三种写法：
- 摄像头编号 / 名称关键字（如 "Insta360"）/ 视频文件路径；
- "ndi:<源名关键字>"：B 机 OBS 开 NDI 输出 → 局域网 → 本机直接收（不经过本机 OBS/虚拟摄像头，
  不需要 v4l2loopback/sudo）。如 "ndi:Game-PC" 匹配 "PLP16S-5YWCJX (Game-PC)"；"ndi:" 取第一个源。
  需要 pip install cyndilib（自带 libndi）。

NDI 注意事项（2026-08-28 实测，B=Windows OBS DistroAV，A=Ubuntu WiFi）：
- libndi 默认优先 RUDP(UDP) 传输，在这张网上握手成功但**一帧视频都收不到**；强制 TCP 后 1080p30 零丢帧。
  所以默认把 ndi_transport=tcp 写进 NDI_CONFIG_DIR/ndi-config.v1.json（.ndi/ 目录，自动生成），
  该环境变量必须在 import cyndilib 之前设置。
- 用 frame_sync 取帧：永远拿最新一帧，处理慢了自动丢旧帧，不会积压延迟。

Linux(V4L2) 注意事项（Ubuntu 迁移时实测，Insta360 Ace Pro 2）：
- 必须先设 MJPG fourcc 再设分辨率，否则 1920x1080 协商偶发 select() timeout 读不到帧；
- 默认像素格式可能落到 YUYV，1080p 只有 5~10fps；
- /dev/videoN 编号随插拔顺序变化，所以 source 允许写摄像头名称关键字（如 "Insta360"）。
"""
import glob
import json
import os
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
NDI_PREFIX = "ndi:"
NDI_CONFIG_DIR = os.path.join(HERE, ".ndi")   # 自动生成，已 gitignore

# Windows 下用 DirectShow 打开摄像头更稳；Linux 显式用 V4L2；Mac 用默认后端 (AVFoundation)
if sys.platform == "win32":
    CAMERA_BACKEND = cv2.CAP_DSHOW
elif sys.platform.startswith("linux"):
    CAMERA_BACKEND = cv2.CAP_V4L2
else:
    CAMERA_BACKEND = cv2.CAP_ANY


def list_cameras():
    """列出本机摄像头 [(index, name)]。Linux 读 sysfs，只保留视频采集节点（跳过元数据节点）；
    其他平台逐个 index 试探。"""
    out = []
    if sys.platform.startswith("linux"):
        for d in sorted(glob.glob("/sys/class/video4linux/video*"), key=lambda p: int(p.rsplit("video", 1)[1])):
            idx = int(d.rsplit("video", 1)[1])
            try:
                with open(os.path.join(d, "name"), encoding="utf-8", errors="replace") as f:
                    name = f.read().strip()
            except OSError:
                continue
            if _is_capture_node(f"/dev/video{idx}"):
                out.append((idx, name))
        return out
    for idx in range(6):
        cap = cv2.VideoCapture(idx, CAMERA_BACKEND)
        if cap.isOpened():
            out.append((idx, f"camera {idx}"))
        cap.release()
    return out


def _is_capture_node(dev):
    """V4L2 VIDIOC_QUERYCAP：device_caps 含 VIDEO_CAPTURE(0x1) 才是能出图的节点。
    UVC 摄像头通常还有一个 META_CAPTURE 节点（如 video3），不能用来取帧。"""
    import ctypes
    import fcntl

    class Cap(ctypes.Structure):
        _fields_ = [("driver", ctypes.c_char * 16), ("card", ctypes.c_char * 32), ("bus_info", ctypes.c_char * 32),
                    ("version", ctypes.c_uint32), ("capabilities", ctypes.c_uint32), ("device_caps", ctypes.c_uint32),
                    ("reserved", ctypes.c_uint32 * 3)]

    try:
        fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        c = Cap()
        fcntl.ioctl(fd, 0x80685600, c)  # VIDIOC_QUERYCAP
        return bool(c.device_caps & 0x1)
    except OSError:
        return False
    finally:
        os.close(fd)


def is_ndi_source(source):
    return isinstance(source, str) and source.strip().lower().startswith(NDI_PREFIX)


def _ndi_prepare(transport="tcp"):
    """设置 libndi 传输方式并导入 cyndilib。transport: tcp（推荐，实测唯一能出图的）/ auto（libndi 默认，优先 RUDP）。
    用户自己设了 NDI_CONFIG_DIR 则不动。"""
    if transport and transport != "auto" and "NDI_CONFIG_DIR" not in os.environ:
        en = {"rudp": transport == "rudp", "tcp": transport == "tcp", "unicast": transport == "unicast"}
        cfg = {"ndi": {k: {"send": {"enable": v}, "recv": {"enable": v}} for k, v in en.items()}}
        cfg["ndi"]["multicast"] = {"send": {"enable": False}, "recv": {"enable": False}}
        os.makedirs(NDI_CONFIG_DIR, exist_ok=True)
        with open(os.path.join(NDI_CONFIG_DIR, "ndi-config.v1.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        os.environ["NDI_CONFIG_DIR"] = NDI_CONFIG_DIR
    try:
        import cyndilib  # noqa: F401
    except ImportError as e:
        raise RuntimeError("NDI 源需要 cyndilib：pip install cyndilib") from e


def list_ndi_sources(timeout=3.0, transport="tcp"):
    """局域网上可见的 NDI 源名字列表（等最多 timeout 秒）。"""
    _ndi_prepare(transport)
    from cyndilib.finder import Finder
    f = Finder()
    f.open()
    try:
        t_end = time.time() + timeout
        while time.time() < t_end:
            f.wait_for_sources(timeout=1.0)
            names = list(f.get_source_names())
            if names:
                return names
        return list(f.get_source_names())
    finally:
        f.close()


class _NDIReceiver:
    """按名字关键字连一个 NDI 源，read() 返回最新的 BGR 帧（只在有新帧时返回）。"""

    def __init__(self, keyword, transport="tcp", timeout=8.0):
        _ndi_prepare(transport)
        from cyndilib.finder import Finder
        from cyndilib.receiver import Receiver
        from cyndilib.video_frame import VideoFrameSync
        from cyndilib.wrapper.ndi_recv import RecvBandwidth, RecvColorFormat

        key = keyword.strip().lower()
        self.finder = Finder()
        self.finder.open()
        name, names = None, []
        t_end = time.time() + timeout
        while name is None and time.time() < t_end:
            self.finder.wait_for_sources(timeout=1.0)
            names = list(self.finder.get_source_names())
            hits = [n for n in names if key in n.lower()] if key else names
            if hits:
                name = hits[0]
        if name is None:
            self.finder.close()
            raise RuntimeError(f"找不到 NDI 源 '{keyword}'；当前可见: {', '.join(names) if names else '无'}"
                               "（确认 B 机 OBS 已开启 NDI 输出、两台机器在同一局域网）")
        self.name = name
        self.recv = Receiver(color_format=RecvColorFormat.BGRX_BGRA, bandwidth=RecvBandwidth.highest,
                             recv_name="game_vision")
        self.vf = VideoFrameSync()
        self.recv.frame_sync.set_video_frame(self.vf)
        self.recv.set_source(self.finder.get_source(name))
        self._last_ts = None
        ok, frame = self.read(timeout=timeout)
        if not ok:
            self.release()
            raise RuntimeError(f"NDI 源 '{name}' 已连接但 {timeout:.0f}s 内没有视频帧"
                               "（B 机 OBS 是否在推流？ndi_transport 是否为 tcp？）")
        self.height, self.width = frame.shape[:2]
        try:
            self.fps = float(self.vf.get_frame_rate()) or 30.0
        except (ZeroDivisionError, TypeError, ValueError):
            self.fps = 30.0

    def read(self, timeout=3.0):
        """等到一帧“新”的视频（时间戳变化）再返回；超过 timeout 秒没有新帧返回 (False, None)。"""
        t_end = time.perf_counter() + timeout
        while True:
            self.recv.frame_sync.capture_video()
            if self.vf.xres > 0 and self.vf.yres > 0:
                ts = self.vf.get_timestamp_posix()
                if ts != self._last_ts:
                    self._last_ts = ts
                    a = np.asarray(self.vf.get_array(), dtype=np.uint8).reshape(self.vf.yres, self.vf.xres, 4)
                    return True, cv2.cvtColor(a, cv2.COLOR_BGRA2BGR)   # BGRX -> BGR（cvtColor 比 numpy 切片拷贝快 3 倍；同时脱离 NDI 内部缓冲）
                # 还是同一帧：这次 capture_video 也向 SDK 拿了一个帧引用（NDIlib_framesync_capture_video 必须配对 free_video），
                # 而 cyndilib 只在缓冲区视图释放时才 free（get_array 内部会做，这里没取数据就不会）。不释放 = 每帧漏 8 MB，
                # 30 fps 一分多钟吃光 16 GB + 4 GB swap，进程假死后被 OOM 杀掉（2026-08-28 三次）。memoryview 进出一次即触发释放。
                memoryview(self.vf).release()
            if time.perf_counter() >= t_end:
                return False, None
            time.sleep(0.002)

    def release(self):
        try:
            self.recv.disconnect()
        except Exception:
            pass
        try:
            self.finder.close()
        except Exception:
            pass


def resolve_source(source):
    """把配置里的 source 规整为 int(摄像头编号) 或 str(视频文件路径)。
    支持：整数 / 数字字符串 / 文件路径 / 摄像头名称关键字（不区分大小写，取第一个匹配的采集节点）。"""
    if isinstance(source, int):
        return source
    s = str(source).strip()
    if s.isdigit():
        return int(s)
    if os.path.exists(s):
        return s
    key = s.lower()
    for idx, name in list_cameras():
        if key in name.lower():
            return idx
    return s  # 交给 VideoCapture 报错


def _try_open(index, width, height, reads=1):
    """打开并协商一次；能出帧返回 cap，否则返回 None（已 release）。"""
    cap = cv2.VideoCapture(index, CAMERA_BACKEND)
    if not cap.isOpened():
        cap.release()
        return None
    if sys.platform != "win32":
        # V4L2 下 fourcc 必须在分辨率之前设置（见文件头注释）
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    # 尽量关闭自动对焦，减少画面漂移（不同驱动支持程度不同；Insta360 UVC 只暴露 Brightness）
    cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)
    # 预热：确认真的能出帧。V4L2 协商失败时每次 read() 会等 ~10s 超时，所以次数要少
    for _ in range(reads):
        ok, _frame = cap.read()
        if ok:
            return cap
    cap.release()
    return None


RESET_SIZE = (1280, 720)


def open_camera(index, width, height):
    """打开摄像头并完成协商，返回已能出帧的 cv2.VideoCapture；失败抛 RuntimeError。

    实测（Insta360 Ace Pro 2, Ubuntu）：1080p MJPG 偶发协商卡死（read() select() timeout），
    用另一个分辨率打开一次再切回来就恢复。所以失败后自动做一次"复位"再重试。"""
    cams = None
    cap = _try_open(index, width, height)
    if cap is not None:
        return cap
    if not cv2.VideoCapture(index, CAMERA_BACKEND).isOpened():
        cams = list_cameras()
        hint = "；可用摄像头: " + ", ".join(f"{i}={n}" for i, n in cams) if cams else ""
        raise RuntimeError(f"无法打开摄像头 {index}{hint}")
    print(f"[camera] {width}x{height} 协商失败，用 {RESET_SIZE[0]}x{RESET_SIZE[1]} 复位后重试 ……")
    reset = _try_open(index, *RESET_SIZE)
    if reset is not None:
        reset.release()
    cap = _try_open(index, width, height)
    if cap is not None:
        print("[camera] 复位后恢复正常")
        return cap
    raise RuntimeError(f"摄像头 {index} 已打开但读不到帧（协商 {width}x{height} 失败，复位重试也失败）。"
                       f"请重新插拔摄像头，或在设置里把分辨率改为 {RESET_SIZE[0]}x{RESET_SIZE[1]}")


def open_writer(path_no_ext, fps, size):
    """创建录像写入器：优先 mp4v，失败则降级 MJPG/avi。返回 (writer, path)。"""
    fps = fps if (fps and 1 <= fps <= 120) else 30.0
    for fourcc, ext in (("mp4v", ".mp4"), ("MJPG", ".avi")):
        path = path_no_ext + ext
        w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*fourcc), fps, size)
        if w.isOpened():
            return w, path
        w.release()
    raise RuntimeError("无法创建录像写入器（mp4v/MJPG 都失败）")


class FrameSource:
    def __init__(self, source, width=1920, height=1080, loop_video=False, ndi_transport="tcp"):
        self.frame_index = -1
        self._t_last = time.perf_counter()
        self._fps_est = 0.0
        self.ndi = None
        self.cap = None
        if is_ndi_source(source):
            self.is_file, self.loop_video = False, False
            self.ndi = _NDIReceiver(str(source).strip()[len(NDI_PREFIX):], ndi_transport)
            self.fps, self.width, self.height = self.ndi.fps, self.ndi.width, self.ndi.height
            print(f"[ndi] 已连接 '{self.ndi.name}' {self.width}x{self.height} @{self.fps:.0f}fps 传输={ndi_transport}")
            return
        source = resolve_source(source)
        self.is_file = isinstance(source, str)
        self.loop_video = loop_video
        if not self.is_file:
            self.cap = open_camera(source, width, height)
        else:
            self.cap = cv2.VideoCapture(source)
        if not self.cap.isOpened():
            raise RuntimeError(f"无法打开视频源: {source}")
        fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.fps = fps if (fps and 1 <= fps <= 120) else 30.0  # 摄像头常返回 -1/0
        self.width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    def read(self):
        if self.ndi is not None:
            ok, frame = self.ndi.read()
        else:
            ok, frame = self.cap.read()
            if not ok and self.is_file and self.loop_video:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                self.frame_index = -1
                ok, frame = self.cap.read()
        if ok:
            self.frame_index += 1
            now = time.perf_counter()
            dt = now - self._t_last
            self._t_last = now
            if dt > 0:
                self._fps_est = 0.9 * self._fps_est + 0.1 / dt if self._fps_est else 1 / dt
        return ok, frame

    def seek(self, index):
        if self.cap is None:
            return
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        self.frame_index = index - 1

    @property
    def total_frames(self):
        return int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT)) if self.is_file else -1

    @property
    def measured_fps(self):
        return self._fps_est

    def release(self):
        if self.ndi is not None:
            self.ndi.release()
        else:
            self.cap.release()

"""摄像头 / 视频文件统一读取。

Linux(V4L2) 注意事项（Ubuntu 迁移时实测，Insta360 Ace Pro 2）：
- 必须先设 MJPG fourcc 再设分辨率，否则 1920x1080 协商偶发 select() timeout 读不到帧；
- 默认像素格式可能落到 YUYV，1080p 只有 5~10fps；
- /dev/videoN 编号随插拔顺序变化，所以 source 允许写摄像头名称关键字（如 "Insta360"）。
"""
import glob
import os
import sys
import time

import cv2

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
    def __init__(self, source, width=1920, height=1080, loop_video=False):
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
        self.frame_index = -1
        self._t_last = time.perf_counter()
        self._fps_est = 0.0

    def read(self):
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
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        self.frame_index = index - 1

    @property
    def total_frames(self):
        return int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT)) if self.is_file else -1

    @property
    def measured_fps(self):
        return self._fps_est

    def release(self):
        self.cap.release()

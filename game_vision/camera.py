"""摄像头 / 视频文件统一读取。"""
import sys
import time

import cv2

# Windows 下用 DirectShow 打开摄像头更稳；Linux/Mac 用默认后端 (V4L2/AVFoundation)
CAMERA_BACKEND = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY


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
        self.is_file = isinstance(source, str) and not source.isdigit()
        self.loop_video = loop_video
        if not self.is_file:
            source = int(source)
            self.cap = cv2.VideoCapture(source, CAMERA_BACKEND)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            # 尽量关闭自动曝光/对焦，减少画面漂移（不同驱动支持程度不同）
            self.cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)
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

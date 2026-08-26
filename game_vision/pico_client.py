"""A 端 -> Pico 2 W 的 UDP 客户端（P1）。

作为库：
    from pico_client import PicoClient
    pc = PicoClient()            # host 省略则在局域网内自动发现
    pc.start_heartbeat()
    pc.key_press("x")
    pc.release_all(); pc.close()

作为命令行测试工具：
    python pico_client.py                 # 自动发现 Pico，进入交互模式
    python pico_client.py --host 192.168.1.50
    python pico_client.py --selftest      # 自动跑一遍测试序列（等同 P0 的动作）
交互模式下直接输入协议命令（如 KEY_PRESS x / MOUSE_MOVE 100 0），
输入 selftest 跑测试序列，quit 退出（退出时自动 RELEASE_ALL）。
"""
import argparse
import socket
import sys
import threading
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_PORT = 5000


def discover(port=DEFAULT_PORT, timeout=3.0):
    """向局域网广播 PING，返回第一个回复 PONG 的 Pico 的 IP，找不到返回 None。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(0.5)
    t_end = time.time() + timeout
    while time.time() < t_end:
        try:
            s.sendto(b"PING", ("255.255.255.255", port))
            data, addr = s.recvfrom(256)
            if data.startswith(b"PONG"):
                s.close()
                return addr[0]
        except socket.timeout:
            continue
        except OSError:
            break
    s.close()
    return None


class PicoClient:
    def __init__(self, host=None, port=DEFAULT_PORT, heartbeat_ms=500):
        if host is None:
            print("[pico] 正在局域网内查找 Pico ……")
            host = discover(port)
            if host is None:
                raise RuntimeError("未发现 Pico：确认它已连上同一 Wi-Fi（LED 常亮），或用 host 参数指定 IP")
            print(f"[pico] 发现 Pico: {host}")
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(1.0)
        self.heartbeat_ms = heartbeat_ms
        self._hb_stop = threading.Event()
        self._hb_thread = None
        self._lock = threading.Lock()

    # ---- 基础 ----
    def send(self, cmd, wait_reply=True):
        with self._lock:
            self.sock.sendto(cmd.encode("utf-8"), self.addr)
            if not wait_reply:
                return None
            try:
                data, _ = self.sock.recvfrom(256)
                return data.decode("utf-8", "replace")
            except socket.timeout:
                return None

    def ping(self):
        return (self.send("PING") or "").startswith("PONG")

    def start_heartbeat(self):
        """后台每 heartbeat_ms 发一次 PING，维持 Pico 的心跳（否则它会 RELEASE_ALL）。"""
        if self._hb_thread:
            return

        def loop():
            while not self._hb_stop.wait(self.heartbeat_ms / 1000):
                try:
                    self.send("PING", wait_reply=False)
                except OSError:
                    pass

        self._hb_thread = threading.Thread(target=loop, daemon=True)
        self._hb_thread.start()

    # ---- 键盘 ----
    def key_down(self, key):
        return self.send(f"KEY_DOWN {key}")

    def key_up(self, key):
        return self.send(f"KEY_UP {key}")

    def key_press(self, key, ms=50):
        return self.send(f"KEY_PRESS {key} {ms}")

    # ---- 鼠标 ----
    def mouse_move(self, dx, dy):
        return self.send(f"MOUSE_MOVE {dx} {dy}")

    def mouse_click(self, button="left"):
        return self.send(f"MOUSE_{button.upper()}_CLICK")

    def mouse_scroll(self, n):
        return self.send(f"MOUSE_SCROLL {n}")

    def release_all(self):
        return self.send("RELEASE_ALL")

    def close(self):
        try:
            self.release_all()
        except OSError:
            pass
        self._hb_stop.set()
        self.sock.close()


def selftest(pc):
    print("测试序列开始：3 秒内把光标放进记事本 ……")
    time.sleep(3)
    for ch in "pico p1 ok ":
        pc.key_press("space" if ch == " " else ch)
        time.sleep(0.05)
    pc.key_down("shift")
    pc.key_press("x")
    pc.key_up("shift")
    for dx, dy in ((100, 0), (0, 100), (-100, 0), (0, -100)):
        pc.mouse_move(dx, dy)
        time.sleep(0.2)
    pc.mouse_click("left")
    pc.mouse_scroll(-1)
    pc.release_all()
    print("测试序列结束（记事本应出现 pico p1 ok X，鼠标画了正方形）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=None, help="Pico 的 IP，省略则自动发现")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    pc = PicoClient(args.host, args.port)
    if not pc.ping():
        print("[pico] PING 无回复（不影响发送，但请检查网络）")
    else:
        print("[pico] PING -> PONG 正常")
    pc.start_heartbeat()
    try:
        if args.selftest:
            selftest(pc)
            return
        print("交互模式：直接输入协议命令（如 KEY_PRESS x / MOUSE_MOVE 100 0），selftest 跑测试，quit 退出")
        while True:
            try:
                line = input("pico> ").strip()
            except EOFError:
                break
            if not line:
                continue
            if line.lower() in ("quit", "exit", "q"):
                break
            if line.lower() == "selftest":
                selftest(pc)
                continue
            print(pc.send(line))
    finally:
        pc.close()
        print("已退出并 RELEASE_ALL")


if __name__ == "__main__":
    main()

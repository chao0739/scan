# P1: Pico 2 W — Wi-Fi UDP 命令 -> USB HID 键鼠，带心跳超时保护
#
# 协议（UTF-8 文本，一条命令一个 UDP 包，大小写不敏感）：
#   PING                    -> 回复 "PONG <board>"（也用作心跳）
#   KEY_DOWN <key>          按下并保持
#   KEY_UP <key>            释放
#   KEY_PRESS <key> [ms]    按下-等待 ms（默认 50）-释放
#   MOUSE_MOVE <dx> <dy>    相对移动（自动拆分为多次 HID report）
#   MOUSE_LEFT_DOWN / MOUSE_LEFT_UP / MOUSE_LEFT_CLICK
#   MOUSE_RIGHT_DOWN / MOUSE_RIGHT_UP / MOUSE_RIGHT_CLICK
#   MOUSE_SCROLL <n>        滚轮，正=向上
#   RELEASE_ALL             释放全部按键和鼠标键
#
# 安全：超过 TIMEOUT_MS 没收到任何包 -> 自动 RELEASE_ALL（LED 快闪提示）。
#
# v2（2026-08-27，A 端真机实测后改）：v1 每圈只收 1 个包、KEY_PRESS 期间 sleep 不收包、每圈查一次 Wi-Fi 状态，
#   结果连发 2 个包只能收到 1 个、5 个连发全丢。v2：每圈把待收的包全部收完；KEY_PRESS 改成定时释放不阻塞；
#   Wi-Fi 状态每 1 s 查一次；socket 超时 5 ms。协议不变。A 端 pico_client 仍按 ≥60 ms 节拍发包（两边都留余量）。
#   **改了固件要重新把本文件拷到 CIRCUITPY 盘**（当前 Pico 上烧的是 v1 还是 v2 看串口启动打印的版本号）。
# LED：快闪=连 Wi-Fi 中；常亮=就绪；收到命令时短灭一下；超时保护中=慢闪。
#
# Wi-Fi 帐号密码写在 CIRCUITPY 盘根目录的 settings.toml（见 settings.toml.example）。
import os
import time

import board
import digitalio
import usb_hid
import wifi
import socketpool
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode
from adafruit_hid.mouse import Mouse

VERSION = "p1-v2"
PORT = int(os.getenv("PICO_PORT", "5000"))
TIMEOUT_MS = int(os.getenv("PICO_TIMEOUT_MS", "1500"))

led = digitalio.DigitalInOut(board.LED)
led.direction = digitalio.Direction.OUTPUT
kbd = Keyboard(usb_hid.devices)
mouse = Mouse(usb_hid.devices)

KEYS = {
    "space": Keycode.SPACE, "enter": Keycode.ENTER, "esc": Keycode.ESCAPE,
    "tab": Keycode.TAB, "backspace": Keycode.BACKSPACE,
    "up": Keycode.UP_ARROW, "down": Keycode.DOWN_ARROW,
    "left": Keycode.LEFT_ARROW, "right": Keycode.RIGHT_ARROW,
    "ctrl": Keycode.CONTROL, "shift": Keycode.SHIFT, "alt": Keycode.ALT,
    "win": Keycode.GUI, "home": Keycode.HOME, "end": Keycode.END,
    "pageup": Keycode.PAGE_UP, "pagedown": Keycode.PAGE_DOWN,
    "insert": Keycode.INSERT, "delete": Keycode.DELETE,
}
for i, name in enumerate("abcdefghijklmnopqrstuvwxyz"):
    KEYS[name] = Keycode.A + i
for i in range(1, 10):
    KEYS[str(i)] = Keycode.ONE + i - 1
KEYS["0"] = Keycode.ZERO
for i in range(1, 13):
    KEYS[f"f{i}"] = Keycode.F1 + i - 1


pending_release = []   # [(release_at_monotonic, keycode)]：KEY_PRESS 的定时释放（不阻塞收包）


def release_all():
    pending_release.clear()
    kbd.release_all()
    mouse.release_all()


def service_pending(now):
    """到点的 KEY_PRESS 释放。"""
    if not pending_release:
        return
    keep = []
    for t, code in pending_release:
        if now >= t:
            kbd.release(code)
        else:
            keep.append((t, code))
    pending_release[:] = keep


def clamp8(v):
    return max(-127, min(127, v))


def mouse_move(dx, dy):
    while dx or dy:
        sx, sy = clamp8(dx), clamp8(dy)
        mouse.move(x=sx, y=sy)
        dx -= sx
        dy -= sy


def handle(cmd, parts):
    """返回回复字符串或 None。未知命令/参数错误抛 ValueError。"""
    if cmd == "PING":
        return "PONG pico2w " + VERSION
    if cmd == "RELEASE_ALL":
        release_all()
        return "OK"
    if cmd in ("KEY_DOWN", "KEY_UP", "KEY_PRESS"):
        code = KEYS.get(parts[1].lower())
        if code is None:
            raise ValueError("unknown key " + parts[1])
        if cmd == "KEY_DOWN":
            kbd.press(code)
        elif cmd == "KEY_UP":
            kbd.release(code)
        else:
            ms = int(parts[2]) if len(parts) > 2 else 50
            kbd.press(code)
            pending_release.append((time.monotonic() + min(ms, 2000) / 1000, code))   # 定时释放，期间继续收包
        return "OK"
    if cmd == "MOUSE_MOVE":
        mouse_move(int(parts[1]), int(parts[2]))
        return "OK"
    if cmd == "MOUSE_LEFT_DOWN":
        mouse.press(Mouse.LEFT_BUTTON)
        return "OK"
    if cmd == "MOUSE_LEFT_UP":
        mouse.release(Mouse.LEFT_BUTTON)
        return "OK"
    if cmd == "MOUSE_LEFT_CLICK":
        mouse.click(Mouse.LEFT_BUTTON)
        return "OK"
    if cmd == "MOUSE_RIGHT_DOWN":
        mouse.press(Mouse.RIGHT_BUTTON)
        return "OK"
    if cmd == "MOUSE_RIGHT_UP":
        mouse.release(Mouse.RIGHT_BUTTON)
        return "OK"
    if cmd == "MOUSE_RIGHT_CLICK":
        mouse.click(Mouse.RIGHT_BUTTON)
        return "OK"
    if cmd == "MOUSE_SCROLL":
        mouse.move(wheel=max(-8, min(8, int(parts[1]))))
        return "OK"
    raise ValueError("unknown cmd " + cmd)


# ---- 连 Wi-Fi ----
ssid = os.getenv("CIRCUITPY_WIFI_SSID")
pwd = os.getenv("CIRCUITPY_WIFI_PASSWORD")
print("connecting to", ssid)
while not wifi.radio.connected:
    try:
        wifi.radio.connect(ssid, pwd)
    except Exception as e:
        print("wifi retry:", e)
    for _ in range(6):   # 连接期间快闪
        led.value = not led.value
        time.sleep(0.1)
print("IP:", wifi.radio.ipv4_address)

pool = socketpool.SocketPool(wifi.radio)
sock = pool.socket(pool.AF_INET, pool.SOCK_DGRAM)
sock.bind(("0.0.0.0", PORT))
sock.settimeout(0.005)
led.value = True
print("listening UDP", PORT, VERSION)

buf = bytearray(256)
last_rx = time.monotonic()
safe_released = False
last_wifi_check = time.monotonic()

while True:
    now = time.monotonic()
    # 把这一圈里能收的包全收完（v1 每圈只收 1 个，连发的包会丢）
    got_any = False
    for _ in range(8):
        try:
            n, addr = sock.recvfrom_into(buf)
        except OSError:          # 超时无包
            break
        if not n:
            break
        got_any = True
        try:
            line = bytes(buf[:n]).decode("utf-8").strip()
            parts = line.split()
            if parts:
                reply = handle(parts[0].upper(), parts)
                if reply:
                    sock.sendto(reply.encode(), addr)
        except Exception as e:
            print("bad cmd:", e)
            try:
                sock.sendto(("ERR " + str(e)).encode(), addr)
            except Exception:
                pass
    if got_any:
        last_rx = now
        safe_released = False
        led.value = False    # 收包时短灭
    else:
        led.value = True
    service_pending(now)

    # ---- 心跳超时保护 ----
    if not safe_released and (now - last_rx) * 1000 > TIMEOUT_MS:
        release_all()
        safe_released = True
        print("timeout -> RELEASE_ALL")
    if safe_released:        # 保护状态：慢闪
        led.value = (int(now * 2) % 2) == 0

    if now - last_wifi_check < 1.0:   # Wi-Fi 状态每秒查一次（每圈查会拖慢收包）
        continue
    last_wifi_check = now
    if not wifi.radio.connected:   # Wi-Fi 掉线：释放并重连
        release_all()
        print("wifi lost, reconnecting")
        while not wifi.radio.connected:
            try:
                wifi.radio.connect(ssid, pwd)
            except Exception:
                time.sleep(1)
        led.value = True

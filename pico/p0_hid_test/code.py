# P0: Pico 2 W 作为 USB 复合 HID（键盘+鼠标）单机验证
# 现象：上电后 LED 慢闪 10 秒（准备时间，请把光标放进记事本），
#       然后执行一轮测试序列，结束后 LED 常亮、释放所有按键。
# 测试序列：
#   1. 键盘逐字输入 "pico p0 ok"
#   2. 按住 SHIFT 输入 "x"（应出现大写 X，验证修饰键）
#   3. 鼠标画一个正方形（右-下-左-上，每边 100px）
#   4. 鼠标左键单击一次
#   5. 滚轮向下 1 格
#   6. RELEASE_ALL
import time

import board
import digitalio
import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode
from adafruit_hid.mouse import Mouse

led = digitalio.DigitalInOut(board.LED)
led.direction = digitalio.Direction.OUTPUT

kbd = Keyboard(usb_hid.devices)
mouse = Mouse(usb_hid.devices)

KEYMAP = {
    "a": Keycode.A, "b": Keycode.B, "c": Keycode.C, "d": Keycode.D, "e": Keycode.E,
    "f": Keycode.F, "g": Keycode.G, "h": Keycode.H, "i": Keycode.I, "j": Keycode.J,
    "k": Keycode.K, "l": Keycode.L, "m": Keycode.M, "n": Keycode.N, "o": Keycode.O,
    "p": Keycode.P, "q": Keycode.Q, "r": Keycode.R, "s": Keycode.S, "t": Keycode.T,
    "u": Keycode.U, "v": Keycode.V, "w": Keycode.W, "x": Keycode.X, "y": Keycode.Y,
    "z": Keycode.Z, "0": Keycode.ZERO, "1": Keycode.ONE, "2": Keycode.TWO,
    "3": Keycode.THREE, "4": Keycode.FOUR, "5": Keycode.FIVE, "6": Keycode.SIX,
    "7": Keycode.SEVEN, "8": Keycode.EIGHT, "9": Keycode.NINE, " ": Keycode.SPACE,
}


def type_text(text, delay=0.05):
    for ch in text:
        code = KEYMAP.get(ch.lower())
        if code is None:
            continue
        kbd.press(code)
        time.sleep(delay)
        kbd.release_all()
        time.sleep(delay)


def blink(seconds, period=0.5):
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        led.value = not led.value
        time.sleep(period)
    led.value = False


# ---- 准备期：慢闪 10 秒，把光标放进记事本 ----
blink(10, 0.5)

# ---- 测试序列 ----
led.value = True
type_text("pico p0 ok ")

kbd.press(Keycode.SHIFT, Keycode.X)   # 修饰键组合 -> 大写 X
time.sleep(0.05)
kbd.release_all()
time.sleep(0.3)

for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)):   # 正方形，每边 100px 分 20 步
    for _ in range(20):
        mouse.move(x=dx * 5, y=dy * 5)
        time.sleep(0.01)
    time.sleep(0.2)

mouse.click(Mouse.LEFT_BUTTON)
time.sleep(0.3)
mouse.move(wheel=-1)

# ---- RELEASE_ALL ----
kbd.release_all()
mouse.release_all()

# 结束：LED 常亮。再次测试请按 Pico 上的 RESET（拔插 USB 亦可）。
while True:
    led.value = True
    time.sleep(1)

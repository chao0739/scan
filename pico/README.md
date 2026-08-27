# Pico 2 W 固件（CircuitPython 原型）

对应开发规格 v0.2 的 P0/P1 阶段。当前阶段：**P0 —— USB 复合 HID（键盘+鼠标）单机验证**。

## 一次性准备：刷 CircuitPython

1. 下载 CircuitPython 固件（UF2）：
   打开 https://circuitpython.org/board/raspberry_pi_pico2_w/ ，下载最新稳定版 `.uf2`。
   **注意必须是 "Pico 2 W" 页面的固件**（RP2350 + Wi-Fi），不要下成 Pico / Pico W / Pico 2 的。
2. 按住 Pico 上的 **BOOTSEL** 键不放，插入 USB 线到电脑，松开按键。
   电脑会出现一个叫 `RP2350` 的 U 盘。
3. 把下载的 `.uf2` 文件拖进这个 U 盘。Pico 会自动重启，重新出现一个叫 **CIRCUITPY** 的 U 盘。

## 一次性准备：安装 adafruit_hid 库

1. 下载 Adafruit CircuitPython Bundle：
   https://circuitpython.org/libraries → 下载与固件大版本一致的 `adafruit-circuitpython-bundle-9.x-mpy-xxxx.zip`。
2. 解压后，把其中的 `lib/adafruit_hid/` 整个文件夹复制到 CIRCUITPY 盘的 `lib/` 目录下。

## P0：烧录并测试

1. 把 `p0_hid_test/code.py` 复制到 CIRCUITPY 盘根目录（覆盖原有 code.py）。
2. Pico 自动重启，板载 LED 开始**慢闪 10 秒**——在这期间打开记事本并点一下让光标进去。
3. 观察测试序列：
   - 记事本出现 `pico p0 ok X`（最后的大写 X 验证 SHIFT 修饰键）
   - 鼠标画一个正方形
   - 左键单击一次、滚轮向下一格
   - LED 常亮 = 序列结束，所有按键已释放
4. 再测一次：拔插 USB 即可。

### 验收标准（规格 P0）
- [ ] 设备管理器中出现 HID 键盘 + HID 鼠标（无需安装任何驱动）
- [ ] 键盘输入、修饰键、鼠标相对移动、左键点击、滚轮全部生效
- [ ] 序列结束后没有任何按键保持按下（记事本里不会持续出字）

### 已知限制
- CircuitPython 原型用于快速验证；正式版按规格评估是否切换 Pico SDK + TinyUSB（C）。
- 测试序列在上电 10 秒后自动执行，期间请勿把光标放在重要窗口里。
- CIRCUITPY 盘和 USB 串口默认可见（方便开发）；正式版会关闭，只暴露 HID。

## 故障排查
- **插上只有 RP2350 盘**：还没刷 CircuitPython，按上面步骤刷入。
- **没有出现 CIRCUITPY**：换一根确认支持数据的 USB 线（纯充电线不行）。
- **code.py 报错**：CIRCUITPY 盘会出现 `boot_out.txt` / 串口输出错误信息；最常见是 `lib/adafruit_hid` 没放对位置。
- **记事本没出字但 LED 正常**：检查测试期间光标是否在文本框内。

## P1：Wi-Fi 命令 -> HID

1. 把 `p1_wifi_hid/settings.toml.example` 复制到 CIRCUITPY 盘根目录并改名为 `settings.toml`，
   填入你的 Wi-Fi 名称和密码（**必须是 2.4GHz 网络**，Pico 2 W 不支持 5GHz）。
2. 把 `p1_wifi_hid/code.py` 复制到 CIRCUITPY 盘根目录（覆盖 P0 的 code.py）。
3. 观察 LED：快闪 = 正在连 Wi-Fi；**常亮 = 已就绪**。
4. 在电脑上测试（电脑需和 Pico 在同一 Wi-Fi/局域网）：
   ```bash
   cd ../game_vision
   python pico_client.py --selftest
   ```
   程序会自动在局域网内发现 Pico（无需查 IP），3 秒内把光标放进记事本，
   应看到与 P0 相同的动作序列：输入 `pico p1 ok X`、鼠标画正方形、单击、滚轮。
5. 交互模式：`python pico_client.py`，直接敲协议命令（如 `KEY_PRESS x`、`MOUSE_MOVE 100 0`）。

### LED 状态含义（P1 固件）
| LED | 含义 |
|---|---|
| 快闪 | 连接 Wi-Fi 中 |
| 常亮 | 就绪，等待命令 |
| 慢闪 | 心跳超时，已自动 RELEASE_ALL（A 端恢复发包后回到常亮） |

### 验收标准（规格 P1）
- [ ] `pico_client.py --selftest` 动作全部正确
- [ ] 把 A 端程序直接关掉（模拟崩溃），按住的键在 1.5 秒内自动释放（LED 转慢闪）
- [ ] 断开 Wi-Fi 路由或让 Pico 掉线，不出现卡键；恢复后能重连
- [ ] 发送非法命令（如 `KEY_DOWN foo`）返回 ERR 且不影响后续命令

### 协议（UDP 文本，端口 5000）
```
PING                      -> PONG pico2w（也用作心跳，A 端每 500ms 发一次）
KEY_DOWN / KEY_UP <key>   key: a-z 0-9 f1-f12 space enter esc tab ctrl shift alt
                               win up down left right home end pageup pagedown insert delete backspace
KEY_PRESS <key> [ms]
MOUSE_MOVE <dx> <dy>      相对移动，自动拆分大位移
MOUSE_LEFT_DOWN/UP/CLICK  MOUSE_RIGHT_DOWN/UP/CLICK
MOUSE_SCROLL <n>          正=向上
RELEASE_ALL
```

### P1 v2（2026-08-27）—— 需要重新烧录
真机实测发现 v1 固件**每圈只收一个 UDP 包、KEY_PRESS 期间 sleep 不收包**：连发 2 个包只收到 1 个，5 个连发一个都收不到。
A 端 bot 换向时发 `KEY_UP + KEY_DOWN`，后一个必丢，表现为角色"卡住"/走走停停/背对怪。
- A 端已改：`pico_client.py` 所有控制命令进队列按 ≥60 ms 节拍单发（对 v1 固件也有效，已真机验证）。
- 固件 v2（本目录 `p1_wifi_hid/code.py`）：每圈收完全部待收包、KEY_PRESS 定时释放不阻塞、Wi-Fi 状态每秒查一次。协议不变。
  烧录：把 `code.py` 拷到 CIRCUITPY 盘覆盖即可；`PING` 回复带版本号 `PONG pico2w p1-v2` 可确认。
  烧完可用 `python -c "from pico_client import *; ..."` 或交互模式连发几个 PING 验证不丢包。

### 卡键验证方法（重要）
交互模式里输入 `KEY_DOWN w`（此时记事本会持续出 w），然后直接关掉命令行窗口——
1.5 秒内应停止出字（Pico 超时自动 RELEASE_ALL）。这是规格 §6.2 的核心安全要求。

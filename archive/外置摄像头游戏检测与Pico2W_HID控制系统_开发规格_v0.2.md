<!-- development_spec_v0.2.md -->

**外置摄像头游戏目标检测与 Pico 2 W HID 控制系统**

**开发需求与系统架构规格 v0.2**

*用途：作为 AI Coding Agent / 开发者的统一实现依据*

| **当前硬件状态** | Raspberry Pi Pico 2 W 已购买                                 |
|------------------|--------------------------------------------------------------|
| **目标平台**     | A 电脑：视觉/决策；Pico 2 W：Wi-Fi + USB HID；B 电脑：被控端 |
| **视觉范围**     | 2D 横版游戏；玩家前方有限 ROI                                |
| **检测目标**     | 只判断 ROI 内是否存在目标/怪物，不要求识别类别               |
| **核心原则**     | 先做最小可用闭环；不默认引入 YOLO、深度学习或复杂 UI         |

> **一句话架构：**摄像头观察 B 电脑游戏画面；A 电脑完成画面校正、固定 ROI 检测和决策；A 通过 Wi-Fi 向 Pico 2 W 发送高级控制命令；Pico 通过同一根 USB 数据线向 B 电脑输出标准 HID 键盘和鼠标事件。

# 1. 项目目标与边界

本项目的目标不是构建通用游戏视觉 AI，而是构建一个低计算量、低延迟、可验证的闭环控制系统。系统只处理固定游戏、固定显示环境下的有限视觉区域，并将检测结果转换为标准 USB HID 键鼠输入。

## 1.1 核心功能目标

- 使用外置摄像头拍摄 B 电脑的显示器，而不是直接读取 B 的 framebuffer、截图 API 或游戏内存。

- A 电脑负责视频采集、屏幕校正、ROI 截取、目标存在检测、状态机与动作决策。

- 游戏为 2D 横版；每张地图只有一种怪物，因此不需要怪物类别识别。

- 只检查玩家前方固定距离/固定区域，原则上不做全屏目标检测。

- 检测结果最低只需输出布尔值：target_present = True / False；必要时附带 score。

- A 电脑通过 Wi-Fi 向 Pico 2 W 发送“按键、鼠标移动、点击、释放”等高级命令。

- Pico 2 W 作为 USB Device，通过一根 Micro-USB 数据线连接 B，并同时暴露 HID Keyboard + HID Mouse。

- B 电脑无需运行视觉识别程序；对 B 而言，Pico 是标准 USB HID 设备。

## 1.2 MVP 明确不做

- 不默认使用 YOLO / RT-DETR / 大型深度学习模型。

- 不做怪物分类、实例分割或精确像素级轮廓。

- 不做全屏持续检测。

- 不在第一版自动识别玩家位置；可以先使用固定 ROI 或人工标定坐标。

- 不在第一版自动识别屏幕四角；优先采用一次人工四点标定并保存透视矩阵。

- 不在 Pico 上运行 OpenCV 或视觉模型；Pico 只负责通信、状态安全和 HID 输出。

- 不在第一版开发 Web UI、数据库、Docker、复杂消息队列。

# 2. 总体系统架构

```text
[外置摄像头]
      │ USB
      ▼
┌─────────────────────────────┐
│ A 电脑                       │
│ Python + OpenCV             │
│ 1. Camera Capture           │
│ 2. Screen Rectification     │
│ 3. Fixed / Dynamic ROI      │
│ 4. Presence Detection       │
│ 5. Temporal Filter          │
│ 6. Decision / State Machine │
└──────────────┬──────────────┘
               │ Wi-Fi (UDP/TCP)
               ▼
┌─────────────────────────────┐
│ Raspberry Pi Pico 2 W       │
│ 1. Wi-Fi receiver           │
│ 2. Command parser           │
│ 3. Safety / timeout         │
│ 4. USB HID composite        │
└──────────────┬──────────────┘
               │ Micro-USB DATA + POWER
               ▼
┌─────────────────────────────┐
│ B 电脑                       │
│ USB HID Keyboard + Mouse    │
│ 运行游戏                    │
└─────────────────────────────┘
```

> **关键职责分离：**A 电脑承担“感知 + 决策”，Pico 2 W 承担“通信 + 执行”。任何复杂逻辑都优先留在 A，Pico 固件保持小、确定、可恢复。

# 3. 硬件与连接

| **组件**              | **当前状态** | **用途**             | **开发备注**                                    |
|-----------------------|--------------|----------------------|-------------------------------------------------|
| Raspberry Pi Pico 2 W | 已购买       | Wi-Fi 接收 + USB HID | RP2350；无需 microSD；不运行 Linux              |
| Micro-USB 数据线      | 需确认/准备  | Pico → B             | 必须支持数据传输；同时负责供电                  |
| 外置摄像头            | 需准备       | 拍摄 B 屏幕          | 优先支持稳定曝光/对焦，30 FPS 即可，60 FPS 更佳 |
| A 电脑                | 已有/开发端  | OpenCV 与决策        | 普通 CPU 即可；MVP 不要求独显                   |
| B 电脑                | 被控端       | 运行游戏             | Pico 插入其 USB 口，被识别为 HID 设备           |

**USB 连接要求：**使用 Pico 2 W 的 Micro-USB 口连接 B。线材必须具有 D+/D- 数据线，不能使用 charge-only 线。B 的 USB 口同时为 Pico 供电并承载 HID 数据。

# 4. A 电脑视觉处理链路

## 4.1 推荐 MVP Pipeline

```text
Camera frame
    ↓
Perspective correction（一次人工四点标定）
    ↓
Canonical game frame（统一尺寸，例如 1920×1080）
    ↓
Player-front ROI（固定坐标，后续可动态）
    ↓
Presence detector
    ↓
Raw score / raw boolean
    ↓
Temporal debounce / hysteresis
    ↓
target_present = True / False
    ↓
Decision state machine
```

## 4.2 检测算法优先级

| **优先级** | **方法**                      | **训练需求** | **优点**                 | **使用条件**                 |
|------------|-------------------------------|--------------|--------------------------|------------------------------|
| 1          | 模板匹配（cv2.matchTemplate） | 无           | 简单、可解释、CPU 负载低 | 2D sprite 外观稳定；当前首选 |
| 2          | 颜色/阈值/轮廓                | 无           | 计算量最低               | 目标与背景色彩差异明显时     |
| 3          | 小型 CNN 二分类               | 需要少量数据 | 比模板更抗姿态变化       | 模板匹配鲁棒性不足时         |
| 4          | YOLO 等检测器                 | 需要标注训练 | 位置/姿态复杂时更强      | 只有前面方法无法满足时再引入 |

> **当前默认选择：**先以模板匹配作为目标存在检测器。由于每张地图只有一种怪物，且只检查有限 ROI，类别识别和通用目标检测没有必要。

## 4.3 ROI 设计

MVP 可先固定 ROI，不要求实时定位玩家。ROI 应覆盖“玩家前方一定距离”的有效游戏空间，并尽量排除 UI、血条、技能栏和非目标动态背景。

```yaml
# 示例，仅用于表达配置结构
roi:
  x1: 650
  y1: 430
  x2: 1100
  y2: 820
```

## 4.4 时间滤波

单帧识别结果不能直接触发动作。建议使用最近 N 帧投票、连续帧确认或双阈值滞回，避免摄像头噪声、摩尔纹或动画瞬间造成误触发。

例如：

- 最近 5 次检测中至少 3 次为 `True` → `target_present = True`
- 或使用双阈值滞回：
  - `score >= 0.75` → 进入 `detected`
  - `score <= 0.55` → 离开 `detected`
  - 中间区间保持上一状态

# 5. 摄像头与屏幕校正

- 第一版允许用户手动点击显示器画面的四个角：左上、右上、右下、左下。

- 使用 cv2.getPerspectiveTransform / cv2.warpPerspective 将摄像头画面变换为固定尺寸的“虚拟截图”。

- 标定完成后保存 homography matrix 到 calibration.yaml；摄像头和显示器位置固定时无需每次重做。

- 优先锁定摄像头曝光、白平衡、对焦，减少自动曝光导致的输入分布变化。

- 实测是否存在屏幕刷新导致的 banding / flicker / moiré；若明显，调整摄像头曝光或显示器刷新配置。

# 6. A 电脑 → Pico 2 W 通信协议

MVP 建议采用同一局域网 Wi-Fi 下的 UDP 或 TCP。若追求低延迟与实现简单，可先 UDP；若更关心可靠送达和连接状态，可先 TCP。不要一开始引入 MQTT，除非后续需要与智能家居系统整合。

## 6.1 推荐命令模型

```text
PING
KEY_DOWN W
KEY_UP W
KEY_PRESS X 50
MOUSE_MOVE 20 -5
MOUSE_LEFT_DOWN
MOUSE_LEFT_UP
MOUSE_RIGHT_CLICK
MOUSE_SCROLL -1
RELEASE_ALL
```

第一版协议可以直接使用 UTF-8 文本 + 换行。稳定后再考虑二进制编码、序列号或校验字段。

## 6.2 必须实现的安全机制

- Pico 在超过设定时间未收到 A 的 heartbeat/命令时执行 RELEASE_ALL，避免按键卡住。

- 任何异常解析不得持续保持按键按下状态。

- 收到 REBOOT / DISCONNECT / timeout 时释放全部键盘按键和鼠标按钮。

- A 端退出时主动发送 RELEASE_ALL。

- 对重复命令明确语义，避免 UDP 重复包造成不必要多次点击。

# 7. Pico 2 W 固件设计

Pico 2 W 的固件只负责可预测的底层执行。建议优先使用 Pico SDK + TinyUSB（C/C++）获得稳定的 USB HID Composite 支持；若快速原型更重要，也可先评估 CircuitPython/MicroPython 的 HID 支持，但正式版本应以时序和稳定性测试为准。

## 7.1 Pico 模块

| **模块**       | **职责**                                      |
|----------------|-----------------------------------------------|
| wifi_manager   | 连接 Wi-Fi、自动重连、保存最小网络配置        |
| command_server | UDP/TCP 监听、分帧与解析                      |
| command_queue  | 限制队列长度，避免积压过期动作                |
| hid_keyboard   | 按下、释放、组合键、release_all               |
| hid_mouse      | 相对移动、左/右/中键、滚轮                    |
| watchdog       | heartbeat timeout、异常恢复、release_all      |
| status         | LED/简单状态码：boot、Wi-Fi、connected、error |

# 8. USB HID 输出要求

- Pico 对 B 枚举为 USB Composite Device，至少包含 HID Keyboard 与 HID Mouse 两个接口。

- 键盘支持 key down / key up / press / modifier / release all。

- 鼠标 MVP 采用相对坐标 HID：dx、dy、buttons、wheel。单次位移需遵守 HID report 的数值范围，长距离移动拆分为多次 report。

- 不要求绝对坐标鼠标；若后续确实需要屏幕绝对定位，再单独设计 absolute pointing device descriptor。

- B 端不依赖自定义驱动，优先使用操作系统原生 HID 驱动。

# 9. 推荐代码仓库结构

```text
game-control-system/
├─ README.md
├─ docs/
│  └─ development_spec_v0.2.md
├─ host/                         # A 电脑
│  ├─ requirements.txt
│  ├─ config.yaml
│  ├─ main.py
│  ├─ src/
│  │  ├─ camera.py
│  │  ├─ calibration.py
│  │  ├─ roi.py
│  │  ├─ detector.py
│  │  ├─ temporal_filter.py
│  │  ├─ decision.py
│  │  └─ pico_client.py
│  ├─ templates/
│  ├─ samples/
│  └─ tests/
└─ pico/                         # Pico 2 W 固件
   ├─ CMakeLists.txt
   ├─ src/
   │  ├─ main.c
   │  ├─ wifi_manager.c
   │  ├─ command_server.c
   │  ├─ hid_keyboard.c
   │  ├─ hid_mouse.c
   │  └─ watchdog.c
   └─ tests/
```

# 10. Host 配置文件建议

```yaml
camera:
  index: 0
  width: 1920
  height: 1080
  fps: 30

screen:
  output_width: 1920
  output_height: 1080
  calibration_file: calibration.yaml

roi:
  x1: 650
  y1: 430
  x2: 1100
  y2: 820

detector:
  type: template_matching
  threshold_on: 0.75
  threshold_off: 0.55
  templates_dir: templates/map_01

temporal_filter:
  window: 5
  minimum_positive: 3

pico:
  host: 192.168.1.50
  port: 5000
  protocol: udp
  heartbeat_ms: 500

safety:
  command_timeout_ms: 1500
  release_all_on_exit: true
```

# 11. 开发阶段与验收标准

| **阶段** | **实现内容**          | **验收条件**                                             | **暂不包含**   |
|----------|-----------------------|----------------------------------------------------------|----------------|
| P0       | Pico USB HID 单机验证 | B 能识别键盘+鼠标；可执行按键、移动、点击、release_all   | Wi-Fi、视觉    |
| P1       | Pico Wi-Fi 命令接收   | A 发文本命令，Pico 正确转 HID；timeout 能 release_all    | 视觉           |
| P2       | A 摄像头采集          | 稳定显示视频、FPS、异常处理                              | 屏幕校正、检测 |
| P3       | 屏幕四点标定          | 输出稳定矩形游戏画面；标定可保存/加载                    | 自动屏幕定位   |
| P4       | 固定 ROI + 模板匹配   | 实时显示 ROI、score、raw result；有/无怪可分离           | 动态玩家定位   |
| P5       | 时间滤波 + 状态机     | 误触发显著降低；动作只在满足条件时产生                   | 复杂 AI        |
| P6       | 完整闭环              | 摄像头事件 → A 决策 → Wi-Fi → Pico → B HID，连续运行稳定 | Web UI/数据库  |

# 12. 关键测试指标

- 视觉准确性：在采集的有怪/无怪样本上统计 false positive、false negative，而不是只看单个示例。

- 阈值依据：记录 raw score 分布后再确定 threshold_on / threshold_off，禁止凭经验固定为 0.8。

- 端到端延迟：从目标进入 ROI 到 B 收到 HID 动作的时间，分解 camera / vision / network / Pico / USB。

- 稳定性：至少进行连续运行测试，检查 Wi-Fi 重连、USB 断连、程序退出后是否存在卡键。

- 故障安全：断开 A 网络、关闭 A 程序、发送非法命令时，Pico 必须最终处于 RELEASE_ALL 状态。

- 资源占用：A 端 CPU 使用率、处理 FPS；MVP 目标不是满 60 FPS，而是满足动作响应要求。

# 13. AI 辅助开发工作方式

不要让 AI 一次生成整个项目。每个阶段只给一个可验证任务，并要求 AI 不主动扩大 scope。每次提交都必须可运行、可测试、可回退。

## 13.1 每次给 AI 的固定上下文

- 当前开发规格（本文件）以及当前阶段 P0–P6。

- 当前真实目录结构和已有代码，不允许 AI 假设不存在的文件。

- 当前硬件：Raspberry Pi Pico 2 W；B 通过 Micro-USB 数据线连接。

- 本次唯一任务、明确输入/输出以及验收条件。

- 禁止项：不要添加未请求框架、数据库、Web UI、YOLO 等。

- 要求 AI 给出运行方式、测试方法和已知限制。

## 13.2 推荐给 AI 的第一个固件任务

> **P0 Prompt 要点：**只实现 Pico 2 W 的 USB Composite HID：Keyboard + Mouse。先不连接 Wi-Fi。要求 B 电脑可枚举设备；通过固件内置测试序列验证键盘按下/释放、鼠标相对移动、左键点击和 RELEASE_ALL。必须提供构建、刷写、测试步骤。

## 13.3 推荐给 AI 的第一个 Host 任务

> **Host P2 Prompt 要点：**只实现 Windows/Python/OpenCV 的 Camera 类：camera index、width、height、FPS 配置，显示实际 FPS，Q 退出，读取失败明确报错。暂不实现检测、YOLO、模板匹配或网络控制。

# 14. 尚未锁定、需要实测后决定的参数

| **项目**    | **当前默认**            | **决定依据**                 |
|-------------|-------------------------|------------------------------|
| A→Pico 协议 | UDP 或 TCP              | 端到端延迟、丢包、实现复杂度 |
| 检测方法    | 模板匹配                | 真实摄像头数据上的 FP/FN     |
| ROI 坐标    | 固定配置                | 实际游戏分辨率和玩家画面位置 |
| 模板数量    | 每地图若干代表帧        | 动画姿态覆盖率               |
| 阈值        | 由数据标定              | 有怪/无怪 score 分布         |
| Camera FPS  | 30 起步                 | 延迟需求、CPU 负载、闪烁情况 |
| Pico 开发栈 | Pico SDK + TinyUSB 优先 | HID + Wi-Fi 稳定性与开发成本 |

# 15. 当前下一步（按顺序执行）

1.  确认并准备一根支持数据传输的 Micro-USB 线，将 Pico 2 W 连接到 B 电脑。

2.  完成 P0：Pico 单独作为 USB Composite HID Keyboard + Mouse，验证 B 的枚举与基本动作。

3.  完成 P1：让 Pico 加入 Wi-Fi，A 发送测试命令并驱动 HID；加入 heartbeat / RELEASE_ALL。

4.  固定摄像头物理位置并录制 1–3 分钟真实游戏视频，覆盖有怪、无怪、滚屏、攻击动画等场景。

5.  完成 P2/P3：摄像头采集 + 四点透视校正。

6.  选定 ROI，建立第一组模板，记录有怪/无怪 score 分布。

7.  完成 P4/P5 后再接入动作逻辑，最后做 P6 端到端闭环。

> **当前最重要原则：**先验证“Pico 可以稳定输出键鼠”和“固定 ROI 可以稳定判断目标存在”这两个独立子系统，再把它们连起来。不要在子系统尚未验证前同时调试视觉、网络和 HID。

# 附录 A：MVP 完成定义（Definition of Done）

- B 电脑插入 Pico 后无需自定义驱动即可识别键盘和鼠标。

- A 可以通过局域网向 Pico 发送命令，Pico 在合理延迟内生成对应 HID 行为。

- A 能稳定读取摄像头并生成校正后的游戏画面。

- 固定 ROI 中的目标存在检测在真实测试样本上达到可接受的误检/漏检水平。

- 时间滤波避免单帧噪声直接触发。

- 网络中断、A 程序退出或非法命令不会造成按键长期保持。

- 端到端闭环至少可以连续运行一段测试周期而无明显卡键、USB 重枚举或程序崩溃。

- 所有关键参数均来自 config，而不是散落在代码中的 magic numbers。

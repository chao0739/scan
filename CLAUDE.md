# 项目：外置摄像头游戏目标检测 + Pico 2 W HID 控制

用外置摄像头拍摄 B 电脑显示器上的冒险岛(MapleStory)画面，A 电脑(本机)做视觉检测与决策，
经 Wi-Fi UDP 把按键命令发给 Raspberry Pi Pico 2 W，Pico 以 USB HID 键鼠身份插在 B 电脑上执行。
B 电脑零软件。完整规格见 `外置摄像头游戏检测与Pico2W_HID控制系统_开发规格_v0.2.md`（必读），
详细进展与下一步见 `docs/PROGRESS.md`。

## 目录
- `game_vision/` — A 端视觉程序（Python + OpenCV，跨平台）。入口 `menu.py`（菜单式）或 `app.py`（命令行）。
- `game_vision/pico_client.py` — A→Pico 的 UDP 客户端（自动发现、心跳、命令封装）。
- `pico/` — Pico 2 W CircuitPython 固件。P0=HID 验证，P1=Wi-Fi+UDP+心跳保护（当前烧录的是 P1）。

## 关键事实（新 session 必知）
- 视觉链路已完成并验证：四角标定→透视矫正(1280×720)→玩家名牌定位(带跟踪)→前方 ROI 模板匹配→滞回去抖。
- 检测只判"有/无"，不分类别。模板按怪物分目录 `templates/<怪物名>/`，玩家名牌在 `templates/players/<ID>.png`。
- 阈值 0.78 是在旧机位数据上标定的；**换机位/换摄像头后要重新采模板、重标阈值**。
- 标定文件与机位绑定：离线处理视频必须用 `--calib` 指定该视频对应的标定，否则全错位。
- Pico P1 协议：UDP:5000 文本命令（PING/KEY_DOWN/KEY_UP/KEY_PRESS/MOUSE_*/RELEASE_ALL），
  心跳 500ms，超时 1500ms 自动 RELEASE_ALL（已实测）。LED：快闪=连Wi-Fi，常亮=就绪，慢闪=超时保护。
- 用户设置在 `game_vision/settings.yaml`（gitignore，不入库）；`config.yaml` 是默认值，被 settings 覆盖。
- 大文件(视频/录像/日志/harvest)全部 gitignore。

## 已定决策（不要重新讨论）
- MVP 不用 YOLO/CNN；模板匹配为主，只有实测不达标才升级（规格 §11）。
- 朝向 `facing: auto`（角色位移−背景滚动位移判方向）；将来由按键状态直接提供。
- Pico 固件先用 CircuitPython 原型，稳定后再评估切 Pico SDK + TinyUSB。
- 一次只做一个可验证任务（规格 §13），不主动扩大 scope。

## 当前阶段
P0/P1 ✅（Pico HID + Wi-Fi 命令 + 卡键保护全部实测通过）。视觉端 P2–P4 ✅。
**下一步：P5 决策状态机（检测到怪→按键逻辑，等用户定义）+ P6 端到端闭环。**
A 电脑正在从 Windows 迁移到 Ubuntu（代码已做跨平台适配，需重新标定和采模板）。

# 项目进展记录

更新于 2026-08-26。新开发环境（Ubuntu A 电脑）从这里接手。

## 一、系统架构（规格 v0.2）

```
外置摄像头 --USB--> A 电脑(Ubuntu, 视觉+决策) --Wi-Fi UDP--> Pico 2 W --USB HID--> B 电脑(Windows, 跑游戏)
```

## 二、已完成

### 视觉端（game_vision/，对应规格 P2–P4，全部完成并超出）
| 模块 | 状态 | 说明 |
|---|---|---|
| camera.py | ✅ | 摄像头/视频统一读取；fps 非法值(-1)防护；录像写入器（mp4v→MJPG 降级）；跨平台后端 |
| calibration.py | ✅ | 点四角标定 UI + 透视矫正到 1280×720 |
| roi.py | ✅ | 玩家名牌模板定位（局部跟踪 120px + 丢失 5 帧全局重搜）；前方 ROI；facing auto/left/right/both |
| detector.py | ✅ | 多模板灰度匹配 + 自动水平翻转；取最高分 |
| debounce.py | ✅ | 5 帧滞回（3 进 4 出） |
| app.py | ✅ | 主循环；JSONL 日志；运行中热键 q/空格/m 换怪/t 抠模板/c 重标定/r 录制 |
| menu.py | ✅ | 菜单式入口（标定/玩家档案/怪物模板/采集/设置/复盘），设置存 settings.yaml |
| pico_client.py | ✅ | UDP 客户端：广播自动发现 Pico、500ms 心跳线程、命令封装、退出 RELEASE_ALL |
| tools/harvest_templates.py | ✅ | 半自动采模板：种子扫视频/摄像头→候选缩略图→按编号保存（对齐、统一尺寸） |
| tools/review_log.py | ✅ | 日志叠加到抽帧图，人工核对检测结果 |
| tools/crop_templates.py | ✅ | 离线手动抠模板 |

### 实测数据结论（旧机位 shot.mp4，1296 帧）
- 单帧 ≈22ms（facing auto 单侧）/48ms（both 双侧），达标 <50ms。
- 阈值 0.78：132 个难例候选（47 真怪）上 38 命中/1 误报；单帧漏检靠去抖兜底。
- 玩家定位："名字+公会牌"模板比只用名字稳一倍；加跟踪后全程 0 丢失。
- 踩过的坑（勿重复）：
  - 被打击闪白的帧做模板 → 在空白天空误报 0.8，禁止用此类帧。
  - 摄像头 fps 报告 -1 → `or 30` 不生效导致录像 0 字节，已修。
  - 实机标定覆盖 homography.json 导致离线视频全错位 → 所有工具支持 --calib，每段视频配自己的标定。
  - 平坦/低纹理模板会大范围误匹配。

### Pico 端（pico/，对应规格 P0/P1，全部实测通过）
- P0：CircuitPython 复合 HID 键盘+鼠标，测试序列验证（键入/修饰键/移动/点击/滚轮/release_all）。
- P1：Wi-Fi + UDP:5000 文本协议 + PING 心跳；1500ms 无包自动 RELEASE_ALL（实测：KEY_DOWN w 后杀掉 A 端进程，1.5s 内停止输出）；Wi-Fi 掉线释放并重连。
- 当前 Pico 上烧的是 P1 固件 + settings.toml（用户 Wi-Fi）。CircuitPython 10.2.1。
- A/B 目前同机测试通过；接下来 Pico 插到 B 电脑（Windows）。

## 三、正在进行 / 下一步（按顺序）

1. **迁移 A 端到 Ubuntu**：克隆本仓库 → `pip install -r game_vision/requirements.txt` →
   `ls /dev/video*` 确定摄像头号 → menu.py 里改摄像头、重新标定、重建玩家档案、重新采当前地图模板
   （模板与机位/摄像头绑定，旧模板仅作参考）→ 重标阈值（记录有怪/无怪 score 分布，规格 §12）。
2. **P5 决策状态机**：`decision.py` — detected/side → 按键命令序列。
   用户尚未定义打怪逻辑（按什么键、节奏、要不要先转身）。**先做 dry-run 模式**（只打日志不发按键）。
   计划支持：p 键暂停/恢复自动控制；分数双阈值滞回(threshold_on/off)可在此阶段一并加入。
3. **P6 端到端闭环**：app.py 集成 pico_client + decision；测端到端延迟（怪进 ROI → B 收到按键），
   分解 camera/vision/network/USB 各段；连续运行稳定性测试（规格 §12）。

## 四、遗留问题 / 已知限制
- 阈值余量薄：背景岩石可到 0.75–0.76，弱命中 0.73–0.75；新机位数据到手后重标，必要时加彩色二次校验或多模板同位置投票。
- facing auto 原地转身不走动时不更新方向；P6 之后可由决策模块的按键状态直接提供朝向。
- 摄像头位移无自动检测，需人工重标定。
- 每张地图两种怪可混放一个模板目录（检测不分类别）；日志 template 字段可区分命中哪张。
- 硬件事实：用户外置摄像头（Windows 下编号 3）输出 1920×1080@30，fps 报告为 -1；Pico 2 W 只支持 2.4GHz Wi-Fi。

## 五、类似开源项目调研（2026-08-26）
截屏方案的冒险岛 bot：tanjeffreyz/auto-maple（小地图定位玩家思路可借鉴）、Dashadower/MS-Visionify、qlvbrknp/maple-bot。
Pico 远程 HID：quiboco/remote-hid-pico、Saketh-Chandra/PicoWiFiDucky。
「外置摄像头拍屏+透视矫正」组合未见完成度高的开源实现。

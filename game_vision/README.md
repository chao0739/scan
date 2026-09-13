# game_vision — 外置视觉的冒险岛目标检测 / 自动巡逻

取得游戏画面（**NDI 网络流**，或摄像头拍显示器）→ 矫正成 1280×720 → 定位玩家（名牌）→ 截取玩家前方 ROI → 模板匹配怪物 → 多帧去抖 → 决策 → Pico 按键。

画面来源两种（`camera.source`）：
- **NDI（当前）**：B 机游戏 → B 机 OBS 开 NDI 输出（DistroAV）→ 局域网 → **本机脚本直接收**（`ndi:Game-PC`）。不经过本机 OBS/虚拟摄像头，
  不需要 v4l2loopback/sudo；画面像素级清晰、无透视畸变。需要 `pip install cyndilib`。
- 摄像头（旧）：Insta360 拍显示器，`camera.source: Insta360`，要手动标定四角。

## 环境 / 安装
Python 3.10+。**一键安装**（在仓库根目录，会建 `.venv` 虚拟环境并装依赖，约 100 MB）：

```bash
./install.sh            # Linux / macOS；Windows 双击 install.bat
./run.sh                # 启动菜单；Windows 双击 run.bat
```

要从游戏客户端 `aa/` 导出精灵图（`tools/wz_sprites.py`）的机器加 `--tools`（装 UnityPy/lz4）。
手动装也行：`pip install -r requirements.txt`（可选 `-r requirements-tools.txt`）。cyndilib 自带 libndi，Windows/Linux/macOS 都有预编译包，不用装 NDI SDK。
Ubuntu 系统自带的 python3 建不了虚拟环境（缺 `python3-venv`）时脚本会提示 `sudo apt install python3 python3-venv`（交互时可当场装）；有 miniconda 的机器会直接用 conda 的 python。
Ubuntu 若报缺 `libGL.so.1`：`sudo apt install libgl1 libglib2.0-0`；NDI 源发现靠 mDNS，要有 `avahi-daemon`（桌面版默认有）。

## 部署到另一台电脑
完整步骤（游戏机 D + 脚本机 C 两边各要做什么）见仓库根目录的 **`README.md`**。要点：
`python tools/pack.py [--with-wz]` 打成 zip → C 机解压后 `./install.sh` / `./run.sh` → 菜单里设「画面来源」（NDI 源名）、「标定屏幕四角」（自动）、「Pico」（自动发现或填 IP）。
D 机只需要 OBS + DistroAV 开 NDI 输出，并把 Pico 插在它的 USB 上。同一角色/地图的名牌模板、怪物模板、巡逻端点都随包，不用重做。

## 快速开始（菜单式，推荐）
```bash
python menu.py
```
数字选功能即可，不用记命令。首次使用按顺序做：

1. **设置 → 摄像头**：菜单会列出本机摄像头和**局域网上的 NDI 源**。NDI 填 `ndi:源名关键字`（如 `ndi:Game-PC`）；
   摄像头填编号或名称关键字（如 `Insta360`，编号变了也能找到）
2. **标定屏幕四角**：NDI/采集卡选「自动」（整幅画面就是游戏，自动取非黑区域；B 机 OBS 画布比游戏窗口大时右/下黑边会被切掉）；
   摄像头拍屏选「手动」，依次点击游戏画面 左上 → 右上 → 右下 → 左下，按 `s` 保存。
   **换了画面来源（相机↔NDI）后，标定和名牌/怪物模板都要在新画面上重做**（清晰度、缩放都变了）。
3. **玩家设置 → 新增玩家**：输入玩家 ID，点一下弹窗取焦点 → 按空格定格 → **贴紧框住名牌**（黑底白字的名字条，有公会牌一起框；不要带角色的脚和背景，否则定位会乱跟）→ 按 `s`
4. **怪物模板 → 新建怪物** → **手动抠模板** 抠 2–3 张种子 → **半自动采集**（录 2 分钟 → 自动弹出候选缩略图 → 输入编号保存）
   → **模板去重**（模板超过 ~20 张时做一次：耗时 ≈ ROI 面积 × 模板数 × 2，45 张≈110ms/帧，15 张≈40ms）
5. **巡逻端点**（P7 位置巡逻）：用你自己的键盘把角色走到想让它掉头的左位置按 `l`、右位置按 `r`、`s` 保存。
   **推荐选「小地图坐标」**：用左上角小地图的黄点定位，端点可在任意位置、名牌被挡也照走（见下方「小地图巡逻」）。以下屏幕/地图坐标是旧方案：
   窗口顶部 `CAM: LOCKED` 表示镜头被地图边界顶住、这里的屏幕 x 可靠；`FOLLOWING` 表示镜头在跟随、此处记的点无效。
   端点按怪物名存在 `settings.yaml` 的 `patrol` 段；没标端点时巡逻退化为「走 walk_max_ms 后掉头」。
   保存后若两端都抠到了地标，会问用哪种坐标：**屏幕坐标**（简单可靠，要求端点在镜头被顶住处）或
   **地图坐标**（`worldpos.py` 靠地标校准，端点可在任意位置，包括镜头跟随区）。可在「巡逻端点→切换坐标模式」改。
6. **开始检测**。掉帧/省 CPU：「设置 → 运行方式」选「不开窗口」（可顺便设自动停止秒数），之后「开始检测」不弹画面、Ctrl+C 停止；
   命令行等价 `python app.py --no-show [--seconds 600]`。

以后日常只需 `python menu.py` → `1`。换地图：怪物模板 → 新建/选择怪物 → 采模板。换号：玩家设置 → 新增/切换。
摄像头挪动后重新标定。所有选择都记在 `settings.yaml`，下次自动沿用。

> 玩家 ID 只用于命名和切换档案；定位依赖的是框选的名牌图像，所以每个玩家需要在实机画面上框一次。

## 命令行方式（高级 / 离线调试）


1. 离线回放验证（用已录好的拍屏视频，注意带上该视频对应的标定文件）：
   ```bash
   python app.py --source ../shot.mp4 --calib calibration/homography_shot_mp4.json
   ```
   窗口里：青色框 = 搜索 ROI，紫色十字 = 玩家脚底，绿框 = 命中的树桩，顶部显示状态/分数/耗时。`q` 退出，空格暂停，`m` 切换怪物，`t` 在当前画面拖框新增模板，`c` 重新标定，`r` 开始/停止录制摄像头画面到 `recordings/`。

2. 接真实摄像头（第一版允许人工初始化）：
   ```bash
   python app.py --source 0 --calibrate
   ```
   弹出标定窗口，按 **左上 → 右上 → 右下 → 左下** 顺序点击游戏画面四角，右侧会实时预览矫正结果，满意后按 `s` 保存。
   摄像头和显示器不动的话，下次直接 `python app.py --source 0` 复用标定。

3. 无窗口运行（只出日志）：`python app.py --source 0 --no-show`
4. 自动化 / 真机实验：`--seconds 150` 到时自动停（退出走 finally → RELEASE_ALL）；`--control off|dry|pico` 覆盖控制模式；
   `--set a.b.c=value`（可多次）临时覆盖任意配置项而不写 settings，例如
   `python app.py --control pico --no-show --seconds 150 --set control.jump_on_stuck=true`

## 输出
- 控制台：状态变化时和每 N 帧打印一行 JSON。
- `logs/run_*.jsonl`：每帧一行，字段 `timestamp / frame / monster / detected / raw / score / template / color_dist / edge_score /
  roi / side / facing / player / player_score / dist / ctl / held / patrol_target / bg_dx / stuck / latency_ms`。
  `ctl` 状态：PATROL / ATTACK / LOST / PAUSED / IDLE / RECOVER(卡住后跳) / GIVEUP(跳无效掉头)；`bg_dx` 背景滚动量(px/帧)；`stuck` 卡住判定。

## 配置 `config.yaml`
| 段 | 关键项 | 说明 |
|---|---|---|
| camera | source / width / height / ndi_transport | `ndi:<源名关键字>` / 摄像头编号 / 名称关键字（如 `Insta360`）/ 视频路径。`ndi_transport: tcp`（默认；libndi 默认的 RUDP/UDP 在本网上连得上但收不到帧） |
| screen | output_width/height | 矫正后虚拟游戏画面尺寸（**模板尺寸与其绑定**，改了要重抠模板） |
| app | show / run_seconds / cv_threads | 菜单「开始检测」的运行方式：不开窗口、到时自动停；`cv_threads: 2`——ROI 只有 300×90，OpenCV 8 线程切不开只是空转（帧率相同、CPU 241% → 148%） |
| roi.player | template / match_threshold / local_threshold / track_window / mid_window / mid_threshold | 用玩家名牌定位玩家：小窗跟踪(±120) → 丢了先在上一位置 ±mid_window 内找(≥mid_threshold，靠位置连续性认边缘处的低分真名牌) → 再全局搜(≥match_threshold)。摄像头拍屏用 0.82/0.72/0.65；**NDI 清晰画面名牌半透明、暗背景真值只 0.76–0.8，模板只抠名字一圈以内（32×12），阈值 0.70/0.62/0.55**（假峰 ≤0.46）。全局搜索是「粗到精」（`global_coarse_scale: 0.5` / `topk: 20`：缩小找 20 个峰再全分辨率精修，11 ms → 3.7 ms，620 帧只漏 1 帧且下一帧即补）；`global_coarse_scale: 0` 回到整幅全分辨率搜。**`text_mask`**（默认关）：只比名字文字像素的掩码匹配（名牌底条半透明，亮背景前整块 NCC 掉到 0.6~0.7 就丢），开之前先用 `tools/nameplate_eval.py` 在录像上看分数分布并重标四个阈值；掩码匹配约 5 倍耗时（跟踪时每帧多 6 ms） |
| minimap | enabled / region / px_scale / yellow_lo,hi / min_area,max_area / ui_dir / anchor_min | 小地图黄点定位（`minimap.py`）；`patrol.<怪物>.mode: minimap` 时巡逻用它。region 要把整个小地图窗口（含右/下边框）框进来。缩略图区域靠 `templates/_ui/` 三块窗口 UI 模板（标题栏左端/右端、底部条纹）定位，匹配分 <`anchor_min` 沿用上次区域 |
| roi.world | enabled / deadband / lock_frames / landmark_thr / fix_every / landmark_box | 地图 x 估计（`worldpos.py`）：屏幕 x + 累加镜头位移(bg_dx)，靠地图边界归零或地标匹配防漂移。端点标定时会自动抠地标 |
| roi | facing / near_offset / far_offset / up / down / scroll_band | 玩家前方 ROI（up/down 只覆盖同一层，默认 70/20——上 135 会把上层平台的怪框进来、站在下面对着够不着的怪挥刀）。near_offset<0 时两侧矩形重叠会自动合并成一个（不重复匹配，怪在哪侧按位置判断）。**菜单「设置 → 检测范围」可在画面上拖框设置**：定格后在角色面朝一侧拖框，自动换算成相对脚底的前/后/上/下距离（左侧拖也行，运行时左右镜像；near 为负 = 从身后开始）。`facing`: **`key`**(用决策按住的方向键定朝向，只检测前进方向，推荐) / `auto`(按位移估计) / `right` / `left` / `both`。far_offset 现为 140（攻击区，不追怪）。scroll_band 是估计背景滚动量的画面带 |
| roi.facing_auto | window / min_move | 前进方向判定：累计最近 N 帧的“角色屏幕位移 − 背景滚动位移”，超过 min_move 像素才切换方向 |
| detection | threshold / sure_score / motion_min / sprite_scale / sprite_threshold / sprite_sure / color_verify… | `threshold` 是候选下限（0.5）；`sure_score`(0.65) 以上直接接受，之间的候选还要求**框里在动**（与上一帧按背景滚动对齐后的平均灰度差 ≥ `motion_min`=5）——棕色岩壁纹理能拿 0.5~0.63 并骗过颜色/边缘校验但它不动，走动/被打的怪 15~60；发呆的中分怪会漏（与只用 0.65 时相同）。2026-08-28 录像回归：命中帧 319→576，人工核对新增的全是真怪。精灵图模板（`wz_*.png`，见「原版精灵图模板」）走 masked 两阶段匹配：`sprite_scale` 0.93、`sprite_threshold` 0.6、`sprite_sure` 0.75，命中帧再到 1106。候选框颜色直方图距离上限 0.48；边缘图匹配下限 0.4；每模板检查的峰数；flip 自动加水平翻转模板；`idle_skip: 2` 空闲跳帧——去抖窗口内一次都没命中时每 2 帧才跑一次模板匹配（占单帧 70% 的开销减半），一有命中立刻逐帧，首次发现最多晚 1 帧（33 ms）；日志里该帧带 `det_skipped: true` |
| debounce | window_size / enter_min_hits / exit_min_misses | 滞回去抖：5 帧中 ≥3 命中进入，≥4 未命中退出 |
| control | mode / attack_key / attack_interval_ms / approach / patrol_tolerance / walk_max_ms … | 决策：off 只检测 / dry 只打日志 / pico 真发按键。有怪（在攻击区内）：站着打（approach=false 不追）；没怪：在两个端点间巡逻（端点存 settings `patrol.<怪物名>`）。`p` 暂停 |
| control | stuck_ms / stuck_move_px / stuck_scroll_px | P9 卡住检测：按着方向键 1.5 s 内玩家 x 没动且背景没滚 → `stuck`（只报警） |
| control | jump_on_stuck / jump_key / jump_recover_ms / jump_max_retry / jump_clear_px | P10 跳跃恢复（默认关）：卡住 → 按着方向键点跳；连续 3 次无效 → 掉头；离开卡住点 40 px 才算脱困 |
| monster | current / ask_on_start | 怪物模板集（`templates/<name>/`）；启动时菜单选择，运行中 `m` 切换 |
| control.human_* | human_jump_per_min / human_jump_min_gap_ms / human_endpoint_px / human_pause_ms / human_walk_jitter | 拟人随机：巡逻中按泊松间隔随机跳（默认 4/min）；每次掉头下一个端点随机多走/少走 ±60px、停 0~800ms 再走；时间巡逻步长 ±20%。攻击/卡住恢复/停顿期间不跳。菜单「设置→拟人随机动作」可改，0=关 |

## 小地图巡逻（`minimap.py`，推荐的巡逻坐标）
左上角「小地图」里的**黄点 = 自己在整张地图里的绝对位置**：不受镜头跟随/顶住影响，也不会被宠物名牌、怪物挡住。
- 缩略图区域用窗口自己的 UI 图样定位（`templates/_ui/minimap_title.png` 标题栏左端 → 左/上边；`minimap_title_right.png`「大地图」按钮 → 右边；
  `minimap_bottom.png` 底部 9 行条纹 → 下边），不同地图缩略图大小不同、窗口被拖动都没关系，只在区域内找黄点（标题栏按钮、表头太阳图标也是黄的，必须排除）。
  2026-08-29 前按「偏蓝灰浅色 = 边框」统计找区域，在岩壁/天空背景的地图（野猪的领土）会把窗口右边的游戏背景当成缩略图，26% 的帧丢黄点（一丢十几秒）；
  改后野猪录像 876 采样帧 0 丢失、区域恒定，树人录像不变。小地图窗口**收起**（只剩标题栏）时黄点自然没有，巡逻退回按时间掉头。
- 端点存黄点**相对缩略图左上角**的 x（`settings.yaml` → `patrol.<怪物>.left_mm/right_mm`，`mode: minimap`）；
  交给巡逻逻辑时乘 `minimap.px_scale`(10)，`patrol_tolerance` 30 px ≈ 3 个小地图像素。1 个小地图像素 ≈ 10 个画面像素（勇士部落东入口）。
- 名牌丢了但黄点还在 → 继续巡逻不松键（攻击靠名牌 ROI，名牌丢了自然不会打）。实测宠物压名牌 260 帧全部照走，LOST 0%。
- 标定：菜单「巡逻端点 → 标定端点」，走到两端按 l / r（窗口里 `mm=` 就是黄点 x，任何位置都可靠，不用看 CAM 状态），保存时选「小地图坐标」。
  按 l/r 时 `mm=--`（黄点没找到）会明确警告：那个端点只有屏幕坐标，镜头跟随时屏幕 x 不变、走多远都会被判「太近」。
  两端点间距 < 2×`patrol_tolerance`（默认 100 画面 px ≈ 10 小地图 px）时提示并询问是否仍保存（测试可以硬存；跑起来会在端点附近原地抽搐，正式用拉开到 ≥3×tolerance）。

## NDI 注意（2026-08-28 实测：B=Windows OBS 32 + DistroAV，A=Ubuntu Wi-Fi）
- 接收用 `cyndilib`（自带 libndi 6，不用装 NDI SDK），`camera.py` 里 `ndi:` 前缀走这条路；`python -c "from camera import list_ndi_sources; print(list_ndi_sources())"` 列源。
- **必须 TCP**：libndi 默认优先 RUDP(UDP)，实测 TCP 控制连接建立、tally 都通，但视频一帧不来（本机 OBS 也是走 TCP 才有画面）。
  `camera.py` 自动生成 `.ndi/ndi-config.v1.json`（只开 tcp）并设 `NDI_CONFIG_DIR`，必须在 import cyndilib 前设置。
- 取帧用 frame_sync：永远最新帧，处理慢了自动丢旧帧；实测 1920×1080@30，全流程 25 fps、单帧 25 ms。
- 任何要读实时画面的工具都必须走 `camera.FrameSource`（菜单的采集/标定/抠模板、`tools/harvest_templates.py` 都已是）；
  直接 `cv2.VideoCapture("ndi:...")` 会得到 0 帧（半自动采集曾因此"没有找到任何候选"）。
- Wi-Fi 上一路 1080p 约 110 Mbit/s；本机 OBS 同时也在收就是两路，尽量别再开第三个接收端。
- **内存泄漏已修（2026-08-28）**：cyndilib frame-sync 每次 `capture_video()` 都要配对释放，等新帧的轮询里没取数据就不会释放，
  每帧漏 8 MB → 一分多钟吃光内存、进程假死、被 OOM 杀（`journalctl -k | grep "Out of memory"` 可查）。`camera.py` 轮询到同一帧时现在会 `memoryview(vf).release()`。
- **不要把 B 机改成 720p 输出**：模拟实测名牌假峰从 0.48 涨到 0.68（阈值 0.70），会跟错；处理耗时也不会少（矫正后都是 1280×720）。
  掉帧先查本机 CPU：跑图时别同时跑采集/去重/离线分析，A 机 OBS 不看画面时可以把它的 NDI 源停掉。
  单帧开销见下面「当前指标」：模板匹配占 70%，`detection.idle_skip` / `app.cv_threads` / 名牌粗到精搜索三项默认已开，30 fps 不掉帧、CPU 134%。
- **控制端自己别开 NDI 输出**：本机 OBS 若也开了 DistroAV 主输出，源名会和被控端撞车（都叫 Game-PC），`camera.py` 现在优先选非本机的源并打印命中了谁；写全名（如 `ndi:THINKPAD`）最保险。菜单列源要等满 3 s，别的机器的源靠 mDNS 晚 1~3 s 才出现。
- **Pico 的按键是发到 B 机当前焦点窗口的**：B 机开着 OBS 时要把游戏窗口点回前台，否则脚本"OK"了但角色不动。

## Ubuntu / Linux 注意
- 必须装 `opencv-python`（带 GUI）。若环境里有 `opencv-python-headless`（例如 easyocr 带进来的），`imshow` 会报错，先 `pip uninstall opencv-python-headless` 再 `pip install "opencv-python>=4.8,<5"`。
- `camera.py` 在 Linux 用 V4L2，并**先设 MJPG 再设分辨率**（否则 1080p 偶发协商超时读不到帧，或落到 YUYV 只有 5–10 fps）。
- `/dev/videoN` 编号随插拔顺序变化，且 UVC 摄像头会多出一个元数据节点（如 video3）不能取帧；`python -c "from camera import list_cameras; print(list_cameras())"` 只列出能出图的节点。
- Insta360 Ace Pro 2（webcam 模式）通过 UVC 只暴露 Brightness，曝光/白平衡/对焦无法从 A 端锁定，靠环境光稳定（避免窗户强光直射）。

## 标定文件
`calibration/homography.json` 是当前画面来源的四角（`--calibrate` 手动 / `--auto-calib` 自动 都会覆盖它）；现在是 NDI 的自动标定（游戏区域 1853×1042 在 1920×1080 画布左上）。
`homography_insta360_20260827.json` 是之前 Insta360 拍屏的标定（换回相机时 `--calib` 指定它）；`homography_shot_mp4.json` 是 shot.mp4 对应的标定。
**所有命令都支持 `--calib 路径`**：离线处理某段视频时务必指定它自己的标定文件，否则实机标定和离线视频会互相打架、模板和检测全部错位。

## 采集怪物模板（推荐：半自动，实机流程）
1. 正常运行 `python app.py --source 0`，画面里出现怪物时按 `t` 手抠 **2–3 张**种子模板。
2. 让怪物多出现一会儿（走动、攻击、不同姿态），用摄像头现场录 2 分钟并扫描：
```bash
python tools/harvest_templates.py scan --source 0 --seconds 120 --seeds templates/stump_map01 --name stump_map01
```
   （也可以在 app.py 里按 `r` 录制到 `recordings/`，之后 `--source recordings/rec_xxx.mp4` 扫描；实机标定直接沿用，不用 `--calib`。）
打开 `harvest/stump_map01/sheet.jpg`，记下真怪物的编号，再：
```bash
python tools/harvest_templates.py pick --name stump_map01 --ids 0,3,5-9,12 --out templates/stump_map01
```
框由算法按匹配位置对齐、尺寸统一；挑选时**跳过**闪白/被遮挡/模糊的候选。`--step` 控制抽帧间隔，`--thr` 控制候选宽松度（默认 0.55）。

## 原版精灵图模板（2026-08-28 起推荐）

游戏客户端（Unity 版）的 `aa/` 目录里有全部怪物的精灵图集（`Assets/WzAssets/SpriteSheet/CN/Mob/<id>_0.png`，RGBA，
带透明通道）。`tools/wz_sprites.py` 直接从 Unity 包里流式取出（Mob 包解压后 8.8 GB，不整包载入），
按 `.wzspritesheet` 里的帧矩形表切成单帧，存成 `templates/<集>/wz_<id>_<帧>.png`。运行时只比精灵像素（masked 匹配），
背景是什么都不影响：实测真怪 0.85~0.94、背景 ≤0.6，`harvest_yezhu.mp4` 上命中帧 576→1106，且没有岩壁/宠物/金币误报。

```
python tools/wz_sprites.py list --grep 2230                    # 列 Mob ID
python tools/wz_sprites.py atlas --mob 2230102 --out /tmp/x     # 看图集是哪种怪
python tools/wz_sprites.py extract --mob 2230102 --mob 1130100 --out templates/yezhu   # 菜单「怪物模板 → 从游戏原版精灵图导入」同此
python tools/wz_sprites.py extract-all                          # 全部怪 -> templates/_wz 模板库 + 中文名 + 缩略图（菜单搜索用）
python tools/wz_sprites.py scale --mob 2230102 --source recordings/harvest_yezhu.mp4    # 标定 detection.sprite_scale（本机 0.93）
```
**全怪物模板库（推荐用法）**：`python tools/wz_sprites.py extract-all` 一次把 Mob 包里全部 858 只怪导成 `templates/_wz/<id>/wz_<id>_<帧>.png`
（动画帧去重），并生成 `catalog.json`、`names.json`（客户端 String 表里的中文名，839 只有名字）和缩略图 `sheet_XX.jpg`（每页 100 只按 ID 排）。
之后换图流程 = 「怪物模板 → 新建怪物（名字按地图起）」→「**搜索精灵库添加怪物**」输 `野猪`/`2230` 之类关键字、选编号 → 帧复制进当前集 → 标端点 → 开始检测。
找不到名字的怪用「浏览精灵库缩略图」按图找 ID。库目录不进 git（可重建）。
已知 ID：野猪 2230102、斧木妖 1130100、树桩 0130100、黑斧木妖 1140100、绿蘑菇 1110100、蘑菇 2230101。
限制：被宠物/角色挡住大半的怪、被技能特效盖住的怪，masked 分会掉到 0.6 以下（手抠模板 + 运动门槛反而能认）；
需要的话把 `_manual/` 里的手抠模板放回目录，两种模板可混用（耗时相加）。检测框高度要 ≥ 最高的精灵帧（斧木妖 93×0.93≈87 px，up 70 + down 20 刚好）。

## 工具
- `tools/crop_templates.py --source ../shot.mp4 --out templates/stump_map01`：在矫正后的画面上拖框抠模板（a/d 翻帧，s 保存）。
- `tools/review_log.py --source ../shot.mp4 --log logs/run_xxx.jsonl --out review.jpg`：把日志叠加到抽帧图上，人工核对检测结果。
- `tools/analyze_run.py logs/run_xxx.jsonl`：真机日志统计——来回趟数与每趟用时、STUCK/RECOVER/GIVEUP 事件、LOST 比例、玩家 x/y 范围。
- `tools/event_frames.py logs/run_xxx.jsonl recordings/rec_xxx.mp4 out.jpg [--frames 100,200]`：把 STUCK/掉头等事件时刻的录像帧矫正后拼图（需 `--record` 录的同步录像）。
- `tools/crops.py logs/run_xxx.jsonl recordings/rec_xxx.mp4 out.jpg 起始帧 结束帧 步长`：玩家周围小图按帧拼条带，看角色在做什么。
- `tools/measure_scale.py --mob 2230102 [--source 录像] [--write]`：标 `detection.sprite_scale`——暂停后拖框圈住一只怪、回车，几秒出最佳缩放比（`wz_sprites.py scale` 的快速版，换窗口大小后必做）。
- `tools/nameplate_eval.py --source 录像 --template templates/players/KEEEE.png [--calib ...] [--truth 真值.jsonl]`：名牌整块 NCC vs 文字掩码 NCC 离线对比（分数分布、峰差、位置一致率、最差帧拼图；有真值时给命中率/错锁率），决定要不要开 `text_mask`、阈值怎么标。
- `tools/wz_map.py find 野猪` / `export --map 101040001` / `platforms --map 101040001`：从客户端 `aa/` 读地图原版几何（foothold 平台、miniMap 参数、传送门、绳梯、刷怪点），格式说明见文件头；名牌兜底和自动巡逻路线的数据来源。

## 换地图 / 换角色
- 新地图：建 `templates/<怪物名>/`（或运行中按 `t` 直接拖框保存），抠 8–16 张怪物模板（不同动作、含被打击帧），`monster.current` 改为新目录名，或启动时在菜单里选。
- 换角色名：重抠 `templates/player_nameplate.png`（名字 + 公会牌区域），并调整 `anchor_dx/dy`（名牌左上角到脚底中心的偏移）。

## 当前指标（2026-08-27 真机，woniu-mogu 地图，`run_20260827_132650`）
- 端点巡逻 150 s：**15 个来回**，单趟 4.0 s（中位），LOST 0%，STUCK 2 次（自行恢复），玩家定位分 0.92，单帧 13.6 ms。
- 10 min 稳定性（`run_20260827_133139`）：**57 个来回**，单趟中位 4.8 s，LOST 0%，STUCK 5 次（均自行恢复），无卡键/断连/崩溃，29.8 fps。
- 同一段代码在发送节拍修复前（`run_20260827_130712`）：150 s 仅 5 个来回、STUCK 33 次、单趟 14–120 s —— 差别全在 Pico 丢包。
- 模板注意：被打击时闪白/半透明的帧**不要**做模板（曾有一张这样的模板在空白天空上误报 0.8）。
- **单帧开销（2026-08-28，yezhu 21 张模板 ×翻转，1 个 ROI，`harvest_yezhu.mp4` 1152 帧）**：模板匹配 21.4 ms（70%，42 次 matchTemplate）、
  名牌定位 4.5 ms（局部 1.5 + 丢失后全局重搜 11–16 ms/次）、读帧 2.1、矫正 0.9–3、背景滚动 1、小地图 0.3，其余可忽略；合计 ~30 ms ≈ 帧周期，
  真机（`run_20260828_141513`）只有 25.4 fps、p90 54 ms。三项优化后（`cv_threads: 2` + 名牌粗到精 + `idle_skip: 2`）录像回归 raw/detected/player 逐帧一致
  （检测事件数相同、个别事件晚 1 帧），真机 60 s **30.0 fps 零掉帧、进程 CPU 134%**（原 ~240%），空闲帧 ~9 ms、检测帧 ~25 ms。
  试过不值得做的：iGPU/MX150 OpenCL（30 ms 比 CPU 20 ms 还慢，小 kernel 启动开销）；再删模板（两两相似度都 <0.75，已无冗余）。
  还能做的：匹配降到 0.5 倍尺度「粗到精」（模板匹配 21.9 → 5.2 ms/ROI，但要用标注帧重标 threshold/max_dist/edge_min）。

## 已知限制 / 下一步
- **Pico v1 固件收不了连发包**（见 `pico/README.md` P1 v2）：A 端已用 ≥60 ms 节拍队列规避；攻击/拾取按键积压 ≥3 时丢弃。
  换向延迟 ≈ 120 ms（KEY_UP + KEY_DOWN 两拍）+ 游戏响应，角色会越过端点 ~50 px 再掉头，属正常。
- 屏幕 x 只在镜头被地图边界顶住时等于地图位置；这张图约 1.76 屏宽，角色屏幕 x 在 500–800 时镜头在跟随。端点标定窗口有 CAM 指示。
- 跳跃恢复只能越过矮台阶；高墙 3 跳无效后掉头。地图中段的坑（左右皆墙）进去就出不来，端点要标在坑外。
- 摄像头移位需人工重新 `--calibrate`，暂无自动检测。

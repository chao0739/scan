# game_vision — 外置摄像头游戏目标检测 MVP

摄像头拍摄显示器上的冒险岛画面 → 透视矫正 → 定位玩家 → 截取玩家前方 ROI → 模板匹配树桩（木妖）→ 多帧去抖 → 输出 `detected=True/False`。

## 环境
Python 3.10+，依赖见 `requirements.txt`：

```bash
pip install -r requirements.txt
```

## 快速开始（菜单式，推荐）
```bash
python menu.py
```
数字选功能即可，不用记命令。首次使用按顺序做：

1. **设置 → 摄像头**（默认 0；可填编号或名称关键字，Ubuntu 上填 `Insta360` 即可，编号变了也能找到；菜单会列出检测到的摄像头）
2. **标定屏幕四角**：依次点击游戏画面 左上 → 右上 → 右下 → 左下，按 `s` 保存
3. **玩家设置 → 新增玩家**：输入玩家 ID，点一下弹窗取焦点 → 按空格定格 → **贴紧框住名牌**（黑底白字的名字条，有公会牌一起框；不要带角色的脚和背景，否则定位会乱跟）→ 按 `s`
4. **怪物模板 → 新建怪物** → **手动抠模板** 抠 2–3 张种子 → **半自动采集**（录 2 分钟 → 自动弹出候选缩略图 → 输入编号保存）
   → **模板去重**（模板超过 ~20 张时做一次：耗时 ≈ ROI 面积 × 模板数 × 2，45 张≈110ms/帧，15 张≈40ms）
5. **开始检测**

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

## 输出
- 控制台：状态变化时和每 N 帧打印一行 JSON。
- `logs/run_*.jsonl`：每帧一行，字段 `timestamp / frame / map_id / detected / raw / score / template / roi / player / player_score / latency_ms`。
  对外只需关心 `detected`；其余用于调参。

## 配置 `config.yaml`
| 段 | 关键项 | 说明 |
|---|---|---|
| camera | source / width / height | 摄像头编号 / 名称关键字（如 `Insta360`）/ 视频路径；建议 1080p 采集 |
| screen | output_width/height | 矫正后虚拟游戏画面尺寸（**模板尺寸与其绑定**，改了要重抠模板） |
| roi.player | template / match_threshold / local_threshold / track_window | 用玩家名牌 `KEEEE+公会牌` 定位玩家，带局部跟踪 |
| roi | facing / near_offset / far_offset / up / down | 玩家前方 ROI 的范围（up/down 只覆盖同一层，默认 70/20；上层平台的怪打不到不算）。`facing`: `auto`(按前进方向，见下) / `right` / `left` / `both` |
| roi.facing_auto | window / min_move | 前进方向判定：累计最近 N 帧的“角色屏幕位移 − 背景滚动位移”，超过 min_move 像素才切换方向 |
| detection | threshold / color_verify.max_dist / edge_min / topk / flip | 模板分阈值（开校验后 0.55）；候选框颜色直方图距离上限 0.48；边缘图匹配下限 0.4；每模板检查的峰数；flip 自动加水平翻转模板 |
| debounce | window_size / enter_min_hits / exit_min_misses | 滞回去抖：5 帧中 ≥3 命中进入，≥4 未命中退出 |
| control | mode / attack_key / attack_interval_ms / attack_range_px / walk_max_ms … | 决策：off 只检测 / dry 只打日志 / pico 真发按键。有怪：转身→接近→攻击；没怪：巡逻掉头。`p` 暂停 |
| monster | current / ask_on_start | 怪物模板集（`templates/<name>/`）；启动时菜单选择，运行中 `m` 切换 |

## Ubuntu / Linux 注意
- 必须装 `opencv-python`（带 GUI）。若环境里有 `opencv-python-headless`（例如 easyocr 带进来的），`imshow` 会报错，先 `pip uninstall opencv-python-headless` 再 `pip install "opencv-python>=4.8,<5"`。
- `camera.py` 在 Linux 用 V4L2，并**先设 MJPG 再设分辨率**（否则 1080p 偶发协商超时读不到帧，或落到 YUYV 只有 5–10 fps）。
- `/dev/videoN` 编号随插拔顺序变化，且 UVC 摄像头会多出一个元数据节点（如 video3）不能取帧；`python -c "from camera import list_cameras; print(list_cameras())"` 只列出能出图的节点。
- Insta360 Ace Pro 2（webcam 模式）通过 UVC 只暴露 Brightness，曝光/白平衡/对焦无法从 A 端锁定，靠环境光稳定（避免窗户强光直射）。

## 标定文件
`calibration/homography.json` 是实机摄像头的四角（`--calibrate` 会覆盖它）；`homography_shot_mp4.json` 是 shot.mp4 对应的标定。
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

## 工具
- `tools/crop_templates.py --source ../shot.mp4 --out templates/stump_map01`：在矫正后的画面上拖框抠模板（a/d 翻帧，s 保存）。
- `tools/review_log.py --source ../shot.mp4 --log logs/run_xxx.jsonl --out review.jpg`：把日志叠加到抽帧图上，人工核对检测结果。

## 换地图 / 换角色
- 新地图：建 `templates/<怪物名>/`（或运行中按 `t` 直接拖框保存），抠 8–16 张怪物模板（不同动作、含被打击帧），`monster.current` 改为新目录名，或启动时在菜单里选。
- 换角色名：重抠 `templates/player_nameplate.png`（名字 + 公会牌区域），并调整 `anchor_dx/dy`（名牌左上角到脚底中心的偏移）。

## 当前指标（shot.mp4，1296 帧）
- 单帧处理 ≈ 22 ms（`facing: auto`，单侧 ROI）/ ≈ 48 ms（`both`，双侧），满足 < 50 ms。
- 在 132 个“难例”候选上（47 真树桩）：阈值 0.78 命中 38、误报 1；单帧漏检由去抖覆盖。
- 模板注意：被打击时闪白/半透明的帧**不要**做模板（曾有一张这样的模板在空白天空上误报 0.8）。

## 已知限制 / 下一步
- 背景岩石纹理分数可达 0.75–0.76，与弱命中（0.73–0.75）有重叠，阈值余量薄；真实摄像头数据到手后需重新标阈值，必要时增加模板或加彩色二次校验。
- `facing: auto` 靠走动方向判断，玩家**原地转身不走动**时会沿用之前的方向；刚启动未知方向时两侧都检。后续若由程序控制角色，应直接用按键状态提供朝向。
- 摄像头移位需人工重新 `--calibrate`，暂无自动检测。

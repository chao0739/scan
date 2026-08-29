# 冒险岛外置视觉自动巡逻 — 安装 / 部署（final v3，2026-08-29）

两台电脑：**D 跑游戏，C 跑脚本**。项目说明见 `game_vision/README.md`，开发记录见 `docs/PROGRESS.md`，早期规格/路线图在 `archive/`。

角色分工（和现在的 B/A 一样）：

| 电脑 | 干什么 | 要装什么 |
|---|---|---|
| **D 游戏机**（Windows） | 跑冒险岛；OBS 把游戏画面用 NDI 发到局域网；**Pico 插在这台机的 USB 上**给游戏发按键 | 游戏、OBS + DistroAV（NDI 输出插件）、Pico 用 USB 线插上 |
| **C 脚本机**（Ubuntu / Windows / macOS 都行） | 收 NDI 画面 → 识别 → 通过 Wi-Fi 给 Pico 发命令 | 本包（`install.sh` / `install.bat`） |

三者（C、D、Pico）在**同一个局域网**。Pico 只连 2.4 GHz Wi-Fi。

## 第 0 步：在旧电脑上打包（一次）
```bash
cd game_vision
python tools/pack.py              # → dist/game_vision-YYYYMMDD.zip（≈1 MB：代码+配置+模板+标定+本机 settings.yaml）
python tools/pack.py --with-wz    # C 机没有游戏客户端 aa/ 目录的话，加这个把全怪物精灵库带上（≈170 MB）
```
把 zip 拷到 C 机（U 盘 / 网络都行）。

## 第 1 步：D 游戏机
1. 装 OBS 和 DistroAV 插件（https://distroav.org）。OBS 里加「游戏捕获/窗口捕获」把冒险岛窗口抓进来，画布 1920×1080。
2. OBS 菜单 工具 → DistroAV NDI 设置 → 勾「主输出」，**主输出名称**填一个好认的名字（比如 `Game-PC`）。局域网上看到的源名会是 `D机主机名 (Game-PC)`。
3. Pico：
   - 第一次用的 Pico 按 `pico/README.md` 刷 CircuitPython + `adafruit_hid` + `p1_wifi_hid/code.py`；已经用过的 Pico 直接拔过来插上就行。
   - CIRCUITPY 盘里的 `settings.toml` 填 C/D 所在 Wi-Fi 的名字和密码（2.4 GHz）。LED **常亮** = 已连上 Wi-Fi、就绪。
   - Pico 的按键发到 D 机**当前焦点窗口**：跑脚本时把游戏窗口点回前台（OBS 可以最小化）。

## 第 2 步：C 脚本机
1. 解压 zip，进目录：
   ```bash
   ./install.sh        # Windows：双击 install.bat。建 .venv、装依赖（约 100 MB），自动检查
   ./run.sh            # Windows：双击 run.bat。进入菜单
   ```
   Ubuntu 若提示缺 `python3-venv`，按提示 `sudo apt install python3 python3-venv` 后再跑一次。
2. 菜单「设置 → 画面来源」：它会列出局域网上的 NDI 源，填 `ndi:` + 你在 OBS 里起的名字（如 `ndi:Game-PC`，写一部分能唯一匹配即可）。
3. 菜单「标定屏幕四角」→ 选自动（NDI 画面整幅就是游戏，自动裁出游戏区域）。
4. 菜单「设置 → Pico」：留空 = 局域网自动发现；发现不了就填 Pico 的 IP（路由器后台能看到）。`python pico_client.py --selftest` 可单独测按键。
5. 「开始检测」。包里已带旧电脑的玩家名牌模板、怪物模板、巡逻端点，**同一个角色、同一张地图直接能用**；换角色 → 「玩家设置」重抠名牌；换地图 → 「怪物模板 → 搜索精灵库添加怪物」+「巡逻端点 → 标定端点」。

## 常见问题
- 菜单列不出 NDI 源：C 和 D 不在同一网段 / D 机防火墙拦了 OBS；Ubuntu 上还需要 `avahi-daemon` 在跑（桌面版默认有）。
- 有源但没画面：脚本已强制 TCP 传输（`.ndi/ndi-config.v1.json` 自动生成），若仍无画面看 D 机 OBS 是否真的在输出（DistroAV 设置里主输出打勾）。
- 脚本显示 OK 但角色不动：D 机焦点不在游戏窗口；或 Pico LED 不是常亮（Wi-Fi 没连上 / 5 GHz）。
- OpenCV 窗口打不开（Ubuntu 最小安装）：`sudo apt install libgl1 libglib2.0-0`。

细节（配置项、工具、指标）见 `game_vision/README.md`；开发记录见 `docs/PROGRESS.md`。

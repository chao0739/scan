# Windows 全新安装指南（从装 Conda 开始）

在一台什么都没有的 Windows 电脑上，把这套系统跑起来的完整步骤。
本机实测环境：Windows 11 LTSC 2024，Miniconda + Python 3.13，全部依赖均有现成 wheel，
不需要编译器、不需要 NDI SDK、Pico 也不需要装任何驱动。

> 叫法：**控制端** = 跑脚本的机器（本文的"本机"），**被控端** = 跑游戏的机器（旧文里的 B 机 / D 机，OBS 开 NDI 输出、Pico 插在它上面）。

> **和根目录 `README.md` 的分工**：那份是官方部署流程（D 游戏机 + C 脚本机怎么配、`install.bat`
> 一键建 `.venv` 装依赖），**优先按它做**。本文补充两件它没写的：
> ① 用 conda 而非 python.org + venv 的路线；② 国内网络和 Windows 上实际踩到的坑。
> 只想快速跑起来：装好 Python 3.10+ 后直接双击 `install.bat`，再双击 `run.bat`，可跳过本文第二节。

---

## 一、装 Miniconda 和 Git

1. **Miniconda**（Windows 64-bit）：<https://www.anaconda.com/download/success> → Miniconda Installers。
   安装时选 **Just Me**，**不要**勾 "Add to PATH"（下一步用 conda 自己的方式接管 PowerShell）。
2. **Git**，三选一：

   ```powershell
   winget install --id Git.Git -e --accept-source-agreements --accept-package-agreements
   ```

   装完需关掉 PowerShell 重开，PATH 才刷新，再用 `git --version` 验证。
   winget 是从 GitHub 拉安装包，国内可能很慢；那就改用 conda（走清华镜像，快得多，
   在 `(base)` 下装则全局可用）：

   ```powershell
   conda install -y --override-channels -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge git
   ```

   或手动下安装包：官网 <https://git-scm.com/download/win>，
   国内走淘宝镜像 <https://npmmirror.com/mirrors/git-for-windows/>（选 `Git-*-64-bit.exe`），默认选项一路下一步。
3. 打开开始菜单里的 **Anaconda PowerShell Prompt**，让普通 PowerShell 也能用 conda：

   ```powershell
   conda init powershell
   ```

   关掉窗口重开一个 PowerShell，提示符前出现 `(base)` 即成功。

> **坑 1**：没激活 conda 环境时直接敲 `python`，会命中微软商店的占位程序（`WindowsApps\python.exe`），
> 运行后什么都不输出也不报错。确认提示符里有 `(base)` 或 `(scan)` 再跑。

> **坑 2**：`conda init` 后重开窗口报「无法加载文件 ...\profile.ps1，因为在此系统上禁止运行脚本」，
> 是 PowerShell 执行策略拦住了。放开当前用户即可（只影响该账户，网络下载的脚本仍要求签名）：
>
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force
> ```
>
> 跑完再关掉窗口重开。另注意 `conda init powershell` 写的是 **Windows PowerShell 5.1** 的配置文件；
> 若你用 PowerShell 7（`pwsh`），配置文件路径不同，需在 7 里再执行一次 init。

> **winget 提示「已安装，找不到可用的升级」**：不是报错，说明机器上已装过 conda，跳过安装即可。
> 用 `winget list --name conda` 确认，再定位 `conda.exe`（常见于 `C:\Users\<用户名>\miniconda3\Scripts\`
> 或 `C:\ProgramData\miniconda3\Scripts\`），用全路径执行 `conda init powershell`。

> **base 的 Python 版本不重要**：winget 装的 Miniconda 可能自带 Python 3.14，
> 而 opencv-python / cyndilib 尚无对应 wheel。所以**不要往 base 里装依赖**，
> 一律按下一节建 Python 3.12 的独立环境。

---

## 二、拉代码、建环境

仓库 origin 是 SSH 地址；新电脑没配 SSH key 就用 HTTPS 克隆：

```powershell
git clone https://github.com/chao0739/scan.git $env:USERPROFILE\Desktop\scan
```

建独立环境（README 要求 Python 3.10+；3.12 各依赖 wheel 最齐）：

```powershell
conda create -n scan python=3.12 -y
conda activate scan
pip install -r $env:USERPROFILE\Desktop\scan\game_vision\requirements.txt
```

验证：

```powershell
python -c "import cv2, numpy, yaml, cyndilib; print(cv2.__version__)"
```

打印 `4.x.x` 即可。

### 服务条款报错（CondaToSNonInteractiveError）

新版 conda 建环境时可能报：

```
CondaToSNonInteractiveError: Terms of Service have not been accepted for the following channels
    - https://repo.anaconda.com/pkgs/main ...
```

Anaconda 默认渠道对**员工 200 人以上的组织需要付费授权**。最省事的做法是改用社区维护的
conda-forge，没有这层条款；本项目依赖全部走 pip 安装，不受渠道影响：

```powershell
conda create -n scan python=3.12 -y --override-channels -c conda-forge
```

`--override-channels` 表示本次只用指定渠道、不碰 Anaconda 官方源，因此不会再触发条款检查。

### 国内网络：换清华镜像

conda 和 pip 都要换。建环境时直接指向清华的 conda-forge 镜像（同样带 `--override-channels`，
顺便解决上面的条款问题）：

```powershell
conda create -n scan python=3.12 -y --override-channels -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge
```

清华源不通就把域名换成中科大 `mirrors.ustc.edu.cn`，路径相同。

pip 换源（写进用户配置，只需设一次）：

```powershell
conda activate scan
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

之后再 `pip install -r requirements.txt` 即可。

### 依赖清单与作用

| 包 | 作用 | 备注 |
|---|---|---|
| `opencv-python>=4.8,<5` | 取帧、透视矫正、模板匹配、GUI 窗口 | **不要装 `opencv-python-headless`**（无 `imshow`）；5.x 未验证 |
| `numpy` | 图像数组运算 | |
| `pyyaml` | 读 `config.yaml` / `settings.yaml` | |
| `cyndilib>=0.1.1` | 仅 NDI 源需要，自带 libndi | PyPI 有 win_amd64 wheel，无需装 NDI SDK |

用摄像头拍屏（不走 NDI）时 `cyndilib` 可以不装；`camera.py` 只在 `source` 以 `ndi:` 开头时才 import。

---

## 三、精灵库（从游戏客户端导出原版怪物模板）

`final v3` 起支持直接用客户端的原版精灵图当模板。原因见 `tools/wz_sprites.py` 头注释：
手抠模板带着当时的背景，换背景（棕色岩壁）匹配分掉到 0.5~0.65；原版精灵图带 alpha，
可以只比精灵像素做遮罩匹配，真怪 0.85~0.94、背景 ≤0.6。

**1. 装工具依赖**（UnityPy / lz4，只有要导出的机器需要）：

```powershell
pip install -r game_vision\requirements-tools.txt
```

官方等价做法是 `install.bat --tools`（走 `.venv` 路线时用）。

**2. 指向客户端资源目录**。`config.yaml` 里 `wz.aa_dir` 是仓库作者机器的 Linux 路径，必须改：

```yaml
wz:
  aa_dir: C:\Users\<用户名>\Desktop\scan\aa
```

`aa` 是 Unity Addressables 目录，在游戏安装目录的 `<游戏名>_Data\StreamingAssets\aa\`，约 2.2 GB。
`aa/w/spritesheet_*.bundle` 里是怪物图集，脚本只按需解压需要的 LZ4 块，不整包载入。
该目录已在 `.gitignore` 里，不入库。找不到客户端路径时（游戏开着）：

```powershell
Get-Process | Where-Object { $_.Path -like "*aple*" } | Select-Object Name, Path
```

**3. 生成精灵库**（约 1 分钟，输出到 `templates/_wz/`，同样不入库）：

```powershell
python tools\wz_sprites.py extract-all
```

生成后菜单里「怪物模板 → 搜索精灵库添加怪物」可按中文名或 ID 直接加怪，
「浏览精灵库缩略图」按图找 ID（每页 100 只）。

**4. 标缩放比**。精灵是客户端原始尺寸，要按游戏窗口与 1280×720 的比例缩放才匹配得上。
`config.yaml` 里 `detection.sprite_scale` 默认 0.93（作者机 1854×1042 窗口实测），
**偏 0.03 匹配分就掉 0.1**，换了窗口大小必须重标：

```powershell
python tools\wz_sprites.py scale --mob <怪物ID> --source <录像文件>
```

> 没有客户端 `aa/` 的机器：让有客户端的机器用 `python tools/pack.py --with-wz` 打包，
> 精灵库（约 170 MB）会一起带过去，对方不用装 UnityPy/lz4。

---

## 四、Pico 2 W 固件（只做一次）

电脑端不需要装任何东西，全靠 U 盘拖拽。详见 [`pico/README.md`](../pico/README.md)：

1. 按住 **BOOTSEL** 插 USB → 出现 `RP2350` 盘 → 拖入 **Pico 2 W** 的 CircuitPython `.uf2`
   （<https://circuitpython.org/board/raspberry_pi_pico2_w/>，别下成 Pico / Pico W / Pico 2 的）。
2. 重启后出现 `CIRCUITPY` 盘 → 把 Adafruit bundle 里的 `lib/adafruit_hid/` 整个文件夹复制到盘里的 `lib/`。
3. 把 `pico/p1_wifi_hid/settings.toml.example` 复制到盘根目录、改名 `settings.toml`，
   填 Wi-Fi 名和密码（**必须 2.4GHz**，Pico 2 W 不支持 5GHz）。
4. 把 `pico/p1_wifi_hid/code.py` 复制到盘根目录。LED **常亮 = 已就绪**。
5. 电脑与 Pico 在同一局域网下自检（3 秒内把光标放进记事本）：

   ```powershell
   cd $env:USERPROFILE\Desktop\scan\game_vision
   python pico_client.py --selftest
   ```

> **坑 2**：首次运行 Windows 防火墙会弹窗，必须勾 **专用网络** 允许，否则 UDP 广播发现不到 Pico，
> 报「未发现 Pico」。事后补救：Windows 安全中心 → 防火墙和网络保护 → 允许应用通过防火墙 → 找到 python.exe 勾上专用网络。
> 也可以绕过发现、直接指定 IP：`python pico_client.py --host 192.168.1.50`。

---

## 五、画面来源（二选一）

- **NDI（推荐）**：游戏机（B 机）装 OBS + DistroAV 插件开 NDI 输出，本机**不用**装 OBS。
  `camera.source` 填 `ndi:源名关键字`（如 `ndi:Game-PC`）。
  `camera.py` 会自动生成 `.ndi/ndi-config.v1.json` 强制走 TCP（libndi 默认的 RUDP 实测收不到帧）。
- **摄像头拍屏**：插上即用，Windows 走 DirectShow（`cv2.CAP_DSHOW`），不用装驱动。
  `camera.source` 填编号或名称关键字（如 `Insta360`）。

---

## 六、启动与首次配置

```powershell
conda activate scan
cd $env:USERPROFILE\Desktop\scan\game_vision
python menu.py
```

首次按 [`game_vision/README.md`](../game_vision/README.md) 的顺序做一遍：
设置摄像头/NDI 源 → 标定屏幕四角 → 新增玩家（框名牌）→ 建怪物模板 → 标巡逻端点 → 开始检测。

> **注意**：标定文件、`settings.yaml`、玩家名牌和怪物模板都跟具体画面来源与分辨率绑定，
> 换电脑 / 换画面来源（相机 ↔ NDI）后都要在新画面上重做，不能直接复制旧机器的。
> `settings.yaml` 和 `logs/`、`recordings/`、`harvest/` 已在 `.gitignore` 里，不会跟着仓库走。

日常只需 `python menu.py` → `1`。

---

## 七、Windows 平台适配说明（无需改代码）

代码已按平台分支，Windows 路径均已覆盖：

- `camera.py:34` 按 `sys.platform` 选后端：Windows → `CAP_DSHOW`，Linux → `CAP_V4L2`。
- Linux 专用的 `fcntl` / `/dev/video*` / sysfs 枚举只在 Linux 分支内 import，Windows 不会触及。
  Windows 下 `list_cameras()` 改为逐个 index 试探。
- `camera.py:225` 的「先设 MJPG 再设分辨率」只对非 Windows 生效。
- Pico 走 Wi-Fi UDP（端口 5000），不占串口、不需要 CH340 之类的驱动。

已实测：全部模块在 Windows + Python 3.13 下 import 通过，`list_cameras()` 正常返回设备列表。

---

## 八、常见问题速查

| 现象 | 原因 / 处理 |
|---|---|
| 敲 `python` 无任何输出 | 命中微软商店占位程序，先 `conda activate scan` |
| `conda create` 报 ToS 未接受 | 加 `--override-channels -c conda-forge` 改用社区渠道 |
| 重开窗口报「禁止运行脚本」 | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force` 后再重开 |
| pip 下载龟速 / 超时 | 换清华源：`pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple` |
| `pico_client.py` 报「未发现 Pico」 | 防火墙没放行 python.exe（专用网络），或用 `--host <IP>` 直连 |
| `imshow` 报错 | 环境里混进了 `opencv-python-headless`，先卸载再装 `opencv-python` |
| NDI 连上但一帧不来 | 传输没走 TCP，检查 `.ndi/ndi-config.v1.json` 是否生成 |
| 摄像头列表报 DSHOW 警告 | opencv 5.x 下的已知噪音，装回 4.x 即可 |
| Pico 插上只有 `RP2350` 盘 | 还没刷 CircuitPython |
| 没出现 `CIRCUITPY` 盘 | USB 线是纯充电线，换一根支持数据的 |

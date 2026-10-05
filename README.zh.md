# SprocketModManager

**中文** | [English](README.md)

Sprocket 模组注册表、GitHub Pages 目录与 Windows/Linux 桌面客户端。

仓库只人工维护模组级基础 meta。GitHub Actions 每小时从每个模组仓库读取一次 Release，
把规范化的版本、tag 与资产写入 Pages 的 `data/packages.json`；网页和默认客户端不直接消耗匿名
GitHub API 配额。二进制仍始终来自模组自己的 GitHub Release。客户端使用快照求解依赖、
验证可用的发布者 SHA-256、按安装规则与 PE 元数据判断文件类型，再将文件事务式安装到供给
该类型的加载器声明的目录（`{Sprocket}/Mods`、`{Sprocket}/BepInEx/plugins` 等）。

## 社区

模组发布公告发在 Sprocket 官方 Discord 服务器的 `#mod-releases` 频道：

- 服务器邀请：https://discord.com/invite/baFH43keyR
- 发布频道：https://discord.com/channels/788349365466038283/1531947654957891614

## 当前纵向场景

```text
furryaxw.sprocket-laser-rangefinder
  -> furryaxw.sprocket-depth
  -> GitHub Releases
  -> SprocketDepth.dll              -> UserLibs/
  -> SprocketLaserRangefinder.dll   -> Mods/
```

该场景已使用两个真实 Release 通过下载、远端 digest 校验、DLL 分类、隔离目录安装、状态记录、
主包卸载和孤立依赖清理。

## 本地模组识别

客户端会静态读取已安装 DLL 的 `MelonInfo` 与 `Sprocket.Mod.*` 程序集元数据，
把它们与 Registry 条目和安装记录对齐。

读哪些目录由**运行时标识符**按当前环境决定：标识符声明的能力在场时才激活，判据是已装的
同命名空间供给者、环境里的能力，或**磁盘上检测到运行时**（管理器之外装上的加载器同样算数）。
目录来自已装供给者的 `supply` 表——桥接加载器（`provides: lavagang.melonloader`）把模组安家到
`MLLoader/Mods` 时，清单、认领与启用/禁用都跟着走；没有已装供给者时用检测到的布局。三者都
不成立时这个运行时的目录不读，也不会凭空列出文件。MelonLoader 的检测是游戏根目录的
`version.dll` 代理加 `MelonLoader/net*/MelonLoader.dll`（桥接布局是 `MLLoader/`），版本取运行时
DLL 的 PE 版本信息；BepInEx 的标识符检测 `winhttp.dll` / `doorstop_config.ini` 加
`BepInEx/core/BepInEx*.dll`，只声明 `BepInEx/plugins` 与 `BepInEx/patchers` 两个目录，不认任何
身份：那里的文件只会作为本地未知条目出现，没有名字、版本或声明 ID。

细节见 [`docs/architecture.zh.md`](docs/architecture.zh.md)。

## 加载器管理

加载器就是注册表里的普通条目：`mods/lavagang/melonloader.json` 与 `mods/bepinex/bepinex-be.json` 的
`kind` 是 `modloader`，用 `supply` 声明它供给别的包哪些类型、各自装在哪，用 `provides` 声明它的
兼容性能力。客户端在加载器页列出每个 `modloader` 包：装没装、已装版本、能装的最新版、当前环境
的兼容判定，以及它供给的类型与目录。安装、更新和卸载都走普通安装管线（解析 → 准备 → 应用），与
模组共用同一套下载主机限制、发布者 SHA-256 校验、ZIP 限制和事务安装；加载器自己的载荷按
`install.payload` 落进游戏根目录，内容映射到供给类型时也可以按 `install.files` 安装。基础运行时
不记逐文件清单：它的安装记录只留版本、发布资产和安装时落地的顶层条目（加载器自己的目录，以及
游戏根目录里的代理文件），卸载按这份清单交还整棵树与代理 DLL。

模组的安装规则里写了哪个类型，求解器就把供给该类型的加载器一起放进同一个安装计划，所以安装一个
MelonLoader 模组会在同一事务里装上 MelonLoader。加载器供给的能力版本是兼容性轴之一。

## 运行

```powershell
.\.venv\Scripts\python.exe modman.py
```

设置页可以持久启用诊断模式；也可以通过 `--debug` 为本次启动强制开启。两者按 OR 计算。
诊断模式会记录 `DEBUG` 级别日志并启用 WebView2 调试；普通启动记录 `INFO` 及以上级别：

```powershell
.\.venv\Scripts\python.exe modman.py --debug
.\SprocketModManager.exe --debug
```

管理器日志位于 `%LOCALAPPDATA%\SprocketModManager\Latest.log`。每次启动都会清空
`Latest.log`，将上一轮日志保存为带时间戳的历史文件，并只保留最新 5 份。关于页面可以直接打开
该目录。侧栏的「上传日志」列出可上传的来源：管理器日志始终可用，游戏目录里检测到的运行时
日志（`MelonLoader\Latest.log`、`BepInEx\LogOutput.log`）在文件存在时才列出；选定一项后
上传，并返回可复制的公开链接。

GUI 使用 Windows Edge WebView2 的硬件加速渲染，Python 继续负责 Registry、扫描、依赖
解析与安装。GUI 支持批量选择；单项安装和批量安装共用一个顺序下载队列。队列
运行期间仍可继续浏览并追加任务，正在执行安装事务时客户端会等待事务完成后再退出。模组列表
显示简介；详情头部集中显示名称、ID、版本和作者，正文会读取登记仓库的默认 README，使用
GitHub 渲染结果并在本地净化后显示。安装确认页会列出 Registry 声明的推荐模组，默认不勾选，
只有用户主动选择后才会一起加入安装队列。Registry 标记为“新安装推荐”的模组只会在当前
运行时的模组目录（没有桥接加载器时就是 `Mods`）中没有任何 DLL 时显示星标并固定在当前排序
顶部；已有任意模组后恢复普通排序。该标记不会弹窗、自动勾选或自动安装。

加载目录和刷新“已安装”页面时，客户端会扫描活跃运行时标识符的目录中尚未受控的 DLL。
只有文件名、静态安装目标和 GitHub Release 提供的 SHA-256 完全匹配且结果唯一时才会自动接管；
未知、本地修改、缺少摘要或存在多重匹配的文件保持不受控。接管后的模组可以正常更新和卸载；
其余 DLL 在“已安装”页标为“仅本地”，只显示文件名和路径，不能更新或卸载。

CLI 使用本地 Registry：

```powershell
.\.venv\Scripts\python.exe modman.py --index-dir site packages
.\.venv\Scripts\python.exe modman.py --index-dir site plan furryaxw.sprocket-laser-rangefinder --scan
.\.venv\Scripts\python.exe modman.py --index-dir site --game-path G:\Sprocket install furryaxw.sprocket-laser-rangefinder
```

CLI 全局参数必须写在子命令前。远端 Registry 默认地址为
`https://sprocketmods.furryaxw.top/data`，下面分 `packages.json`、`environment.json` 与
`diagnosis.json` 三份文件。

## 在 Linux 上运行

Linux 客户端可以从源码跑，也可以用 `build_linux.sh` 打成单文件；两种方式的数据都在
`~/.sprocket-mod-manager`。`requirements.txt` 在 Linux 上会装上 PyQt6 与 Qt WebEngine，
pywebview 用它们代替 WebView2：

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python modman.py
```

Qt 还需要桌面发行版自带的 X11 运行库（Debian/Ubuntu 上是 `libxcb-cursor0`、`libxkbcommon-x11-0`
这些）；裸窗口管理器或容器里可能要自己装。

Qt WebEngine 默认开硬件加速。某次启动没能把窗口带起来（部分 Mesa 驱动的 GPU 路径会崩）会记在
`~/.sprocket-mod-manager` 里，之后的启动改用软件渲染；`--disable-gpu` 让这一次走软件渲染，
`--enable-gpu` 清掉记录再试一次硬件加速，`QTWEBENGINE_CHROMIUM_FLAGS` 仍然可以覆盖 Chromium 参数。
日志与配置在 `~/.sprocket-mod-manager`。`--debug` 记录 `DEBUG` 并开放 Qt 的远程调试端口，但不打开
会挡住启动的 DevTools 窗口。

游戏路径从 `~/.local/share/Steam`、`~/.steam/steam` 与 Flatpak 版 Steam 目录下的游戏库中检测。
Sprocket 通过 Proton 运行，用的是 Wine 自带的代理 DLL，会忽略游戏目录里的那一份，所以要把
已装加载器的覆盖项加进 Sprocket 的 Steam 启动选项：

```text
WINEDLLOVERRIDES="winhttp=n,b" %command%     # BepInEx
WINEDLLOVERRIDES="version=n,b" %command%     # MelonLoader
```

启动游戏后如果没出现加载器日志（`BepInEx/LogOutput.log`、`MelonLoader/Latest.log`），就是覆盖项没写。

客户端从 `/proc` 读出游戏目录里的那个进程，游戏在跑时和 Windows 上一样会拦住安装。打开目录走
`xdg-open`，定位文件走桌面的 `org.freedesktop.FileManager1` 服务，没有该服务时退回打开所在目录。
凭据（GitHub 登录与私服会话）是 `~/.sprocket-mod-manager/credentials` 下的文件，创建时权限就是
`0600`；Windows 上同一批文件由账户的 DPAPI 密钥加密。发布资产按平台分开，这里的更新检查找的是
`SprocketModManager-linux-x64`。

`build_linux.sh` 产出那份单文件构建；它和 Windows 版一样能原地换掉自己：

```sh
sh build_linux.sh
./dist/SprocketModManager-linux-x64
```

把它放在用户可写的目录里（`~/.local/bin`、`~/Applications` 之类）：自更新做的就是替换正在运行的
这份文件，root 拥有的安装目录会拒绝。只有单文件构建能换掉自己，源码运行只报告更新并打开发布页。

用 Linux 解释器跑测试；UI 渲染相关的用例需要 `PATH` 里有 `node`：

```sh
.venv/bin/python -m unittest discover -s tests
```

## 卸载

模组、加载器与补丁包都在客户端的“已安装”页卸载；卸载按安装记录交还文件，受保护或被用户改过的
文件保留。

程序本体不写注册表、不建快捷方式，删除本体（`SprocketModManager.exe`、
`SprocketModManager-linux-x64`）即完成卸载。管理器另有两处状态目录，可以按需清理：管理器目录
（`%LOCALAPPDATA%\SprocketModManager`、`~/.sprocket-mod-manager`；配置、日志与 WebView 存储），
以及 `<游戏目录>/SprocketModManager`（安装记录、DLL 元数据缓存与被覆盖文件的备份）。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe validate_registry.py --mods-dir mods --offline
.\.venv\Scripts\python.exe validate_registry.py --mods-dir mods
.\.venv\Scripts\python.exe gen-index.py --mods-dir mods --output-dir site
.\.venv\Scripts\python.exe gen-index.py --mods-dir mods --output-dir site --fetch-releases
```

在线校验仅调用 GitHub API；它不会克隆、构建或执行第三方模组代码。
`--fetch-releases` 使用 `GITHUB_TOKEN` 时生成与 Pages 相同的嵌入式 Release 快照。

## 构建 EXE

```powershell
.\build_exe.ps1
```

输出位于 `dist\SprocketModManager.exe`。构建脚本使用项目 `.venv`，并按
`requirements.txt` 安装缺失的打包依赖。GUI 需要 Windows 10/11 与 Edge WebView2
Runtime；受支持的 Windows 和当前 Microsoft Edge 通常已预装该 Runtime。

## 安全边界

- 只接受 HTTPS Registry 与 Release 下载地址：GitHub 来源的资产必须落在模组自己仓库的 releases 下，外部来源的资产必须落在条目声明的主机白名单里；只有 `kind` 为 `modloader` 的包可以声明外部来源。
- 文件类型与 DLL 归类只读 PE/.NET 元数据，不使用 `Assembly.Load`。
- ZIP 限制条目数、单文件/总解压体积和压缩比，并拒绝绝对路径、`..` 与设备路径。
- 文件只能落在某个加载器供给表声明的目录，或加载器自己 `install.payload` 的 `target` 里：一个类型可以有几个供给者，用已装上的那个，`subpath` 不得越出游戏目录。
- “翻译”分类是 `patch` 包：整体接管 `xunity:translation` 的供给目录，安装前把整个目录归档（保留最新 5 份）后清空，卸载时整目录还原。
- 原生或无法静态归类的 DLL 必须由安装规则显式给出类型。
- 同一路径的不同内容、外部修改的托管文件和不同哈希的手工文件会阻止安装；补丁模式按它的替换语义直接覆盖，被替换的原件先归档到 `SprocketModManager/backup/patched`。
- Sprocket 运行时拒绝修改游戏目录。
- 加载器与模组走同一条安装管线：同样的下载主机限制、发布者摘要校验、ZIP 限制、事务安装与失败回滚。
- README 只能从该模组登记的 GitHub 仓库读取；显示前会移除脚本、表单、嵌入内容、不安全 URL
  和非 GitHub 图片资源。
- 安装状态按游戏目录隔离；卸载不会删除已被用户修改的文件。普通安装前已存在的文件仍受保护；
  通过 Release 哈希自动接管的文件会成为受管文件，并且仅在内容未变化时允许卸载删除。

模组管理器自身的更新：启动时查一次 GitHub Release（tag `v<版本>`），找当前平台的资产
（`SprocketModManager.exe`、`SprocketModManager-linux-x64`）。有新版本就弹窗给两条路 ——
**立即更新**把新构建下载到同目录、核对 GitHub 给出的资产 SHA-256，再交给一个换壳子进程替换并启动
新构建；Windows 下运行中的 EXE 不能覆盖自己，子进程才需要等旧进程退出，Linux 上直接换掉正在运行的
文件（旧进程继续用自己的 inode）。**暂缓**则这次会话继续用，下次启动重新问一次。源码运行或没打包成
单文件时不给自更新，只把人带到发布页。

这条链路的信任到「GitHub 的 HTTPS + GitHub 自己算的资产摘要」为止。要防到「发布账号被拿走」这一层，
还需要固定公钥验证的更新清单或可验证的 Windows 代码签名。

## Code signing policy

Windows 发布产物采用 SignPath Foundation 的免费开源代码签名（申请中）：构建由 SignPath.io
签名，证书由 SignPath Foundation 持有。

Free code signing provided by SignPath.io, certificate by SignPath Foundation

- 提交者与审查者（Committers and reviewers）：[@furryaxw](https://github.com/furryaxw)
- 批准者（Approvers）：[@furryaxw](https://github.com/furryaxw)
- 隐私政策：见[隐私政策](#隐私政策)。

## 隐私政策

本程序不含遥测与使用统计，不收集用户数据。发送数据的场景只有一处：用户在侧栏「上传日志」
中主动选定日志后，日志原文发往 `https://paste.furryaxw.top/api/q/`（无后台自动上传，见
[日志上传](docs/log-upload-design.zh.md)）。

其余网络访问只读取数据——自身 Release 与更新、Registry 索引与模组 Release 元数据、模组 README——
请求发往 GitHub 或本项目 Pages 站点，受
[GitHub 隐私声明](https://docs.github.com/site-policy/privacy-policies/github-privacy-statement)
约束。GUI 由 Microsoft Edge WebView2 渲染，该组件自身可能按
[Microsoft 隐私声明](https://privacy.microsoft.com/privacystatement)访问网络。

## Registry

元数据规范见 [sprocket-mod-spec.zh.md](https://github.com/furryaxw/SprocketModManager/blob/registry/sprocket-mod-spec.zh.md)，作者提交流程见
[CONTRIBUTING.zh.md](https://github.com/furryaxw/SprocketModManager/blob/registry/CONTRIBUTING.zh.md)。`site/` 是无需构建框架的 GitHub Pages 页面；
`.github/workflows/pages.yml` 会在提交后及每小时生成带 Release 快照的索引并部署它。

## License

本项目采用 GNU Affero General Public License v3.0（AGPL-3.0），详见 [LICENSE](LICENSE)。
